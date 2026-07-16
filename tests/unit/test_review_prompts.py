"""
Smoke tests for review prompt builders — verify they render without error.

No LLM calls. No Docker.

Run:
    pytest tests/unit/test_review_prompts.py -v
"""
import json
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_SAMPLE_PROFILE = {
    "work_history": [
        {
            "company": "Acme GmbH",
            "role": "Software Developer",
            "start_date": "2020-01",
            "end_date": "2022-12",
            "bullets": ["Built APIs", "Led migrations"],
        }
    ],
    "skills": ["Python", "FastAPI"],
    "education": [],
    "languages": [{"language": "German", "level": "Native"}],
    "contact": {"name": "Max Muster", "email": None, "phone": None, "location": "Berlin", "linkedin": None},
}

_SAMPLE_RAW_CV = "Acme GmbH — Software Developer (Jan 2020 – Dec 2022)\n- Built APIs\n- Led migrations"


class TestCVLanguageReviewPrompt:
    """#1 — the CV tailoring path needs a language-consistency reviewer (mirrors the
    ADR-038 question-language reviewer) so skill tags + prose all land in the output
    language. The system prompt must disambiguate the translate-vs-keep boundary that
    the tailoring prompt left ambiguous (discipline phrases translate; product/tool
    names stay)."""

    _DRAFT = {
        "summary": "Experienced designer.",
        "work_history": [{"company": "Acme", "role": "Designer", "bullets": ["Led brand work"]}],
        "skills": ["Brand Identity", "Kampagnenentwicklung", "Figma"],
    }

    def test_review_prompt_names_language_and_surfaces_skills(self):
        from applire.prompts.review_cv_language import build_cv_language_review_prompt

        p = build_cv_language_review_prompt("German", self._DRAFT)
        assert "German" in p
        assert "Brand Identity" in p  # the leak candidate is shown to the reviewer

    def test_system_prompt_states_translate_vs_keep_boundary(self):
        from applire.prompts.review_cv_language import CV_LANGUAGE_REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "skill" in low
        # discipline/skill phrases translate; genuine product/tool/technology names stay
        assert "product" in low or "tool" in low or "technology" in low

    def test_refinement_prompt_includes_feedback_and_draft(self):
        from applire.prompts.review_cv_language import build_cv_language_refinement_prompt

        p = build_cv_language_refinement_prompt({"skills": ["Brand Identity"]}, "Translate skills to German")
        assert "Translate skills to German" in p
        assert "Brand Identity" in p

    # Blind PQ 2026-07-04: English project bullets shipped in a German CV because
    # the language reviewer never saw project text — only summary/bullets/skills.
    def test_review_prompt_surfaces_project_bullets(self):
        from applire.prompts.review_cv_language import build_cv_language_review_prompt

        draft = {
            **self._DRAFT,
            "work_history": [
                {
                    "company": "Acme",
                    "role": "Designer",
                    "bullets": ["Led brand work"],
                    "projects": [
                        {"name": "QC LIMS", "bullets": ["Built the validation pipeline"]}
                    ],
                }
            ],
            "projects": [
                {"name": "Portfolio Site", "bullets": ["Shipped a static site generator"]}
            ],
        }
        p = build_cv_language_review_prompt("German", draft)
        assert "Built the validation pipeline" in p  # nested under a work entry
        assert "Shipped a static site generator" in p  # standalone list

    def test_language_prompts_cover_projects(self):
        from applire.prompts.review_cv_language import (
            CV_LANGUAGE_REFINEMENT_PROMPT,
            CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        )

        assert "project" in CV_LANGUAGE_REVIEW_SYSTEM_PROMPT.lower()
        assert "project" in CV_LANGUAGE_REFINEMENT_PROMPT.lower()


class TestProfileExtractionReviewPrompts:
    def test_build_review_prompt_returns_nonempty_string(self):
        from applire.prompts.review_profile_extraction import build_review_prompt

        result = build_review_prompt(_SAMPLE_RAW_CV, _SAMPLE_PROFILE)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_build_review_prompt_includes_source_text(self):
        from applire.prompts.review_profile_extraction import build_review_prompt

        result = build_review_prompt(_SAMPLE_RAW_CV, _SAMPLE_PROFILE)
        assert "Acme GmbH" in result

    def test_build_review_prompt_includes_extracted_json(self):
        from applire.prompts.review_profile_extraction import build_review_prompt

        result = build_review_prompt(_SAMPLE_RAW_CV, _SAMPLE_PROFILE)
        assert "Software Developer" in result

    def test_review_system_prompt_is_nonempty_string(self):
        from applire.prompts.review_profile_extraction import REVIEW_SYSTEM_PROMPT

        assert isinstance(REVIEW_SYSTEM_PROMPT, str)
        assert len(REVIEW_SYSTEM_PROMPT) > 100


class TestProfileExtractionGeneratorPrompts:
    def test_build_user_prompt_includes_raw_text(self):
        from applire.prompts.profile_extraction import build_user_prompt

        result = build_user_prompt("Max Muster — Acme GmbH")
        assert "Max Muster" in result
        assert "exactly once" in result  # grounding reminder

    def test_build_retry_prompt_includes_feedback(self):
        from applire.prompts.profile_extraction import build_retry_prompt

        result = build_retry_prompt(
            previous_draft={"work_history": []},
            feedback="Remove duplicate at index 1",
            source="Max Muster — Acme GmbH 2020-2022",
        )
        assert "Remove duplicate at index 1" in result
        assert "Patch the JSON" in result
        # US194: the refiner re-reads the source text.
        assert "Max Muster — Acme GmbH 2020-2022" in result

    def test_build_retry_prompt_includes_previous_draft(self):
        from applire.prompts.profile_extraction import build_retry_prompt

        previous = {"work_history": [{"company": "Acme"}]}
        result = build_retry_prompt(
            previous_draft=previous,
            feedback="fix it",
            source="raw cv text",
        )
        assert "Acme" in result


class TestProfileServiceReviewIntegration:
    """Verify that _import_from_text calls review_and_refine with the right arguments."""

    @pytest.mark.asyncio
    async def test_import_from_text_passes_source_to_reviewer(self):
        from unittest.mock import AsyncMock, patch
        from applire.services.profile import _import_from_text

        extracted = {
            "work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020", "end_date": None, "bullets": []}],
            "skills": [],
            "education": [],
            "languages": [],
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
        }

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = extracted

        captured: dict = {}

        async def fake_review(**kwargs):
            captured.update(kwargs)
            return kwargs["draft"]

        mock_db = AsyncMock()

        # Return a fake MasterProfile so _to_response receives real db fields
        from applire.models.profile import MasterProfile
        from datetime import datetime, timezone

        existing_profile_json = {
            "work_history": [],
            "skills": [],
            "education": [],
            "languages": [],
            "contact": {"name": "", "email": None, "phone": None, "location": None, "linkedin": None},
        }
        mock_record = MasterProfile(
            id="00000000-0000-0000-0000-000000000001",
            profile_json=existing_profile_json,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        with patch("applire.services.profile.review_and_refine", side_effect=fake_review), \
             patch("applire.services.profile._get_latest", new=AsyncMock(return_value=mock_record)), \
             patch("applire.services.profile.capture_pre_merge_snapshot", new=AsyncMock()), \
             patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")), \
             patch("applire.services.profile.LLM_REVIEW_MAX_RETRIES", 2):
            await _import_from_text("Acme Dev 2020-2022", mock_db, mock_provider)

        assert captured.get("source") == "Acme Dev 2020-2022"
        assert captured.get("draft") == extracted
        assert captured.get("max_retries") == 2


# ---------------------------------------------------------------------------
# Shared fixtures for CV tailoring prompt tests
# ---------------------------------------------------------------------------

_SAMPLE_TAILORED_CV = {
    "contact": {"name": "Max Muster", "email": None, "phone": None, "location": "Berlin", "linkedin": None},
    "summary": "Experienced developer targeting backend roles.",
    "work_history": [
        {
            "company": "Acme GmbH",
            "role": "Software Developer",
            "start_date": "2020-01",
            "end_date": "2022-12",
            "bullets": ["Built REST APIs with FastAPI"],
        }
    ],
    "skills": ["Python", "FastAPI"],
    "education": [],
    "languages": [{"language": "German", "level": "Native"}],
}

_SAMPLE_SOURCE_MATERIAL = '{"work_history": [{"company": "Acme GmbH", "role": "Software Developer"}]}'

_SAMPLE_JOB = {
    "role_title": "Backend Engineer",
    "required_skills": ["Python", "FastAPI"],
    "nice_to_have_skills": ["Kubernetes"],
    "keywords": ["microservices"],
    "seniority_level": "Senior",
    "company_culture_signals": [],
    "language_requirement": "German",
}


# ---------------------------------------------------------------------------
# Task 6: CV tailoring reviewer prompts
# ---------------------------------------------------------------------------


class TestCVTailoringReviewPrompts:
    def test_build_review_prompt_returns_nonempty_string(self):
        from applire.prompts.review_cv_tailoring import build_review_prompt

        result = build_review_prompt(_SAMPLE_SOURCE_MATERIAL, _SAMPLE_TAILORED_CV)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_build_review_prompt_includes_source_material(self):
        from applire.prompts.review_cv_tailoring import build_review_prompt

        result = build_review_prompt(_SAMPLE_SOURCE_MATERIAL, _SAMPLE_TAILORED_CV)
        assert "Acme GmbH" in result

    def test_build_review_prompt_includes_tailored_cv(self):
        from applire.prompts.review_cv_tailoring import build_review_prompt

        result = build_review_prompt(_SAMPLE_SOURCE_MATERIAL, _SAMPLE_TAILORED_CV)
        assert "FastAPI" in result

    def test_review_system_prompt_is_nonempty_string(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

        assert isinstance(REVIEW_SYSTEM_PROMPT, str)
        assert len(REVIEW_SYSTEM_PROMPT) > 100


# ---------------------------------------------------------------------------
# Task 7: CV tailoring generator prompt v2
# ---------------------------------------------------------------------------


class TestGroundingJudgeFailureClasses:
    """US142 (ADR-040 prevention tier) — the shipped ADR-021 judges must explicitly
    name the FMEA failure classes so they actively look for them.

    JF-M-3.1 (extraction): garbled/invented dates, fabricated certifications, employers.
    JF-M-6.1/6.2 (tailoring): fabricated certs, overstated claim strength (oversell).
    """

    def test_cv_extraction_review_flags_fabricated_certifications(self):
        from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "certification" in low or "qualification" in low  # JF-M-3.1 (certs)

    def test_cv_extraction_review_flags_garbled_values(self):
        from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT as p
        # not just *invented* — *garbled* (mis-transcribed) employers/dates too (JF-M-3.1)
        assert "garbl" in p.lower()

    def test_cv_tailoring_review_flags_fabricated_certifications(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "certification" in low or "qualification" in low  # JF-M-6.1

    def test_cv_tailoring_review_flags_oversell(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        # claim-strength / seniority inflation beyond profile evidence (JF-M-6.2)
        assert "overstate" in low or "exaggerat" in low or "claim strength" in low


class TestCVExtractionReviewerProjectsClause:
    """US172 (E034, ADR-044) — additive projects clause for the CV-extraction reviewer.

    The reviewer previously only covered work_experience.  Projects now have their own
    block in extracted profiles, so the reviewer must:
      (a) apply the same anti-fabrication checks (invented dates → null, no fabricated
          metrics/achievements/technologies without source support, semantic faithfulness)
          to `projects` entries, AND
      (b) explicitly allow standalone personal projects that have no employer/company —
          absence of an employer must NOT be treated as a shell/fabricated/empty entry.

    These tests guard the prompt CONTRACT.  The real-LLM behavioural proof lives in
    test_grounding_corpus_llm.py (INTEGRATION_LLM=1).
    """

    @property
    def _prompt(self):
        from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
        return CV_EXTRACTION_REVIEW_SYSTEM_PROMPT.lower()

    def test_prompt_mentions_projects(self):
        # the reviewer must explicitly address the `projects` block
        assert "project" in self._prompt

    def test_projects_no_employer_required(self):
        # the key difference from work_experience: no employer is required for a project
        low = self._prompt
        assert (
            "no employer" in low
            or "standalone" in low
            or "do not require" in low
            or "does not require" in low
            or "without an employer" in low
        )

    def test_projects_invented_date_rule_applies(self):
        # the invented-date/null rule must appear WITHIN the projects clause itself,
        # not merely elsewhere in the prompt (bind to the clause so deleting it fails).
        low = self._prompt
        assert "projects block" in low
        projects_section = low.split("projects block", 1)[1]
        assert "date" in projects_section and "null" in projects_section

    def test_projects_anti_fabrication_covers_content(self):
        # metrics / achievements / technologies must be source-supported for projects too
        low = self._prompt
        # "metric" OR "achievement" OR "technolog" shows fabrication scope extends to content
        assert "metric" in low or "achievement" in low or "technolog" in low

    def test_projects_cross_entity_misattribution_applies(self):
        # priority check A (cross-role misattribution) must extend to projects as well
        low = self._prompt
        assert "misattribut" in low


class TestCVExtractionReviewerPrecision:
    """US171 (E034, ADR-021 amended) — the CV-extraction reviewer was over-flagging:
    its verbatim/'explicitly stated' fabrication test rejected legitimate paraphrase,
    sentence splits and de-dup merges, exhausting both retries on nearly every upload
    so real errors survived the budget. The recalibration reframes the fabrication test
    from *verbatim* to *semantic* faithfulness and promotes two high-harm checks —
    cross-role content misattribution and invented dates — to named priority checks.

    The behavioural proof (paraphrase stays approved; misattribution/invented-date get
    rejected) is a real-LLM corpus assertion in test_grounding_corpus_llm.py. These
    deterministic tests guard the *prompt contract* that makes that behaviour possible.
    """

    @property
    def _prompt(self):
        from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
        return CV_EXTRACTION_REVIEW_SYSTEM_PROMPT.lower()

    def test_reframes_fabrication_from_verbatim_to_semantic(self):
        # the core recalibration: judge meaning-fidelity, not surface form
        assert "semantic" in self._prompt or "meaning" in self._prompt

    def test_allows_paraphrase_as_legitimate_transformation(self):
        low = self._prompt
        assert "paraphrase" in low
        # framed as permitted, not as invention (mirrors review_cv_language
        # "Translating is not inventing" and review_cover_letter's "not fabrications")
        assert "not invention" in low or "not fabricat" in low or "legitimate" in low or "allowed" in low

    def test_allows_sentence_split_and_dedup_merge(self):
        low = self._prompt
        assert "split" in low      # one source sentence rendered as two bullets
        assert "merge" in low      # de-duplicated / consolidated entries

    def test_names_cross_role_misattribution_as_priority_check(self):
        # genuinely new (no prior art): an achievement landing under the wrong employer/role
        low = self._prompt
        assert "misattribut" in low
        assert "role" in low or "employer" in low

    def test_frames_priority_checks_including_dates(self):
        # the two high-harm checks are promoted/named as priorities
        low = self._prompt
        assert "priorit" in low

    def test_retains_invented_date_null_rule(self):
        # regression guard — the recalibration must NOT drop the invented-date rule
        low = self._prompt
        assert "date" in low and "null" in low


class TestCVTailoringReviewerLedgerChecks:
    """E037 US202 (detection re-sourced by US213, #122) — the CV grounding reviewer
    (a) acts on the deterministic VERIFIED COVERAGE CHECK block (it no longer scans
    for absent claimable terms itself; its only coverage judgment is the grounding
    waiver, riding the bounded ADR-047 loop — no new loop, no forced injection),
    and (b) flags any forbidden honest-gap concept that appears as a claim
    (strengthens the fabrication check). The claimable set rides in via the SOURCE."""

    def test_system_prompt_instructs_absent_claimable_check(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "claimable" in low
        assert "absent" in low or "missing" in low

    def test_system_prompt_instructs_forbidden_claim_check(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        # the do-not-claim / forbidden honest-gap concept must never appear as a claim
        assert "do not claim" in low or "do-not-claim" in low or "forbidden" in low

    def test_system_prompt_keeps_grounding_outranks_coverage(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        # ADR-048 §8: never push fabrication to chase a claimable term
        assert "outrank" in low or "never fabricat" in low or "do not fabricat" in low or "not invent" in low


class TestCoverLetterReviewerLedgerChecks:
    """E037 US202 — cover-letter twin of the CV reviewer ledger checks."""

    def test_system_prompt_instructs_absent_claimable_check(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "claimable" in low
        assert "absent" in low or "missing" in low

    def test_system_prompt_instructs_forbidden_claim_check(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "do not claim" in low or "do-not-claim" in low or "forbidden" in low

    def test_system_prompt_keeps_grounding_outranks_coverage(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "outrank" in low or "never fabricat" in low or "do not fabricat" in low or "not invent" in low


class TestCVTailoringGeneratorPrompts:
    def test_build_user_prompt_returns_nonempty_string(self):
        from applire.prompts.cv_tailoring import build_user_prompt

        result = build_user_prompt(_SAMPLE_JOB, _SAMPLE_PROFILE, [], [])
        assert isinstance(result, str)
        assert "Backend Engineer" in result

    def test_system_prompt_constrains_claim_strength(self):
        """US169 (JF-M-6.2) — generator-side prevention. The reviewer already flags
        oversell (rule 6, US142), but the *generator* prompt never told the model not
        to inflate. Prevention lowers O before the draft exists; detection alone leaves
        an unnecessary over-claim→reject→retry round-trip."""
        from applire.prompts.cv_tailoring import SYSTEM_PROMPT

        low = SYSTEM_PROMPT.lower()
        # the rule must forbid inflating claim strength / seniority beyond evidence
        assert "inflate" in low or "overstate" in low
        assert "seniority" in low
        # and must anchor verb strength to the source (don't upgrade "supported" → "led")
        assert "led" in low and "supported" in low

    def test_build_retry_prompt_includes_feedback(self):
        from applire.prompts.cv_tailoring import build_retry_prompt

        result = build_retry_prompt(
            previous_draft=_SAMPLE_TAILORED_CV,
            feedback="Remove fabricated Kubernetes bullet in work_history[0]",
            source='{"work_history": [{"company": "Acme"}]}',
        )
        assert "Remove fabricated Kubernetes bullet" in result
        assert "Patch the JSON" in result
        # US194: the corrector re-reads the candidate profile (source of truth).
        assert '{"work_history": [{"company": "Acme"}]}' in result

    def test_build_retry_prompt_includes_previous_draft(self):
        from applire.prompts.cv_tailoring import build_retry_prompt

        result = build_retry_prompt(
            previous_draft=_SAMPLE_TAILORED_CV,
            feedback="fix",
            source="profile source",
        )
        assert "Experienced developer" in result


# ---------------------------------------------------------------------------
# Task 8: CV service review integration
# ---------------------------------------------------------------------------


class TestCVServiceReviewIntegration:
    """Verify that _render_cv_background calls review_and_refine with correct arguments."""

    @pytest.mark.asyncio
    async def test_render_cv_background_passes_profile_as_source(self):
        """review_and_refine source should be the serialised master profile JSON."""
        import json
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        profile_json = {
            "work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020", "end_date": None, "bullets": []}],
            "skills": ["Python"],
            "education": [],
            "languages": [],
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
            "personal_info": {},
        }

        tailored_raw = {
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
            "summary": "Dev.",
            "work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020", "end_date": None, "bullets": []}],
            "skills": ["Python"],
            "education": [],
            "languages": [],
        }

        calls: list[dict] = []

        async def fake_review(**kwargs):
            calls.append(kwargs)
            return kwargs["draft"]

        mock_cv_id = uuid.uuid4()
        mock_job_id = uuid.uuid4()
        mock_profile_id = uuid.uuid4()

        mock_cv = MagicMock()
        mock_cv.status = "pending"
        mock_cv.target_pages = 2  # E042/US237: real int — compute_bullet_budgets does arithmetic on it
        mock_job = MagicMock()
        mock_job.role_title = "Dev"
        mock_job.required_skills = []
        mock_job.nice_to_have_skills = []
        mock_job.keywords = []
        mock_job.seniority_level = ""
        mock_job.company_culture_signals = []
        mock_job.language_requirement = ""
        mock_profile = MagicMock()
        mock_profile.profile_json = profile_json

        mock_db = AsyncMock()
        mock_db.get.side_effect = lambda model, id_: {
            mock_cv_id: mock_cv,
            mock_job_id: mock_job,
            mock_profile_id: mock_profile,
        }[id_]
        # Gap query: db.execute(...) returns AsyncMock which when called returns mock_result.
        # AsyncMock auto-magic: return_value is used when the mock is called
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = tailored_raw

        with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
             patch("applire.services.cv.get_provider", return_value=mock_provider), \
             patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
             patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            from applire.services.cv import _render_cv_background
            await _render_cv_background(mock_cv_id, mock_job_id, mock_profile_id, "classic_german")

        expected_source = json.dumps(profile_json, ensure_ascii=False, indent=2)
        # Grounding review (review_cv_tailoring) gets the serialised master profile as source.
        grounding = [c for c in calls if c.get("source") == expected_source]
        assert grounding, "grounding review_and_refine not called with the profile source"
        assert grounding[0]["draft"] == tailored_raw
        assert grounding[0]["max_retries"] == 2
        # #1: a second, language-enforcement pass runs (ADR-038) — source is a language name.
        assert any(c.get("chain_id") == "cv_language" for c in calls), \
            "CV language-review pass not wired into generation"

    @pytest.mark.asyncio
    async def test_grounding_review_source_carries_claimable_ledger(self):
        """US202: the claimable Keyword-Ledger terms must reach the grounding reviewer
        via its source, so the reviewer can report absent-claimable + forbidden claims."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        profile_json = {
            "work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020", "end_date": None, "bullets": []}],
            "skills": ["Python"], "education": [], "languages": [],
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
            "personal_info": {},
        }
        tailored_raw = {
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
            "summary": "Dev.",
            "work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020", "end_date": None, "bullets": []}],
            "skills": ["Python"], "education": [], "languages": [],
        }
        ledger = [
            {"concept": "Kubernetes", "surface_forms": ["Kubernetes", "K8s"], "claimable": True,
             "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "8y"},
            {"concept": "Rust", "surface_forms": ["Rust"], "claimable": False,
             "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
        ]

        calls: list[dict] = []

        async def fake_review(**kwargs):
            calls.append(kwargs)
            return kwargs["draft"]

        cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cv = MagicMock(); mock_cv.status = "pending"
        mock_cv.target_pages = 2  # E042/US237: real int — compute_bullet_budgets does arithmetic on it
        mock_job = MagicMock()
        mock_job.role_title = "Dev"; mock_job.required_skills = []; mock_job.nice_to_have_skills = []
        mock_job.keywords = []; mock_job.seniority_level = ""; mock_job.company_culture_signals = []
        mock_job.language_requirement = ""
        mock_profile = MagicMock(); mock_profile.profile_json = profile_json

        mock_gap = MagicMock()
        mock_gap.keyword_gaps = []; mock_gap.critical_gaps = []; mock_gap.keyword_ledger = ledger

        mock_db = AsyncMock()
        mock_db.get.side_effect = lambda model, id_: {
            cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
        }[id_]
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_gap
        mock_db.execute.return_value = mock_result

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = tailored_raw

        with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
             patch("applire.services.cv.get_provider", return_value=mock_provider), \
             patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
             patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            from applire.services.cv import _render_cv_background
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

        grounding = [c for c in calls if c.get("chain_id") == "cv_tailoring"]
        assert grounding, "grounding reviewer not called"
        src = grounding[0]["source"]
        assert "Kubernetes" in src and "K8s" in src   # claimable term + surface form surfaced
        assert "Rust" in src                          # forbidden honest-gap surfaced


# ---------------------------------------------------------------------------
# US170 (#54): cover-letter grounding reviewer prompts + service integration
# ---------------------------------------------------------------------------


_SAMPLE_LETTER_SOURCE = (
    '{"work_history": [{"company": "Acme GmbH", "role": "Software Developer", '
    '"start_date": "2020-01", "end_date": "2022-12", "bullets": ["Built REST APIs"]}], '
    '"skills": ["Python", "FastAPI"]}'
)

_SAMPLE_LETTER = {
    "header": {"name": "Max Muster", "address": "Berlin"},
    "recipient": {"name": "Dr. Müller", "company": "Roche", "date": None},
    "body": {
        "paragraphs": [
            "Sehr geehrte Frau Dr. Müller,",
            "als Software Developer bei Acme GmbH habe ich REST-APIs entwickelt.",
            "Ich freue mich auf das Gespräch.",
        ]
    },
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Max Muster"},
}


class TestCoverLetterReviewPrompts:
    def test_review_system_prompt_is_nonempty_string(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT

        assert isinstance(REVIEW_SYSTEM_PROMPT, str)
        assert len(REVIEW_SYSTEM_PROMPT) > 100

    def test_review_system_prompt_names_grounding_classes(self):
        """The judge must actively look for the FMEA JF-M-8.1 fabrication classes."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "date" in low                      # the observed date-hallucination class
        assert "employ" in low or "company" in low
        assert "achievement" in low
        assert "invent" in low or "fabricat" in low or "ungrounded" in low

    def test_build_review_prompt_includes_source_and_letter_body(self):
        from applire.prompts.review_cover_letter import build_review_prompt

        result = build_review_prompt(_SAMPLE_LETTER_SOURCE, _SAMPLE_LETTER)
        assert "Acme GmbH" in result          # the source of truth is shown to the judge
        assert "REST-APIs" in result          # the letter body is shown to the judge

    def test_build_review_prompt_hands_judge_source_and_fabricated_achievement(self):
        """US170 (JF-M-8.1) — fabricated-achievement regression. The necessary condition
        for the judge to catch a fabrication is that the prompt carries BOTH the truthful
        source and the ungrounded claim. The source has no such achievement; the body does."""
        from applire.prompts.review_cover_letter import build_review_prompt

        fabricated = "led the company's €4M cloud migration"
        assert fabricated not in _SAMPLE_LETTER_SOURCE   # genuinely ungrounded
        letter = json.loads(json.dumps(_SAMPLE_LETTER))
        letter["body"]["paragraphs"][1] = f"In meiner Rolle habe ich {fabricated}."

        result = build_review_prompt(_SAMPLE_LETTER_SOURCE, letter)
        assert "Acme GmbH" in result        # truthful source present
        assert fabricated in result          # the claim to be caught present

    def test_refinement_prompt_is_nonempty_and_restricts_invention(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        assert isinstance(p, str) and len(p) > 100
        low = p.lower()
        assert "invent" in low or "fabricat" in low

    def test_build_retry_prompt_includes_feedback_and_previous_draft(self):
        from applire.prompts.review_cover_letter import build_retry_prompt

        result = build_retry_prompt(
            previous_draft=_SAMPLE_LETTER,
            feedback="Remove the invented 'Head of Engineering' claim in paragraph 2",
            source=_SAMPLE_LETTER_SOURCE,
        )
        assert "Head of Engineering" in result
        assert "REST-APIs" in result          # the previous draft is carried forward
        # US194: the writer re-reads the candidate source (grounding).
        assert "Software Developer" in result


class TestCoverLetterServiceReviewIntegration:
    """US170 — _render_cover_letter_background must run the grounding reviewer over the
    generated letter, with the CV/profile/user-inputs as the source of truth."""

    @pytest.mark.asyncio
    async def test_render_passes_grounding_source_to_reviewer(self):
        import json
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        cv_tailored = {
            "contact": {"name": "Max Muster"},
            "summary": "Backend developer.",
            "work_history": [{"company": "Acme GmbH", "role": "Software Developer",
                              "start_date": "2020-01", "end_date": "2022-12",
                              "bullets": ["Built REST APIs"]}],
            "skills": ["Python", "FastAPI"],
        }
        # The model hallucinates a letter date (the 2026-06-10 failure class). The system
        # must overwrite it AFTER review — never let an LLM-set date reach the document.
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))
        letter_raw["recipient"]["date"] = "10. Oktober 2023"

        mock_cl = MagicMock()
        mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}
        mock_cl.section_overrides = {}

        mock_job = MagicMock()
        mock_job.raw_text = "We are hiring a Backend Engineer."
        mock_job.company_name = "Roche"
        mock_job.keywords = []

        mock_cv = MagicMock()
        mock_cv.tailored_data = cv_tailored

        mock_profile = MagicMock()
        mock_profile.profile_json = {"work_history": cv_tailored["work_history"], "skills": cv_tailored["skills"]}

        # The render loads cl, job, cv, profile, then the Keyword Ledger gap (US201) —
        # five db.execute() → scalar_one_or_none() calls in order. One shared result
        # object carries the sequence; None gap = pre-E037 row, no ledger.
        shared_result = MagicMock()
        shared_result.scalar_one_or_none.side_effect = [
            mock_cl, mock_job, mock_cv, mock_profile, None,
        ]

        mock_db = AsyncMock()
        mock_db.execute.return_value = shared_result

        calls: list[dict] = []

        async def fake_review(**kwargs):
            calls.append(kwargs)
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="de"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": "Dr. Müller"}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls, "review_and_refine was not called — the letter ships ungrounded"
        c = calls[0]
        assert c["chain_id"] == "cover_letter"
        assert c["draft"] == letter_raw
        # the source must carry the grounded CV facts the judge checks the letter against
        assert "Acme GmbH" in c["source"]
        # date-hallucination regression (2026-06-10): the model's date is overwritten by
        # the system clock AFTER review — the LLM value never survives to the document.
        assert mock_cl.letter_data["recipient"]["date"] != "10. Oktober 2023"
        assert "2026" in mock_cl.letter_data["recipient"]["date"]

    @pytest.mark.asyncio
    async def test_review_source_carries_claimable_ledger(self):
        """US202: the cover-letter grounding reviewer's source must carry the claimable
        Keyword-Ledger terms (and the forbidden honest-gap concepts)."""
        import json
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {
            "contact": {"name": "Max Muster"}, "summary": "Backend developer.",
            "work_history": [{"company": "Acme GmbH", "role": "Software Developer",
                              "start_date": "2020-01", "end_date": "2022-12",
                              "bullets": ["Built REST APIs"]}],
            "skills": ["Python", "FastAPI"],
        }
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We are hiring a Backend Engineer."
        mock_job.company_name = "Roche"; mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {"work_history": cv_tailored["work_history"], "skills": cv_tailored["skills"]}

        ledger = [
            {"concept": "Kubernetes", "surface_forms": ["Kubernetes", "K8s"], "claimable": True,
             "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "8y"},
            {"concept": "Rust", "surface_forms": ["Rust"], "claimable": False,
             "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
        ]
        mock_gap = MagicMock(); mock_gap.keyword_ledger = ledger

        shared_result = MagicMock()
        shared_result.scalar_one_or_none.side_effect = [
            mock_cl, mock_job, mock_cv, mock_profile, mock_gap,
        ]
        mock_db = AsyncMock(); mock_db.execute.return_value = shared_result

        calls: list[dict] = []

        async def fake_review(**kwargs):
            calls.append(kwargs)
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="de"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": "Dr. Müller"}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls, "review_and_refine was not called"
        src = calls[0]["source"]
        assert "Kubernetes" in src and "K8s" in src
        assert "Rust" in src
