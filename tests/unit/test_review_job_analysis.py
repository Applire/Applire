# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""#264 — JD analysis now runs the ADR-021 author/reviewer loop.

analyze_jd() extracts required/nice-to-have skills, keywords, title and company from
a job posting with no downstream grounding guard — unlike CV/profile extraction, which
already has both a reviewer AND deterministic checks. A fabricated requirement here
poisons every downstream truthfulness surface (keyword ledger, gap analysis, interview,
tailoring). This closes that gap with the standard reviewer idiom.

No Docker, no DB for the prompt-builder tests; SQLite in-memory for the wiring tests.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REFINEMENT_PROMPT,
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT,
    build_job_analysis_retry_prompt,
    build_job_analysis_review_prompt,
)


# ---------------------------------------------------------------------------
# Prompt builder smoke tests
# ---------------------------------------------------------------------------


_JD_TEXT = "Senior Backend Engineer at Acme GmbH. Requires Python and PostgreSQL. AWS is a plus."
_EXTRACTED = {
    "role_title": "Senior Backend Engineer",
    "company_name": "Acme GmbH",
    "required_skills": ["Python", "PostgreSQL"],
    "nice_to_have_skills": ["AWS"],
    "keywords": ["backend"],
    "seniority_level": "Senior",
}


class TestJobAnalysisReviewPromptBuilder:
    def test_system_prompt_is_nonempty_string(self):
        assert isinstance(JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT, str)
        assert len(JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT) > 100

    def test_system_prompt_references_approved_field(self):
        assert "approved" in JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT

    def test_build_prompt_includes_source_and_draft(self):
        result = build_job_analysis_review_prompt(_JD_TEXT, _EXTRACTED)
        assert _JD_TEXT in result
        assert "PostgreSQL" in result

    def test_retry_prompt_includes_feedback_and_source(self):
        result = build_job_analysis_retry_prompt(_EXTRACTED, "drop fabricated Kubernetes", _JD_TEXT)
        assert "drop fabricated Kubernetes" in result
        assert _JD_TEXT in result
        assert "PostgreSQL" in result

    def test_refinement_system_prompt_is_nonempty(self):
        assert isinstance(JOB_ANALYSIS_REFINEMENT_PROMPT, str)
        assert len(JOB_ANALYSIS_REFINEMENT_PROMPT) > 50


# ---------------------------------------------------------------------------
# Wave 6 Task 3 — narrow the two reviewer false-positive classes (wording only)
# ---------------------------------------------------------------------------


class TestJobAnalysisReviewPromptWave6Wording:
    """Pinned failure: 'Connect-AI' and 'Lead AI Engineer' were BOTH present
    verbatim in the source JD text, yet the reviewer called them 'not explicitly
    stated' and the corrector dropped them entirely. The prompt must foreclose that
    specific false-positive class, and must also forbid reversing a change it asked
    for in an earlier round (the seniority_level null/non-null 2-cycle) — stated as
    a rule about the reviewer's OWN output, with no history fed into the prompt
    (ADR-058 freeze: memoryless prompt)."""

    def test_verbatim_source_text_is_grounded_full_stop(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        assert "verbatim" in prompt
        # The rule must connect "verbatim" to the "not explicitly stated" failure
        # mode it forecloses.
        assert "explicitly stated" in prompt

    def test_anti_oscillation_rule_present_and_memoryless(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        # A rule about the reviewer's own output reversing a prior request.
        assert "reverse" in prompt or "reversing" in prompt
        assert "earlier round" in prompt or "previous round" in prompt or "prior round" in prompt
        # The rule must NOT be implemented by feeding the reviewer any history —
        # the prompt stays memoryless. build_job_analysis_review_prompt only ever
        # takes (jd_text, extracted_json); no history/previous-issues parameter.
        import inspect

        sig = inspect.signature(build_job_analysis_review_prompt)
        assert list(sig.parameters) == ["jd_text", "extracted_json"]

    def test_genuine_checks_are_not_weakened(self):
        """The original defect classes must still be flagged after the wording
        change — fabrication, misclassification, invented title/company, and
        seniority/language overreach all remain named."""
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        for marker in (
            "FABRICATED REQUIREMENT",
            "MISCLASSIFICATION",
            "FABRICATED KEYWORDS",
            "INVENTED TITLE OR COMPANY",
            "SENIORITY/LANGUAGE OVERREACH",
        ):
            assert marker in prompt

    def test_hallucinated_field_still_reads_as_flagged_by_wording(self):
        """A field with NO basis anywhere in the source text is not protected by
        the new verbatim-grounding rule (which only shields text that IS present).
        This is a wording-level assertion, not a live-LLM call: it demonstrates the
        prompt's own approval bar language keys off source-text presence, so a
        value absent from the source falls outside the "verbatim ⇒ grounded"
        exemption and stays inside the fabrication-check language."""
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        # The verbatim-grounding carve-out must be phrased conditionally on the
        # value actually appearing in the source — not as a blanket "never flag".
        lowered = prompt.lower()
        verbatim_idx = lowered.index("verbatim")
        # The word "source" must appear near the verbatim rule, tying groundedness
        # to actual presence in the posting text (so absence is still flaggable).
        window = lowered[max(0, verbatim_idx - 300) : verbatim_idx + 300]
        assert "source" in window


# ---------------------------------------------------------------------------
# Wiring: analyze_jd() now routes its draft through review_and_refine
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user          # noqa: F401
    import applire.models.job           # noqa: F401
    import applire.models.profile       # noqa: F401
    import applire.models.gap           # noqa: F401
    import applire.models.cv            # noqa: F401
    import applire.models.session       # noqa: F401
    import applire.models.flow          # noqa: F401
    import applire.models.uploads       # noqa: F401
    import applire.models.application   # noqa: F401
    import applire.models.color_profile # noqa: F401
    import applire.models.company       # noqa: F401
    import applire.models.user_settings # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


_VALID_RESPONSE = {
    "company_name": "Acme GmbH",
    "role_title": "Senior Backend Engineer",
    "required_skills": ["Python", "PostgreSQL"],
    "nice_to_have_skills": ["AWS"],
    "keywords": ["backend"],
    "seniority_level": "Senior",
    "company_culture_signals": [],
    "language_requirement": "German (C1)",
    "berufsbild_code": None,
    "berufsbild_label": None,
}


def _make_provider(*, generation: dict, review_responses: list[dict]):
    """A stub whose FIRST aparse_json call returns `generation` (the analyze_jd
    generation call) and every call after that pops from `review_responses`
    (the review_and_refine reviewer/corrector calls, in order)."""
    provider = AsyncMock()
    calls_seen = {"n": 0}
    remaining = list(review_responses)

    async def _dispatch(*args, **kwargs):
        calls_seen["n"] += 1
        if calls_seen["n"] == 1:
            return generation
        return remaining.pop(0)

    provider.aparse_json = AsyncMock(side_effect=_dispatch)
    return provider


@pytest.mark.asyncio
async def test_approved_first_pass_stores_the_draft_unchanged(db):
    from applire.services.job import analyze_jd

    provider = _make_provider(
        generation=_VALID_RESPONSE,
        review_responses=[{"approved": True, "issues": [], "feedback": ""}],
    )

    result = await analyze_jd("full JD text", db, provider)

    assert result.role_title == "Senior Backend Engineer"
    assert result.required_skills == ["Python", "PostgreSQL"]
    # 1 generation + 1 reviewer call — approved on the first pass.
    assert provider.aparse_json.call_count == 2


@pytest.mark.asyncio
async def test_reviewer_rejection_drops_a_fabricated_requirement(db):
    """A fabricated requirement flagged by the reviewer is corrected before the
    JobAnalysis row is persisted — the review loop actually changes stored data."""
    from applire.services.job import analyze_jd

    fabricated = {**_VALID_RESPONSE, "required_skills": ["Python", "PostgreSQL", "Kubernetes"]}
    corrected = {**_VALID_RESPONSE, "required_skills": ["Python", "PostgreSQL"]}

    provider = _make_provider(
        generation=fabricated,
        review_responses=[
            {
                "approved": False,
                "issues": ["Kubernetes not mentioned anywhere in the posting"],
                "feedback": "Remove Kubernetes from required_skills — not in the source",
            },
            corrected,
            {"approved": True, "issues": [], "feedback": ""},
        ],
    )

    result = await analyze_jd("Senior Backend Engineer. Python and PostgreSQL required.", db, provider)

    assert result.required_skills == ["Python", "PostgreSQL"]
    assert "Kubernetes" not in result.required_skills


@pytest.mark.asyncio
async def test_analyze_jd_declares_required_fields_for_company_and_title(db, monkeypatch):
    """Wave-6 Task 2: analyze_jd's review_and_refine call site must declare
    company_name/role_title as required_fields — the pinned defect dropped both
    entirely after a false-positive reviewer round and never recovered them."""
    import applire.services.job as job_svc

    captured: dict = {}
    real_review_and_refine = job_svc.review_and_refine

    async def _spy(*args, **kwargs):
        captured.update(kwargs)
        return await real_review_and_refine(*args, **kwargs)

    monkeypatch.setattr(job_svc, "review_and_refine", _spy)

    provider = _make_provider(
        generation=_VALID_RESPONSE,
        review_responses=[{"approved": True, "issues": [], "feedback": ""}],
    )

    await job_svc.analyze_jd("full JD text", db, provider)

    assert captured.get("required_fields") == ("company_name", "role_title")


@pytest.mark.asyncio
async def test_analyze_jd_recovers_dropped_company_and_title_end_to_end(db):
    """End-to-end reproduction (synthetic data) of the pinned #264-follow-up defect:
    a false-positive reviewer round drops company_name/role_title entirely and never
    recovers them. analyze_jd must persist the ORIGINAL values, not the empty ones."""
    from applire.services.job import analyze_jd

    original = dict(_VALID_RESPONSE)
    dropped = {**_VALID_RESPONSE, "company_name": None, "role_title": ""}

    provider = _make_provider(
        generation=original,
        review_responses=[
            {
                "approved": False,
                "issues": [
                    "company_name: 'Acme GmbH' is not explicitly stated as the hiring company",
                    "role_title: 'Senior Backend Engineer' is not explicitly stated",
                ],
                "feedback": "Drop company_name and role_title — not grounded",
            },
            dropped,
            {"approved": False, "issues": ["still wrong"], "feedback": "try again"},
            dropped,
        ],
    )

    result = await analyze_jd(
        "Senior Backend Engineer at Acme GmbH. Python and PostgreSQL required.",
        db,
        provider,
    )

    assert result.company_name == "Acme GmbH"
    assert result.role_title == "Senior Backend Engineer"


@pytest.mark.asyncio
async def test_review_disabled_when_max_retries_zero(db, monkeypatch):
    """LLM_REVIEW_MAX_RETRIES=0 is still the documented kill switch for job_analysis."""
    import applire.services.job as job_svc

    monkeypatch.setattr(job_svc, "LLM_REVIEW_MAX_RETRIES", 0)
    provider = _make_provider(generation=_VALID_RESPONSE, review_responses=[])

    result = await job_svc.analyze_jd("full JD text", db, provider)

    assert result.role_title == "Senior Backend Engineer"
    # No reviewer call at all — max_retries<=0 short-circuits review_and_refine.
    assert provider.aparse_json.call_count == 1
