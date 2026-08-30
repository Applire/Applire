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
# #617 — ADR-069 clause 4b/4d/4e (amended 2026-08-29): the ANTI-OSCILLATION
# rule is struck (unobeyable by a memoryless reviewer, ADR-021 2026-07-26
# clause 6), a new check 1b polices polarity instead, checks 1/3/5 are
# reworded, and the corrector gains a schema-keys-are-names rule. The
# GROUNDING FACTS block itself (the view + the fact computation) is pinned in
# tests/unit/test_jd_grounding_617.py, not here — this class pins only the
# static prompt text.
# ---------------------------------------------------------------------------


class TestJobAnalysisReviewPrompt617GroundingFacts:
    def test_anti_oscillation_rule_struck_and_reviewer_stays_memoryless_617(self):
        """Captured evidence (#617): seniority oscillated round to round
        ("Senior overreaches" -> "not stated" -> "missing but explicitly
        stated" -> "not stated") — the ANTI-OSCILLATION rule asked a
        reviewer ADR-021 deliberately keeps memoryless to remember its own
        prior rounds, which it structurally cannot do. Struck outright
        rather than reworded (ADR-069's 2026-08-29 amendment): the GROUNDING
        FACTS block is the loop's memory now, rendered fresh each round from
        the posting, never from a literal review-history parameter."""
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        assert "ANTI-OSCILLATION" not in prompt

        import inspect

        sig = inspect.signature(build_job_analysis_review_prompt)
        assert list(sig.parameters) == ["jd_text", "extracted_json"]

    def test_check_1b_misread_polarity_present_617(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        assert "1b. MISREAD POLARITY" in prompt
        lowered = prompt.lower()
        assert "exclude" in lowered or "negate" in lowered

    def test_checks_1_3_5_reworded_wording_present_617(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        lowered = prompt.lower()
        # Checks 1 and 3 share the normalising-transform vocabulary — the
        # replay evidence's own false-positive reasons ("not standalone",
        # "only part of a larger phrase", "capitalisation differs") named
        # as NOT fabrication.
        for marker in (
            "normalising transform",
            "paraphrase",
            "nominalisation",
            "hyphenation",
            "sub-phrase",
        ):
            assert marker in lowered, f"{marker!r} missing from the reworded checks"
        # Check 1: the posting's role description / responsibilities /
        # what-we-look-for sections all state requirements, not only a
        # section literally titled "Requirements".
        assert "responsibilities" in lowered
        # Check 5: a job board's metadata line states a seniority tier, and
        # an English posting implies English — only an invented CEFR level
        # is an overreach.
        assert "metadata line" in lowered
        assert "CEFR" in prompt
        # The original defect NAMES must still be present verbatim — the
        # rewording extends these checks, it never renames them (also
        # covered by test_genuine_checks_are_not_weakened above).
        assert "FABRICATED REQUIREMENT" in prompt
        assert "FABRICATED KEYWORDS" in prompt
        assert "SENIORITY/LANGUAGE OVERREACH" in prompt

    def test_corrector_schema_key_rule_present_617(self):
        """4d: the corrector must treat schema keys as NAMES, never content
        — the captured evidence shows it renaming `leadership_emphasis.emphasis`
        to the mangled key `leadership_led` after round-1 feedback phrased as
        "'leadership_led' overreaches -> 'balanced'"."""
        prompt = JOB_ANALYSIS_REFINEMENT_PROMPT
        lowered = prompt.lower()
        assert "schema key" in lowered
        assert "never rename" in lowered
        assert "leadership_emphasis" in prompt
        assert '"emphasis"' in prompt

    def test_system_prompt_still_recognised_by_mock_617(self):
        """providers/llm/mock.py keys the whole job_analysis reviewer chain
        off this exact substring (case-insensitive, see mock.py's
        `"job-description data quality auditor" in system_lower`). The live
        behavioural half of this guarantee is
        test_mock_reviewer_chain_recognition.py; this is its static-text
        companion, in the same file as the wording it protects."""
        assert (
            "job-description data quality auditor"
            in JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        )


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
