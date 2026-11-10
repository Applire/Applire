# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADV-1 (Hazelnut build 8, adversarial pass; MD8-9) — the letter's blocking
``letter.duration`` check (784:128) reads the posting and the candidate's inputs
for this application as sources of a duration.

The fact and its negatives live in ``test_b8_l_784_128_durations.py``. This file is
the seam layer: one named test per call site that hands the sources to the check —
the drafting loop's checks, the delivered letter's code note, and the terminal
review's re-entry after a post-verdict write. Each keeps a control sentence (a
duration no source carries) that must still be found on the same path.
"""
import copy
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cover_letter import _letter_duration_sources  # noqa: E402

_POSTING = (
    "Senior Controller (m/f/d) at Arnold Antriebstechnik GmbH. Arnold Antriebstechnik has "
    "been a family-owned manufacturer of drive systems for over 30 years. You bring at "
    "least 2 years of experience in production controlling."
)
_EMPLOYER_AGE = "Your company has been a family-owned manufacturer of drive systems for over 30 years."
_UNBACKED = "I bring twelve years of controlling experience."

_PROFILE_JSON = {
    "personal_info": {"name": "Katrin Hoffmann", "email": "katrin.hoffmann@example.com",
                      "location": "Stuttgart"},
    "work_experience": [{
        "id": "11111111-1111-4111-8111-111111111111",
        "company": "Schwarzwald Präzision GmbH",
        "role": "Financial Controller",
        "start_date": "2019-01",
        "end_date": "2023-06",
        "achievements": ["Monats- und Jahresabschlüsse nach HGB."],
    }],
    "skills": [],
    "metadata": {"denied_concepts": []},
}


def _draft(*extra):
    return {
        "header": {"name": "Katrin Hoffmann"},
        "recipient": {"name": None, "title": None, "company": "Arnold Antriebstechnik GmbH",
                      "address": None, "date": None},
        "body": {"paragraphs": [
            "Dear Sir or Madam,",
            "I am applying for the Senior Controller role at Arnold Antriebstechnik GmbH. "
            + " ".join(extra),
            "At Schwarzwald Präzision GmbH I owned the monthly and annual closing under HGB.",
            "I would welcome the opportunity to discuss my experience with you in an interview "
            "and look forward to hearing from you.",
        ]},
        "signature": {"closing": "Kind regards", "name": "Katrin Hoffmann"},
    }


# ── the source text ──────────────────────────────────────────────────────────

def test_the_duration_sources_are_the_posting_and_the_candidates_inputs():
    text = _letter_duration_sources({"motivation": "After 4 years in audit I want to build.",
                                     "salary": {"amount": 70000}, "availability": ""}, _POSTING)
    assert "over 30 years" in text and "After 4 years in audit" in text and "70000" in text
    assert _letter_duration_sources(None, None) == ""


# ── seams: the real render path ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _v2(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "off")
    monkeypatch.setenv("LETTER_PIPELINE", "v2")


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


async def _run(db, writer, *, mutate_after_verdict=False):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User
    from applire.prompts.review_cover_letter import (
        TERMINAL_REVIEW_SYSTEM_PROMPT,
        V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
    )
    from applire.services.cover_letter import _render_cover_letter_background

    user = User(id=uuid.uuid4(), email=f"adv1-{uuid.uuid4().hex[:8]}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text=_POSTING,
        role_title="Senior Controller", company_name="Arnold Antriebstechnik GmbH",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="en", jd_language="en",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    db.add_all([user, job, profile])
    await db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
        document_language="en",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    record = {"loops": [], "audits": []}

    async def _aparse(prompt, system=None, **kw):
        if system in (TERMINAL_REVIEW_SYSTEM_PROMPT, V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
            return {"approved": True, "issues": []}
        return copy.deepcopy(writer)

    async def _fake_review(**kw):
        chk = kw.get("checks_fn")
        record["loops"].append({"chain_id": kw.get("chain_id"),
                                "checks": list(chk(kw["draft"])) if chk else []})
        kw["reviewer_prompt_fn"](kw["source"], kw["draft"])
        return kw["draft"]

    async def _audit(cl_, db_, **kw):
        record["audits"].append(kw)
        if mutate_after_verdict and len(record["audits"]) == 1:
            # a write after the terminal verdict: the subject-identity gate re-enters
            data = copy.deepcopy(cl_.letter_data)
            data["body"]["paragraphs"][2] += " I also introduced a rolling forecast."
            cl_.letter_data = data

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=_fake_review),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              new=MagicMock(side_effect=lambda _pdf: ("text", 1))),
        patch("applire.services.cover_letter._update_ats_report_letter", side_effect=_audit),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return record


def _durations(checks):
    """Which of the two sentences' durations the loop's blocking check names."""
    found = [c for c in checks if c.check == "letter.duration"]
    assert all(c.blocking for c in found)
    return [w for c in found for w in ("twelve years", "30 years") if w in c.text]


def _duration_notes(audit):
    outcome = audit["terminal_review"]
    return [n.subjects for n in (outcome.notes if outcome is not None else ())
            if n.key == "letter.duration"]


@pytest.mark.asyncio
async def test_seam_drafting_loop_checks_read_the_posting(db):
    rec = await _run(db, _draft(_EMPLOYER_AGE, _UNBACKED))
    drafting = next(e for e in rec["loops"] if e["chain_id"] == "cover_letter")
    assert _durations(drafting["checks"]) == ["twelve years"]


@pytest.mark.asyncio
async def test_seam_delivered_letter_note_reads_the_posting(db):
    rec = await _run(db, _draft(_EMPLOYER_AGE, _UNBACKED))
    assert _duration_notes(rec["audits"][-1]) == [("twelve years",)]


@pytest.mark.asyncio
async def test_seam_a_letter_quoting_only_the_posting_ships_without_a_duration_note(db):
    rec = await _run(db, _draft(_EMPLOYER_AGE))
    assert all(_durations(e["checks"]) == [] for e in rec["loops"])
    assert _duration_notes(rec["audits"][-1]) == []


@pytest.mark.asyncio
async def test_seam_terminal_reentry_note_reads_the_posting(db):
    rec = await _run(db, _draft(_EMPLOYER_AGE, _UNBACKED), mutate_after_verdict=True)
    assert len(rec["audits"]) == 2, "the post-verdict write re-entered the terminal review"
    assert _duration_notes(rec["audits"][-1]) == [("twelve years",)]
