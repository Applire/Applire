# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING W-2 (founder 2026-11-10, Hazelnut build 8; amends R-3 for the letter):
the person's own career figure is the ceiling. A tool whose RAW computed span lies
above the candidate's own career figure (``prompt_view.span_above_career``) gets NO duration
in the letter (ux-audit-4 Felix: "over 9 years PostgreSQL" beside his own "8 years
of experience"); a hedge below it stays; no own figure = no ceiling. The candidate's
own per-tool figure (R4-1) is never capped. Both readers of
``ToolDuration.allowed_wordings`` follow: the TOOL DURATIONS line and the critic's
numeric finding. Synthetic profiles only.
"""
from __future__ import annotations

from applire.services.cover_letter import _letter_critic_fn
from applire.services.letter_v2 import render_tool_durations_block, tool_duration_facts


def _w(wid, company, start, end, tech):
    return {"id": wid, "company": company, "role": "Engineer", "start_date": start,
            "end_date": end, "technologies": list(tech)}


WORK = [
    _w("w1", "Alpha GmbH", "2015-01", "2017-06", ["PostgreSQL", "Python"]),
    _w("w2", "Beta AG", "2017-07", "2021-02", ["PostgreSQL"]),
    _w("w3", "Gamma GmbH", "2021-03", "2025-12", ["PostgreSQL", "Django"]),
]


def _profile(summary="Engineer with 8 years of experience in logistics software."):
    return {
        "personal_info": {"name": "Felix Beispiel", "email": "fb@example.com"},
        "professional_summary": {"en": summary, "de": None},
        "work_experience": WORK,
        "skills": [{"name": s, "category": "technical"} for s in ("PostgreSQL", "Django", "Python")],
    }


def _fact(facts, tool):
    return next(f for f in facts if f.tool == tool)


def _line(block, tool):
    return next(ln for ln in block.splitlines() if ln.startswith(f"  - {tool} —"))


def test_a_hedge_over_the_ceiling_gets_no_duration():
    facts = tool_duration_facts(_profile(), None, ceiling_years=8)
    pg = _fact(facts, "PostgreSQL")
    assert pg.years >= 10 and pg.over_ceiling
    assert pg.allowed_wordings("en") == [] and pg.allowed_wordings("de") == []
    line = _line(render_tool_durations_block(facts, "en"), "PostgreSQL")
    assert "longer than the 8 years the candidate states" in line and "state NO duration" in line


def test_a_hedge_below_the_ceiling_stays():
    django = _fact(tool_duration_facts(_profile(), None, ceiling_years=8), "Django")
    assert not django.over_ceiling
    assert django.allowed_wordings("en") == ["over 4 years", "almost 5 years"]


def test_the_raw_span_is_compared_not_the_hedge_floor():
    """Main's ruling on W-2: 8.6 computed years beside the candidate's own 8 is above
    it — no duration, not even "über 8 Jahre"; exactly 8.0 is not above it."""
    from applire.services.letter_v2 import ToolDuration

    def t(years):
        return ToolDuration(tool="X", years=years, since="01/2017", positions=(("a", "A"),),
                            cv_positions=("a",), has_cv=True, ceiling_years=8)

    assert t(8.6).allowed_wordings("de") == [] and t(8.6).over_ceiling
    assert t(8.0).allowed_wordings("de") == ["über 8 Jahre"]


def test_no_ceiling_leaves_r3_unchanged():
    pg = _fact(tool_duration_facts(_profile(), None), "PostgreSQL")
    assert pg.ceiling_years is None and pg.allowed_wordings("en")[0] == f"over {int(pg.years)} years"


def test_the_own_tool_figure_is_never_capped():
    facts = tool_duration_facts(_profile("Engineer with 11 years of PostgreSQL operations."), None,
                                ceiling_years=8)
    pg = _fact(facts, "PostgreSQL")
    assert pg.allowed_wordings("en") == ["11 years"] and not pg.over_ceiling


def test_every_fact_carries_the_ceiling_even_a_figure_without_a_span():
    """A tool the candidate states but no dated position counts still carries it."""
    p = _profile("Engineer with 8 years of experience; 3 years of Kafka on the side.")
    p["skills"].append({"name": "Kafka", "category": "technical"})
    facts = tool_duration_facts(p, None, ceiling_years=8)
    kafka = _fact(facts, "Kafka")
    assert kafka.years == 0.0 and kafka.stated_years == 3
    assert {f.ceiling_years for f in facts} == {8}


class _Adv:
    def __init__(self, concept, state):
        self.concept, self.letter_state = concept, state


def test_the_critic_finding_follows_the_ceiling():
    facts = tool_duration_facts(_profile(), None, ceiling_years=8)
    adv = _Adv("Years of PostgreSQL experience", "I have run PostgreSQL for over 10 years")
    draft = {"body": {"paragraphs": [f"Intro. {adv.letter_state}. Close."]}}
    (finding,) = _letter_critic_fn([adv], facts, "en")(draft)
    assert finding.text.endswith("For PostgreSQL state no duration.")


# ── 790:61 (Lead O hunk): an unconfirmed skill's transcribed duration is no own figure ──

def test_an_unconfirmed_transcribed_duration_is_not_a_stated_tenure():
    p = _profile()
    p["skills"].append({"name": "Kafka", "category": "technical", "source": "transcribed",
                        "years_experience": 12, "status": "unconfirmed"})
    assert not [f for f in tool_duration_facts(p, None) if f.tool == "Kafka"]
    p["skills"][-1]["status"] = "confirmed"
    assert _fact(tool_duration_facts(p, None), "Kafka").stated_years == 12


# ── seam: the letter writer's TOOL DURATIONS block on the real render path ────

import copy  # noqa: E402
import uuid  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from tests.support.profile_factory import make_master_profile  # noqa: E402


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _writer_prompt(db, summary):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User
    from applire.services.cover_letter import _render_cover_letter_background

    user = User(id=uuid.uuid4(), email="w2-seam@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash="b8lw2", raw_text="Backend Engineer at NovaPay",
        role_title="Backend Engineer", company_name="NovaPay GmbH", required_skills=[],
        nice_to_have_skills=[], keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="en", jd_language="en",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(_profile(summary)))
    db.add_all([user, job, profile])
    await db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german", letter_data={},
        pre_gen_inputs={}, status=CoverLetterStatus.pending.value, document_language="en",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    prompts = []
    body = ["Dear Sir or Madam,", "I am applying for the Backend Engineer role at NovaPay GmbH.",
            "At Gamma GmbH I run PostgreSQL services.", "I look forward to hearing from you."]

    async def _aparse(prompt, system=None, **kw):
        prompts.append(prompt)
        return {"recipient": {"company": "NovaPay GmbH"}, "body": {"paragraphs": list(body)},
                "signature": {"closing": "Kind regards", "name": "Felix Beispiel"}}

    async def _fake_review(**kw):
        return kw["draft"]

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=_fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              new=MagicMock(side_effect=lambda _pdf: ("text", 1))),
        patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return next(p for p in prompts if "TOOL DURATIONS" in p)


@pytest.mark.asyncio
async def test_seam_v2_writer_block_carries_the_own_career_ceiling(db, monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    monkeypatch.setenv("PROMPT_CONTEXT", "off")
    prompt = await _writer_prompt(db, "Engineer with 8 years of experience in logistics software.")
    pg = _line(prompt, "PostgreSQL")
    assert "longer than the 8 years the candidate states" in pg and "state NO duration" in pg
    assert '"over 4 years"' in _line(prompt, "Django")


@pytest.mark.asyncio
async def test_seam_v2_writer_block_without_an_own_career_figure_is_r3(db, monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    monkeypatch.setenv("PROMPT_CONTEXT", "off")
    prompt = await _writer_prompt(db, "Engineer for logistics software.")
    assert "May be stated as \"over 10 years\"" in _line(prompt, "PostgreSQL")
