# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#747 — SEAM: the letter's two vault renderings and a derived span.

R-3 (founder, 2026-11-04, Hazelnut collectors WP3): on the v2 pipeline (the default)
the two renderings carry NO derived span; the TOOL DURATIONS fact is the letter's
one duration channel. v1 keeps the #747 hedge below.

`services/cover_letter.py` renders the vault twice for the letter chain: as the
writer's `cv_data` on the no-CV branch, and inside `grounding_source`, which the
letter REVIEWER and CORRECTOR read every round. On the 2026-09-19 blind Kaile
probe the corrector wrote "Über acht Jahre … SAP CO" from `years_experience: 8`
(a computed span of 7.6 years) and the reviewer's round-5 feedback turned it into
"Seit acht Jahren" — the delivered sentence. Ruling T-1 (B): a derived span is
offered only hedged and rounded down. Both call sites pass
`derived_spans="hedge"` (ADR-078 amended 2026-10-07); this drives the real
background task once and asserts on what each of them received.

Harness mirrors `test_593_prompt_facing_profile_view.py`'s letter seam.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _start_years_ago(years: float) -> str:
    today = date.today()
    months = int(round(years * 12))
    y, m = divmod(today.year * 12 + (today.month - 1) - months, 12)
    return f"{y:04d}-{m + 1:02d}"


def _profile_json() -> dict:
    return {
        "personal_info": {"name": "Katrin Beispiel", "email": "kb@example.com"},
        "work_experience": [{
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Financial Controller",
            "start_date": _start_years_ago(7.6),
            "is_current": True,
            "responsibilities": ["Key-Userin SAP CO/FI"],
            "technologies": ["SAP CO", "SAP FI"],
        }],
        "skills": [
            {"name": "SAP CO", "category": "technical", "years_experience": 8,
             "source": "computed", "status": "confirmed"},
            {"name": "Excel", "category": "technical", "years_experience": 6,
             "source": "llm_estimated", "status": "confirmed"},
            {"name": "HGB", "category": "domain", "years_experience": 9,
             "source": "transcribed", "status": "confirmed"},
        ],
    }


@pytest_asyncio.fixture
async def letter_db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def letter_seeded(letter_db):
    from tests.support.profile_factory import make_master_profile
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    letter_db.add(User(id=uuid.uuid4(), email="t-747@test.com"))
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash="t747hash",
        raw_text="Senior Controller (m/w/d) bei Arnold Antriebe",
        role_title="Senior Controller", company_name="Arnold Antriebe",
        required_skills=[], nice_to_have_skills=[], keywords=[],
        seniority_level="senior", company_culture_signals=[], language_requirement="de",
    )
    letter_db.add(job)
    profile = make_master_profile(profile_json=_profile_json())
    letter_db.add(profile)
    await letter_db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
    )
    letter_db.add(cl)
    await letter_db.commit()
    await letter_db.refresh(cl)
    return letter_db, job, cl


def _skills_by_name(profile_block: dict) -> dict:
    return {s["name"]: s for s in (profile_block.get("skills") or [])}


async def _run(db, job, cl) -> dict:
    """Drive the real background task once; capture the review loop's sources and
    the writer prompt's keyword arguments."""
    captured: dict = {}

    async def fake_loop(**kw):
        captured.setdefault("sources", []).append(kw.get("source"))
        return kw["draft"]

    def fake_writer_prompt(**kw):
        captured["cv_data"] = kw.get("cv_data")
        captured["writer_kwargs"] = kw
        return "PROMPT"

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "x", "Mit freundlichen Grüßen"]}})

    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.build_cover_letter_prompt",
               side_effect=fake_writer_prompt), \
         patch("applire.services.cover_letter.review_and_refine",
               new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return captured


@pytest.mark.asyncio
async def test_SEAM_v2_letter_renderings_carry_no_derived_span_and_one_TOOL_DURATIONS_fact(
    letter_seeded, monkeypatch
):
    """R-3 (founder, 2026-11-04): on v2 (the default) the letter's ONLY duration channel
    is the TOOL DURATIONS block — in the writer prompt AND the review loop's source.
    Both vault renderings drop a derived span (``derived_spans="drop"``); the 11-04
    "over 7 years of MuleSoft" entered through the review loop's hedge view."""
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    db, job, cl = letter_seeded
    captured = await _run(db, job, cl)

    sources = [s for s in captured.get("sources", []) if s]
    assert sources, "the letter review loop was never called with a grounding source"
    for src in sources:
        obj, _ = json.JSONDecoder().raw_decode(src)
        skills = _skills_by_name(obj.get("profile") or {})
        assert "years_experience" not in skills["SAP CO"], skills["SAP CO"]
        assert "years_experience_derived" not in skills["SAP CO"], skills["SAP CO"]
        assert "years_experience" not in skills["Excel"]
        assert skills["HGB"]["years_experience"] == 9  # transcribed: the candidate's own
        assert "TOOL DURATIONS" in src
        assert '"über 7 Jahre"' in src  # no CV → the R-3 figure stands, hedged

    cv_skills = _skills_by_name(captured.get("cv_data") or {})
    assert "years_experience" not in cv_skills["SAP CO"], cv_skills["SAP CO"]
    assert "years_experience_derived" not in cv_skills["SAP CO"]
    block = captured["writer_kwargs"].get("tool_durations_block") or ""
    assert block.startswith("TOOL DURATIONS"), block
    assert "SAP CO — since" in block and '"über 7 Jahre"' in block


@pytest.mark.asyncio
async def test_SEAM_v1_letter_renderings_keep_the_747_hedge(letter_seeded, monkeypatch):
    """v1 (the opt-out) is unchanged: #747's hedge view, no TOOL DURATIONS block."""
    monkeypatch.setenv("LETTER_PIPELINE", "v1")
    db, job, cl = letter_seeded
    captured = await _run(db, job, cl)
    sources = [s for s in captured.get("sources", []) if s]
    assert sources
    for src in sources:
        obj, _ = json.JSONDecoder().raw_decode(src)
        skills = _skills_by_name(obj.get("profile") or {})
        assert skills["SAP CO"]["years_experience_derived"]["at_least"] == 7
        assert "TOOL DURATIONS" not in src
    assert not captured["writer_kwargs"].get("tool_durations_block")


@pytest.mark.asyncio
async def test_SEAM_R2_letter_grounding_source_carries_the_cv_form_of_a_translated_title(
        letter_seeded, monkeypatch):
    """Ruling R-2 (WP3): with a CV that translated the single-form title, the letter's
    RECORDED JOB TITLES block in the grounding source names the CV's form for the same
    position — else the letter reviewer blocks the CV's own title "character for
    character" (cover_letter._render_cover_letter_body → with_cv_role_forms)."""
    from applire.models.cv import GeneratedCV

    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    db, job, cl = letter_seeded
    cv = GeneratedCV(
        job_analysis_id=job.id, profile_id=cl.profile_id,
        tailored_data={"work_history": [{
            "id": "w-schwarzwald", "company": "Schwarzwald Präzision GmbH",
            "role": "Finanzcontrollerin", "start_date": "2018-01", "bullets": ["SAP CO/FI key user."],
        }]},
    )
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    captured: dict = {}

    async def fake_loop(**kw):
        captured.setdefault("sources", []).append(kw.get("source"))
        return kw["draft"]

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "x", "Mit freundlichen Grüßen"]}})
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.build_cover_letter_prompt", return_value="PROMPT"), \
         patch("applire.services.cover_letter.review_and_refine",
               new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        from applire.services.cover_letter import _render_cover_letter_background
        await _render_cover_letter_background(cl_id=cl.id, cv_id=cv.id, job_id=job.id)
    sources = [s for s in captured.get("sources", []) if s]
    assert sources
    for src in sources:
        assert 'the CV states this same title in its language as "Finanzcontrollerin"' in src
