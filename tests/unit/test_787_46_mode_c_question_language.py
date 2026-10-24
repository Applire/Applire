# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""787:46 (Hazelnut build 4, founder ruling E-12 2026-10-22) — the Mode C
(profile enrichment) question language: ``ui_language`` when the user chose
one, else the language of the profile's source documents; 'en' only when the
profile carries no source text.

Delivery run 2026-09-13: a German candidate with ``ui_language`` never set got
an English Mode C question — the door read ``get_ui_language`` (NULL -> 'en').

Seam tests drive the REAL start and respond doors (only the gap detector and
the question writer are doubled; the writer records the ``lang`` it was given).

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_787_46_mode_c_question_language.py -q
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile  # noqa: E402

_GERMAN = {
    "personal_info": {"name": "Marcus Test", "email": "marcus@example.de"},
    "skills": [{"name": "Lean Management", "category": "domain"}],
    "work_experience": [{
        "company": "Weberit", "role": "Produktionsleiter", "start_date": "2019-01",
        "responsibilities": ["Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden "
                             "im Dreischichtbetrieb und für die Feinplanung"],
        "achievements": ["Die Termintreue wurde von 87 % auf 96 % verbessert"],
    }],
    # extraction writes the summary in BOTH languages — it must not decide
    "professional_summary": {"en": "Production manager with a track record in lean and the "
                                   "kind of results that the team has delivered for years.",
                             "de": None},
}
_ENGLISH = {
    "personal_info": {"name": "Felix Test", "email": "felix@example.com"},
    "work_experience": [{
        "company": "Cargonaut", "role": "Senior Backend Engineer", "start_date": "2021-03",
        "responsibilities": ["Owned the deployment and the operations of four backend services "
                             "that serve the logistics users"],
    }],
}
_EMPTY = {"personal_info": {"name": "New User", "email": "new@example.com"}}


@pytest_asyncio.fixture
async def db():
    import applire.main  # noqa: F401 — every model on Base.metadata
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def langs(monkeypatch):
    import applire.routers.profile_enrich as pe

    seen: list[str] = []
    monkeypatch.setattr(pe, "gap_detector_mode_c", lambda profile_data, scope=None: [
        "budget_managed: Produktionsleiter @ Weberit", "team_size: Produktionsleiter @ Weberit",
    ])

    async def _q(state, profile_data, provider, lang="en"):
        seen.append(lang)
        return {"question": f"Q{len(seen)}"}

    monkeypatch.setattr(pe, "question_generator_with_profile", _q)
    return seen


def _provider():
    p = MagicMock()
    p.acomplete = AsyncMock(return_value="unused")
    p.aparse_json = AsyncMock(return_value={})
    return p


async def _seed(db, profile_json, ui_language=None):
    from applire.models.user_settings import UserSettings

    prof = make_master_profile(profile_json=profile_json)
    db.add(prof)
    if ui_language is not None:
        db.add(UserSettings(user_id=prof.user_id, ui_language=ui_language))
    await db.commit()
    return prof


async def _start(db):
    from applire.routers.profile_enrich import start_enrich_session
    from applire.schemas.enrich import EnrichStartRequest

    return await start_enrich_session(EnrichStartRequest(), db, _provider(), None)


@pytest.mark.asyncio
async def test_no_ui_language_and_a_german_profile_asks_in_german(db, langs):
    await _seed(db, _GERMAN)
    await _start(db)
    assert langs == ["de"]


@pytest.mark.asyncio
async def test_no_ui_language_and_an_english_profile_asks_in_english(db, langs):
    await _seed(db, _ENGLISH)
    await _start(db)
    assert langs == ["en"]


@pytest.mark.asyncio
async def test_an_explicit_ui_language_wins_over_the_profile(db, langs):
    await _seed(db, _GERMAN, ui_language="en")
    await _start(db)
    assert langs == ["en"]


@pytest.mark.asyncio
async def test_the_respond_door_keeps_the_profile_language_for_the_next_question(db, langs):
    from applire.routers.profile_enrich import respond_to_enrich
    from applire.schemas.enrich import EnrichRespondRequest

    await _seed(db, _GERMAN)
    started = await _start(db)
    await respond_to_enrich(
        started.session_id, EnrichRespondRequest(answer="Rund 6 Mio. Euro Budget."),
        db, _provider(), None,
    )
    assert langs == ["de", "de"]


@pytest.mark.asyncio
async def test_a_profile_without_source_text_falls_back_to_english(db):
    from applire.services.session import get_profile_conversation_language

    prof = await _seed(db, _EMPTY)
    assert await get_profile_conversation_language(db, _EMPTY, user_id=prof.user_id) == "en"


def test_the_bilingual_summary_never_decides():
    from applire.services.session import _profile_source_text

    text = _profile_source_text(_GERMAN)
    assert "Production manager" not in text
    assert "Fertigungsbereiche" in text
