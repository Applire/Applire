# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ruling D-1 (2026-09-14, ADR-059 precedent) — one seam test through the REAL
PATCH path.

`tests/unit/test_cv_assist_grounding.py` pins the mechanism against a fake DB;
this file drives the actual FastAPI router (`applire.routers.cv`) over a real
in-memory sqlite session, so the seam from HTTP body -> `submit_assist_answer`
-> `_ground_suggestion` -> a real `MasterProfile` row is exercised end to end,
not just the helper function in isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.db.session import Base, get_db
from tests.support.profile_factory import make_master_profile

_CV_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")
_JOB_ID = uuid.UUID("77777777-7777-7777-7777-777777777778")
_PROFILE_ID = uuid.UUID("77777777-7777-7777-7777-777777777779")

_DENIAL = {
    "concept": "Investitionsentscheidungen selbst treffen",
    "statement": (
        "Bei der Investitionsverantwortung muss ich differenzieren: das operative "
        "Budget habe ich verantwortet, aber Investitionsentscheidungen selbst lagen "
        "bei der Geschäftsführung, ich habe sie vorbereitet, nicht getroffen."
    ),
    "source": "interview",
    "date": "2026-09-13",
    "denial_level": "direct",
    "probe_asked": False,
}

_PROFILE_JSON = {
    "personal_info": {"full_name": "Stefan Brandt"},
    "professional_summary": {"de": "Produktionsleiter."},
    "work_experience": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "responsibilities": ["Führung von zwei Fertigungsbereichen."],
        }
    ],
    "skills": [],
    "metadata": {"denied_concepts": [_DENIAL]},
}

_CONTENT_SNAPSHOT = {
    "introduction": "Erfahrener Produktionsleiter.",
    "positions": [],
    "skills": [],
}


@pytest_asyncio.fixture
async def db():
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.user  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        id=_JOB_ID,
        raw_text_hash="ruling-d1-test",
        raw_text="Produktionsleiter gesucht.",
        role_title="Produktionsleiter",
        seniority_level="mid",
        language_requirement="de",
    )
    profile = make_master_profile(
        id=_PROFILE_ID,
        profile_json=_PROFILE_JSON,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        id=_CV_ID,
        job_analysis_id=_JOB_ID,
        profile_id=_PROFILE_ID,
        tailored_data={},
        template="classic_german",
        status="ready",
        content_snapshot=_CONTENT_SNAPSHOT,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([job, profile, cv])
    await db.commit()
    return db


class _FakeProvider:
    """Returns a canned suggestion reproducing the delivery-run's own wording,
    so the test controls exactly what the "model" said without a real call."""

    def __init__(self, text: str):
        self._text = text

    async def acomplete(self, *_a, **_kw) -> str:
        return self._text


def _client(db, provider) -> TestClient:
    from applire.routers.cv import _get_provider, router

    async def _override_get_db():
        yield db

    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: None
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[_get_provider] = lambda: provider
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.asyncio
async def test_patch_assist_withholds_a_suggestion_for_a_directly_denied_gap_concept(
    seeded,
):
    """The real PATCH path, real DB, real router: a micro-session targeting the
    SAME concept the candidate directly denied gets its suggestion withheld
    wholesale — never reaching the response the frontend would render."""
    from applire.services.cv_assist import _sessions

    session_id = "d1-seam-session"
    _sessions[session_id] = {
        "cv_id": str(_CV_ID),
        "section_id": "introduction",
        "gap_id": "Investitionsentscheidungen selbst treffen",
        "section_label": "Introduction",
        "section_content": _CONTENT_SNAPSHOT["introduction"],
        "question": "Welche Investitionen hast du verantwortet?",
    }
    # Deliberately no figure in this suggestion (unlike the delivery run's own
    # "2 Mio. Euro" wording) — a suggestion with an unmatched NUMBER would
    # already be withheld by the pre-existing numbers check, and this test
    # must isolate the NEW denial floor, not coincidentally pass through the
    # old mechanism. It restates the vault's own attested fact, which the
    # ordinary per-claim grounding would otherwise keep.
    provider = _FakeProvider(
        "Zwei Fertigungsbereiche mit grossem persoenlichem Engagement gefuehrt."
    )
    client = _client(seeded, provider)

    response = client.patch(
        f"/api/cv/{_CV_ID}/sections/introduction/assist",
        json={"session_id": session_id, "answer": "Ich habe das eigenständig entschieden."},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["suggestion"] == ""
    assert data["withheld_count"] >= 1
    _sessions.clear()


@pytest.mark.asyncio
async def test_patch_assist_still_ships_a_true_suggestion_for_an_undenied_gap(seeded):
    """Negative control on the real path: a gap concept with NO recorded denial
    is unaffected by the new floor — the existing grounding behaviour (kept
    text, vault-supported) still ships."""
    from applire.services.cv_assist import _sessions

    session_id = "d1-seam-session-clean"
    _sessions[session_id] = {
        "cv_id": str(_CV_ID),
        "section_id": "introduction",
        "gap_id": "Führung",
        "section_label": "Introduction",
        "section_content": _CONTENT_SNAPSHOT["introduction"],
        "question": "Wie viele Mitarbeitende hast du geführt?",
    }
    provider = _FakeProvider("Zwei Fertigungsbereiche geführt.")
    client = _client(seeded, provider)

    response = client.patch(
        f"/api/cv/{_CV_ID}/sections/introduction/assist",
        json={"session_id": session_id, "answer": "Zwei Bereiche."},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["suggestion"]
    assert data.get("withheld_count", 0) == 0
    _sessions.clear()
