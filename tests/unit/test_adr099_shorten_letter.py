# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 7 (WP-B, defect B-d1): the letter page card's "kürzen" runs for
real — real DB rows, the real ``delivery_actions.shorten_letter`` (its lazy
imports included: the first build imported ``REGION_NORMS`` from a module that
does not have it, a 500 on every click that no stubbed test reached), the real
re-audit. Doubled: the model call and the PDF render/read-back only.

Also ADR-077 cl. 2/3: the shortening is told the letter's pinned quotes, and a
result that drops one is refused (nothing written)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

PIN = "Budgetverantwortung ca. 6 Mio. € für Personal und Instandhaltung getragen."
LONG = [
    "Sehr geehrte Damen und Herren,",
    "mit großem Interesse habe ich Ihre Ausschreibung gelesen und bewerbe mich als Produktionsleiterin. " * 3,
    PIN + " Außerdem habe ich Rüstworkshops nach SMED eingeführt und über zwei Jahre begleitet.",
    "Ich freue mich auf ein persönliches Gespräch.",
]


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, pinned: bool):
    from applire.models.application import Application
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    from tests.support.owners import HARNESS_USER_ID

    # the harness owner context (ADR-092 cl. 8) is the acting user
    user = User(id=HARNESS_USER_ID, email="shorten@example.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text="Produktionsleitung (m/w/d)",
        role_title="Produktionsleitung", company_name="Muster AG", required_skills=[],
        nice_to_have_skills=[], keywords=[], seniority_level="senior", company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = make_master_profile(user_id=user.id, profile_json={
        "personal_info": {"name": "Anna Beispiel", "email": "anna@example.com"},
        "work_experience": [{
            "id": "c0ffee00-0000-4000-8000-000000000001", "company": "Muster Werk GmbH",
            "role": "Produktionsleiterin", "start_date": "2017-01", "end_date": None, "is_current": True,
            "responsibilities": [PIN, "Rüstworkshops nach SMED eingeführt und begleitet."],
        }],
        "skills": [{"name": "SMED"}],
    })
    db.add(profile)
    await db.flush()
    if pinned:
        db.add(Application(
            user_id=user.id, job_analysis_id=job.id,
            pinned_facts=[{"entry_type": "work", "entry_id": "c0ffee00-0000-4000-8000-000000000001",
                           "quote": PIN, "targets": ["letter"]}],
        ))
    cl = GeneratedCoverLetter(
        user_id=user.id, job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={"header": {"name": "Anna Beispiel"}, "recipient": {"company": "Muster AG"},
                     "body": {"paragraphs": list(LONG)}, "signature": {"closing": "Mit freundlichen Grüßen"}},
        pre_gen_inputs={}, status=CoverLetterStatus.ready.value,
        ats_report={"checks": [{"id": "page-length", "status": "fail", "details": "2 pages",
                                "details_key": "page-length-letter",
                                "details_params": {"pages": 2, "region": "DACH", "letterPages": 1}}],
                    "passed": 0, "failed": 1},
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return user, cl


def _provider(paragraphs):
    p = MagicMock()
    p.aparse_json = AsyncMock(return_value={"body": {"paragraphs": paragraphs}})
    return p


def _render_one_page():
    """The PDF render/read-back doubled (one page now); the re-audit's own model
    calls (Oracle judgement, critic) fail closed instead of reaching a network."""
    down = MagicMock()
    down.aparse_json = AsyncMock(side_effect=RuntimeError("no provider in a unit test"))
    down.acomplete = AsyncMock(side_effect=RuntimeError("no provider in a unit test"))
    return (
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(return_value=b"%PDF-1.4 fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)),
        patch("applire.services.cover_letter.get_provider", return_value=down),
    )


@pytest.mark.asyncio
async def test_kuerzen_runs_for_real_and_the_card_leaves(db):
    from applire.services.delivery_actions import shorten_letter

    user, cl = await _seed(db, pinned=True)
    short = [LONG[0], "Ich bewerbe mich als Produktionsleiterin.", PIN, LONG[3]]
    provider = _provider(short)
    r1, r2, r3 = _render_one_page()
    with r1, r2, r3:
        res = await shorten_letter("cover_letter", cl.id, db, provider, user_id=user.id)
    assert provider.aparse_json.await_count == 1
    prompt = provider.aparse_json.await_args.args[0]
    # ADR-077 cl. 3: the pinned-quote instruction line quotes the pin in "…"
    assert f'"{PIN}"' in prompt, "the condense prompt carries the pinned quotes"
    assert res.still_listed is False
    await db.refresh(cl)
    page = [c for c in cl.ats_report["checks"] if c.get("id") == "page-length"]
    assert page and page[0]["status"] == "pass"


@pytest.mark.asyncio
async def test_a_shortening_that_drops_the_pinned_sentence_is_refused(db):
    from applire.services.delivery_actions import RewriteFailed, shorten_letter

    user, cl = await _seed(db, pinned=True)
    before = cl.letter_data
    provider = _provider([LONG[0], "Ich bewerbe mich als Produktionsleiterin.", LONG[3]])
    r1, r2, r3 = _render_one_page()
    with r1, r2, r3, pytest.raises(RewriteFailed):
        await shorten_letter("cover_letter", cl.id, db, provider, user_id=user.id)
    await db.refresh(cl)
    assert cl.letter_data == before and not (cl.section_overrides or {})
