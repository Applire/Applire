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


class TestCoverLetterPositioningIntegration:
    """E048/US264 (ADR-057 amended 2026-07-24 / ADR-058 exception (a)) — the blind
    hiring-panel blockers: the letter must engage the target company/domain, honestly
    argue the candidate's OWN transfer story for the one true (Category C) gap when
    vault testimony exists, and address availability only when BOTH the deterministic
    concurrent-roles detector fires AND matching vault testimony exists. All three are
    deterministic prompt-input threading — no new LLM chain, no new pass — so this
    harness (mirrors TestCoverLetterServiceReviewIntegration) drives the REAL
    ``_render_cover_letter_background`` and inspects the ACTUAL generation prompt the
    (spy) provider received."""

    def _wire(self, mock_cl, mock_job, mock_cv, mock_profile, mock_gap, mock_provider):
        """Common db/session/provider patch stack — returns the context manager list
        (as a list of `with` targets is awkward, callers use this via a helper)."""
        from unittest.mock import AsyncMock, MagicMock

        shared_result = MagicMock()
        shared_result.scalar_one_or_none.side_effect = [
            mock_cl, mock_job, mock_cv, mock_profile, mock_gap,
        ]
        mock_db = AsyncMock()
        mock_db.execute.return_value = shared_result
        return mock_db

    @pytest.mark.asyncio
    async def test_render_threads_company_name_into_generation_prompt(self):
        """Blocker #1 — the letter must gain an explicit, concrete company/domain
        engagement instruction naming the TARGET company."""
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
        mock_job.raw_text = "Roche builds diagnostics instruments for regulated healthcare labs."
        mock_job.company_name = "Roche Diagnostics"
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": cv_tailored["work_history"], "skills": cv_tailored["skills"],
        }
        mock_gap = None  # legacy pre-E037 row — no ledger, no category_c

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: COMPANY & DOMAIN ENGAGEMENT" in generation_prompt
        assert "TARGET COMPANY: Roche Diagnostics" in generation_prompt
        assert "diagnostics instruments" in generation_prompt  # the JD's own domain text

    @pytest.mark.asyncio
    async def test_render_threads_gap_testimony_when_category_c_story_matches(self):
        """Blocker #2 — a Category C gap with a matching Signature Story (ADR-055)
        must surface the candidate's OWN transfer-argument testimony verbatim."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {
            "contact": {"name": "Max Muster"}, "summary": "QA specialist.",
            "work_history": [{"company": "Acme GmbH", "role": "QA Engineer",
                              "start_date": "2020-01", "end_date": "2022-12",
                              "bullets": ["Built automated regression suites"]}],
            "skills": ["Test Automation"],
        }
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We need regulated industries experience."
        mock_job.company_name = None
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": cv_tailored["work_history"], "skills": cv_tailored["skills"],
            "signature_stories": [
                {
                    "title": "Bringing GxP rigor to a startup",
                    "challenge": "The team had never worked in regulated industries before.",
                    "mechanism": "I applied my prior pharma QA discipline to the release process.",
                    "outcome": "We passed our first external audit with zero findings.",
                    "benchmark": None,
                }
            ],
        }
        mock_gap = MagicMock()
        mock_gap.category_c = ["regulated industries experience"]
        mock_gap.keyword_ledger = []

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: HONEST GAP / TRANSFER ARGUMENT" in generation_prompt
        assert "GAP: regulated industries experience" in generation_prompt
        assert "prior pharma QA discipline" in generation_prompt  # the story's own text, verbatim
        assert "passed our first external audit" in generation_prompt

    @pytest.mark.asyncio
    async def test_render_omits_gap_testimony_when_no_story_matches(self):
        """No Signature Story overlaps the Category C gap → the letter says nothing
        about it (silence over invention)."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {
            "contact": {"name": "Max Muster"}, "summary": "QA specialist.",
            "work_history": [], "skills": [],
        }
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We need Kubernetes orchestration experience."
        mock_job.company_name = None
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": [], "skills": [],
            "signature_stories": [
                {
                    "title": "Winning a design award",
                    "challenge": "Our brand felt generic.",
                    "mechanism": "I ran a full visual identity overhaul.",
                    "outcome": "We won a regional design award.",
                }
            ],
        }
        mock_gap = MagicMock()
        mock_gap.category_c = ["Kubernetes orchestration"]
        mock_gap.keyword_ledger = []

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: HONEST GAP / TRANSFER ARGUMENT" not in generation_prompt

    @pytest.mark.asyncio
    async def test_render_threads_availability_only_when_detector_fires_and_testimony_exists(self):
        """Blocker #3 — >=2 open-ended work_experience entries (concurrent roles) PLUS
        matching vault testimony → the availability block is threaded, verbatim."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {"contact": {"name": "Max Muster"}, "summary": "", "work_history": [], "skills": []}
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We are hiring."
        mock_job.company_name = None
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": [], "skills": [],
            "work_experience": [
                {"role": "CTO", "company": "Startup A", "is_current": True, "end_date": None},
                {"role": "Advisor", "company": "Startup B", "is_current": True, "end_date": None},
            ],
            "signature_stories": [
                {
                    "title": "Managing concurrent commitments",
                    "challenge": "I run two advisory roles in parallel.",
                    "mechanism": "I block dedicated hours for each and communicate availability clearly.",
                    "outcome": "Both engagements stayed on schedule.",
                }
            ],
        }
        mock_gap = None

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS" in generation_prompt
        assert "I run two advisory roles in parallel" in generation_prompt

    @pytest.mark.asyncio
    async def test_render_omits_availability_when_detector_fires_but_no_testimony(self):
        """Detector fires (2 open-ended roles) but the vault holds NO availability
        testimony — the letter must make NO availability/commitment claim."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {"contact": {"name": "Max Muster"}, "summary": "", "work_history": [], "skills": []}
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We are hiring."
        mock_job.company_name = None
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": [], "skills": [],
            "work_experience": [
                {"role": "CTO", "company": "Startup A", "is_current": True, "end_date": None},
                {"role": "Advisor", "company": "Startup B", "is_current": True, "end_date": None},
            ],
            "signature_stories": [],
        }
        mock_gap = None

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS" not in generation_prompt

    @pytest.mark.asyncio
    async def test_render_omits_availability_when_only_single_current_role(self):
        """Detector does NOT fire (only 1 open-ended role) — no availability block even
        if testimony happens to exist in the vault."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {"contact": {"name": "Max Muster"}, "summary": "", "work_history": [], "skills": []}
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "We are hiring."
        mock_job.company_name = None
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_history": [], "skills": [],
            "work_experience": [
                {"role": "CTO", "company": "Startup A", "is_current": True, "end_date": None},
                {"role": "Past role", "company": "B", "is_current": False, "end_date": "2019"},
            ],
            "signature_stories": [
                {
                    "title": "Managing concurrent commitments",
                    "challenge": "I run two advisory roles in parallel.",
                    "mechanism": "I block dedicated hours for each.",
                    "outcome": "Both engagements stayed on schedule.",
                }
            ],
        }
        mock_gap = None

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

        async def fake_review(**kwargs):
            return kwargs["draft"]

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        generation_prompt = mock_provider.aparse_json.call_args.args[0]
        assert "POSITIONING: AVAILABILITY / CONCURRENT COMMITMENTS" not in generation_prompt

    @pytest.mark.asyncio
    async def test_render_threads_job_description_into_reviewer_grounding_source(self):
        """Oracle discipline unchanged (AC #4): the reviewer must receive the SAME JD
        text the generator saw, so a company/domain claim can be judged grounded or
        invented — an invented company fact must still fail to ground."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cv_tailored = {"contact": {"name": "Max Muster"}, "summary": "", "work_history": [], "skills": []}
        letter_raw = json.loads(json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "Roche builds diagnostics instruments for regulated healthcare labs."
        mock_job.company_name = "Roche Diagnostics"
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        mock_profile = MagicMock()
        mock_profile.profile_json = {"work_history": [], "skills": []}
        mock_gap = None

        mock_db = self._wire(mock_cl, mock_job, mock_cv, mock_profile, mock_gap, None)

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
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls, "review_and_refine was not called"
        src = calls[0]["source"]
        # The reviewer's ground truth now carries the SAME JD text the generator saw —
        # the necessary condition for it to judge a company/domain claim grounded vs
        # invented (an invented product/market NOT in this text has nowhere to ground).
        assert "diagnostics instruments" in src
        assert "job_description" in src

    def test_review_system_prompt_flags_invented_employer_facts(self):
        """The reviewer's own instructions must name employer/company facts not
        present in job_description as invented — proves an invented company fact
        would NOT be grounded (Oracle discipline unchanged, AC #4)."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT

        assert "job_description" in REVIEW_SYSTEM_PROMPT
        assert "INVENTED EMPLOYER" in REVIEW_SYSTEM_PROMPT.upper()


# ---------------------------------------------------------------------------
# #255 — positioning inputs threaded through the reviewer/corrector loop, and the
# denied-concept vocabulary collision (a DO-NOT-CLAIM term is forbidden only as a
# candidate-competence claim, not as an honest employer-domain fact or an honest
# gap-naming inside the transfer argument).
# ---------------------------------------------------------------------------


class TestReviewerPositioningThreading:
    """The reviewer prompt must know positioning content was REQUESTED, so it can
    (a) flag its absence, and (b) not mistake an honestly-used denied concept for a
    forbidden candidate claim."""

    def test_review_system_prompt_explains_positioning_requested_block(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        assert "positioning_requested" in p
        assert "company_domain_engagement" in p
        assert "gap_transfer_argument" in p

    def test_review_system_prompt_flags_missing_required_positioning_content(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "missing required positioning content" in low
        assert "absence" in low and "issue" in low

    def test_review_system_prompt_scopes_forbidden_claim_to_possessive_framing(self):
        """The vocabulary collision (#255): 'LegalTech' sits in DO NOT CLAIM because the
        candidate denied having it — but the reviewer must not flag it as an employer-
        domain fact or inside an honest transfer argument naming the gap."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "possessive" in low or "competence" in low
        assert "legaltech" in low  # the concrete #255 example is named
        assert "not a claim" in low or "not the fabrication" in low or "honesty, not" in low

    def test_review_system_prompt_flags_minted_figures(self):
        """#255: the corrector previously minted 'mentoring teams of 5+' while chasing
        a coverage push — the reviewer must never invite/accept an unverbatim figure."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "minted figure" in low or "mint a figure" in low
        assert "verbatim" in low

    def test_build_review_prompt_asks_about_positioning_completeness(self):
        from applire.prompts.review_cover_letter import build_review_prompt

        result = build_review_prompt(_SAMPLE_LETTER_SOURCE, _SAMPLE_LETTER)
        assert "positioning_requested" in result

    def test_review_system_prompt_names_closing_as_required_positioning(self):
        """#272 Task 2: the reviewer's positioning_requested vocabulary must
        include "closing" alongside the pre-existing three, so check 7 (missing
        required positioning content) also fires on a missing/eroded closing."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "closing" in low
        assert "positioning_requested" in p

    def test_refinement_prompt_instructs_preserving_positioning_content(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "positioning_requested" in p
        assert "preserve" in low

    def test_refinement_prompt_never_deletes_closing_paragraph(self):
        """RC-D ground truth: corrector round 5 deleted the entire closing
        paragraph while fixing an unrelated (false-positive) availability flag.
        The corrector's own system prompt must forbid that explicitly."""
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "closing paragraph" in low
        assert "never delete" in low or "do not delete" in low or "never remove" in low

    def test_refinement_prompt_forbids_minting_figures(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "never mint a figure" in low or "never introduce a number" in low
        assert "verbatim" in low

    def test_writer_system_prompt_scopes_do_not_claim_to_competence_only(self):
        """The writer's own CLAIM FRAMING rule must carry the same vocabulary-collision
        clarification, not just the reviewer — defense in depth (#255)."""
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p

        low = p.lower()
        assert "candidate competence" in low
        assert "honesty, not a claim" in low or "not a claim" in low

    def test_writer_system_prompt_forbids_minting_ledger_figures(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p

        low = p.lower()
        assert "minted figure" in low or "invent a number" in low


# ---------------------------------------------------------------------------
# #272 Task 4 — narrow the minted-figure check (check 8) so it stops
# oscillating on word-vs-digit form and non-numeric quantifiers, without
# weakening its real teeth (an actually-invented figure like "teams of 5+"
# must still read as flagged).
# ---------------------------------------------------------------------------


class TestMintedFigureCheckWordingNarrowed:
    @property
    def _prompt(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT
        return REVIEW_SYSTEM_PROMPT.lower()

    def test_word_and_digit_forms_of_same_number_are_never_a_form_difference(self):
        """RC-E ground truth: round 3 wrote '7 months', round 4 changed it to
        'seven months', round 5 flagged 'seven months' as unverbatim and asked
        for '7 months' again — pure oscillation. The prompt must state a
        word-form and its digit-form are the SAME figure."""
        low = self._prompt
        assert "seven months" in low and "7 months" in low
        assert "same figure" in low or "numerically-equivalent" in low or "numerically equivalent" in low

    def test_non_numeric_quantifiers_are_not_figures(self):
        """RC-E ground truth: 'multiple LLMs' was wrongly flagged as an
        unverbatim figure against a source stating 'several different LLMs' —
        neither is a figure at all."""
        low = self._prompt
        for word in ("multiple", "several", "various"):
            assert word in low
        assert "not a figure" in low or "not figures" in low

    def test_minted_only_when_no_equivalent_form_appears_anywhere(self):
        low = self._prompt
        assert "no numerically-equivalent form" in low or "no numerically equivalent form" in low

    def test_never_reverse_a_previously_requested_change(self):
        """The general anti-oscillation rule: never raise the same issue in a
        form that reverses a change requested in an earlier round."""
        low = self._prompt
        assert "oscillat" in low or "reverses a change" in low or "revers" in low

    def test_check_8_still_catches_a_genuinely_invented_figure(self):
        """US264/#255 regression guard: the check's real teeth must survive the
        wording narrowing — an actually-invented 'teams of 5+' must still read
        as something the reviewer is instructed to flag."""
        low = self._prompt
        assert "teams of 5+" in low
        assert "fabricat" in low or "invented" in low

    def test_checks_1_through_7_are_not_weakened(self):
        """Sanity: the check-8 rewrite must not have deleted or diluted the
        earlier checks' own key vocabulary."""
        low = self._prompt
        assert "invented dates" in low or "invented date" in low
        assert "invented employers" in low or "invented employer" in low
        assert "fabricated achievements" in low or "fabricated achievement" in low
        assert "ungrounded requirement claims" in low or "ungrounded requirement claim" in low
        assert "verified coverage" in low
        assert "invented employer/company facts" in low
        assert "missing required positioning content" in low


class TestPositionAnchoringRequirement:
    """#283 — the run-6 ground truth: a corrector chasing keyword coverage
    folded an achievement (BioNTech's "record-breaking QC LIMS implementation
    in 7 months across 3 sites") into a paragraph whose sentence never named
    the employer. The letter separately named a DIFFERENT employer (Applire)
    elsewhere, so neither the sentence-level anchor nor the whole-letter
    single-employer escape could resolve ownership, and the deterministic
    #254 figure guard correctly (and silently) dropped '7' and '3' — leaving
    "delivered ... in months across sites", vaguer than the truth. The fix is
    a prompt-level requirement: any sentence carrying a position-owned
    achievement or figure must name that employer in the SAME sentence.
    """

    def test_review_system_prompt_flags_unanchored_position_owned_content(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "anchor" in low
        assert "same sentence" in low
        assert "#283" in p or "283" in p

    def test_refinement_prompt_requires_naming_employer_in_same_sentence(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "anchor" in low
        assert "same sentence" in low

    def test_refinement_prompt_forbids_silently_omitting_the_anchor(self):
        """The guardrail: restoring the figure with a correct anchor is the
        goal — never quietly leaving it dropped/vaguer to avoid the check."""
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "vaguer" in low or "weaker" in low or "silently dropped" in low

    def test_checks_1_through_8_are_not_weakened_by_the_anchor_check(self):
        """Sanity: adding the anchor check must not have deleted or diluted
        the earlier checks' key vocabulary."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "invented dates" in low or "invented date" in low
        assert "invented employers" in low or "invented employer" in low
        assert "fabricated achievements" in low or "fabricated achievement" in low
        assert "minted figures" in low or "minted figure" in low
        assert "cross-document consistency" in low


class TestKeywordListSpecificityRequirement:
    """#282 — both blind reviewers flagged keyword-stuffed prose: paragraph 2
    rendered the claimable half of the keyword ledger as a flat enumerated
    list ("team management, mentoring, cross-functional collaboration,
    engineering standards, technical best practices, and production
    ownership..."). ADR-048 already ranks grounding above coverage;
    specificity must outrank raw coverage too — a term folded into a
    concrete, specific sentence about what was actually built, owned, or
    delegated is worth more than the same term recited in a list.
    """

    def test_system_prompt_requires_specificity_over_keyword_listing(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p

        low = p.lower()
        assert "specificity" in low
        assert "#282" in p or "282" in p

    def test_review_system_prompt_flags_flat_keyword_lists(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p

        low = p.lower()
        assert "flat" in low or "enumerat" in low
        assert "specificity" in low

    def test_refinement_prompt_requires_folding_terms_into_concrete_sentences(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p

        low = p.lower()
        assert "specificity" in low
        assert "built" in low or "owned" in low or "delegated" in low


class TestCoverLetterServiceThreadsPositioningToReviewer:
    """The service assembly (services/cover_letter.py) must put the SAME positioning
    blocks the writer got into the reviewer/corrector source (grounding_source)."""

    @staticmethod
    def _base_mocks(company_name, gap_testimony_profile_extra=None, work_experience=None,
                     signature_stories=None, category_c=None):
        from unittest.mock import MagicMock

        cv_tailored = {
            "contact": {"name": "Max Muster"}, "summary": "QA specialist.",
            "work_history": [{"company": "Acme GmbH", "role": "QA Engineer",
                              "start_date": "2020-01", "end_date": "2022-12",
                              "bullets": ["Built automated regression suites"]}],
            "skills": ["Test Automation"],
        }
        import json as _json
        letter_raw = _json.loads(_json.dumps(_SAMPLE_LETTER))

        mock_cl = MagicMock(); mock_cl.status = "pending"
        mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
        mock_cl.letter_data = {}; mock_cl.section_overrides = {}
        mock_job = MagicMock()
        mock_job.raw_text = "Roche builds diagnostics instruments for regulated healthcare labs."
        mock_job.company_name = company_name
        mock_job.keywords = []
        mock_cv = MagicMock(); mock_cv.tailored_data = cv_tailored
        profile_json = {
            "work_history": cv_tailored["work_history"], "skills": cv_tailored["skills"],
        }
        if work_experience is not None:
            profile_json["work_experience"] = work_experience
        if signature_stories is not None:
            profile_json["signature_stories"] = signature_stories
        mock_profile = MagicMock(); mock_profile.profile_json = profile_json

        mock_gap = None
        if category_c is not None:
            mock_gap = MagicMock()
            mock_gap.category_c = category_c
            mock_gap.keyword_ledger = []

        return mock_cl, mock_job, mock_cv, mock_profile, mock_gap, letter_raw

    @pytest.mark.asyncio
    async def test_grounding_source_carries_positioning_requested_for_company(self):
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cl, mock_job, mock_cv, mock_profile, mock_gap, letter_raw = self._base_mocks(
            company_name="Roche Diagnostics",
        )

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
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls, "review_and_refine was not called"
        src = calls[0]["source"]
        assert "positioning_requested" in src
        assert "company_domain_engagement" in src
        assert "Roche Diagnostics" in src
        assert '"required": true' in src.lower()

    @pytest.mark.asyncio
    async def test_grounding_source_omits_positioning_requested_entries_when_absent(self):
        """No company, no gap testimony, no availability testimony → the
        conditional positioning_requested entries are absent — matching the
        writer's silence-over-invention discipline. #272 Task 2: "closing" is
        NOT conditional (every letter needs a real closing paragraph), so it is
        always present, unlike the other three."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cl, mock_job, mock_cv, mock_profile, mock_gap, letter_raw = self._base_mocks(
            company_name=None,
        )

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
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls
        src = calls[0]["source"]
        import json as _json
        parsed = _json.loads(src)
        assert set(parsed["positioning_requested"].keys()) == {"closing"}
        assert parsed["positioning_requested"]["closing"]["required"] is True

    @pytest.mark.asyncio
    async def test_grounding_source_carries_gap_transfer_testimony_verbatim(self):
        """Reproduces the #255 ground truth: LegalTech denied-concept gap + regulated/
        GxP transfer testimony must reach the reviewer, verbatim, via positioning_requested."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cl, mock_job, mock_cv, mock_profile, mock_gap, letter_raw = self._base_mocks(
            company_name=None,
            signature_stories=[
                {
                    "title": "Bringing GxP rigor to a startup",
                    "challenge": "The team had never worked in LegalTech before.",
                    "mechanism": "I applied my prior regulated-industry GxP discipline to the release process.",
                    "outcome": "We passed our first external audit with zero findings.",
                    "benchmark": None,
                }
            ],
            category_c=["LegalTech domain experience"],
        )

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
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        assert calls
        src = calls[0]["source"]
        assert "gap_transfer_argument" in src
        assert "LegalTech domain experience" in src
        assert "prior regulated-industry GxP discipline" in src  # verbatim testimony

    @pytest.mark.asyncio
    async def test_retry_prompt_and_corrector_system_carry_positioning_when_rejected(self):
        """Full loop: on a rejection, the corrector's retry USER prompt (which re-sends
        `source`) must still carry positioning_requested, and its SYSTEM prompt is the
        preserve-positioning-aware COVER_LETTER_REFINEMENT_PROMPT."""
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cl, mock_job, mock_cv, mock_profile, mock_gap, letter_raw = self._base_mocks(
            company_name="Roche Diagnostics",
        )

        shared_result = MagicMock()
        shared_result.scalar_one_or_none.side_effect = [
            mock_cl, mock_job, mock_cv, mock_profile, mock_gap,
        ]
        mock_db = AsyncMock(); mock_db.execute.return_value = shared_result

        mock_provider = AsyncMock()
        mock_provider.aparse_json.return_value = letter_raw

        # Use the REAL review_and_refine so the actual reviewer_prompt_fn/generator_system
        # wiring from services/cover_letter.py is exercised, with a scripted reviewer verdict.
        from applire.services.reviewer import review_and_refine as real_review_and_refine

        verdicts = iter([
            {"approved": False, "issues": ["missing domain engagement"], "feedback": "add it"},
            {"approved": True, "issues": [], "feedback": ""},
        ])

        async def scripted_aparse_json(prompt, system=None, **kwargs):
            # First call = the writer's generation call (system=SYSTEM_PROMPT from
            # prompts/cover_letter.py). Subsequent calls alternate reviewer/generator
            # inside review_and_refine, distinguished by which system prompt is used.
            from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT
            if system == REVIEW_SYSTEM_PROMPT:
                return next(verdicts)
            return letter_raw

        mock_provider.aparse_json.side_effect = scripted_aparse_json

        with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, \
             patch("applire.services.cover_letter.get_provider", return_value=mock_provider), \
             patch("applire.services.cover_letter.review_and_refine", new=real_review_and_refine), \
             patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2), \
             patch("applire.services.cover_letter.resolve_jd_language", return_value="en"), \
             patch("applire.services.cover_letter.extract_recipient_from_jd",
                   return_value={"name": None}), \
             patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()):
            msl.return_value.__aenter__.return_value = mock_db
            from applire.services.cover_letter import _render_cover_letter_background
            await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)

        # Find the retry (generator refinement) call — its system prompt is
        # COVER_LETTER_REFINEMENT_PROMPT and its user prompt is build_retry_prompt's output.
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT
        retry_calls = [
            c for c in mock_provider.aparse_json.call_args_list
            if c.kwargs.get("system") == COVER_LETTER_REFINEMENT_PROMPT
        ]
        assert retry_calls, "corrector retry call not observed"
        retry_prompt = retry_calls[0].args[0]
        assert "positioning_requested" in retry_prompt
        assert "Roche Diagnostics" in retry_prompt


# ---------------------------------------------------------------------------
# Wave-7 — the pressure valve nobody bounded. Two related run-6 defects:
# #282 (flat keyword-listing) and #283 (fabricated cross-employer fusion) were
# both downstream of a reviewer that could demand an unbounded number of
# absent claimable terms in one round. These tests pin (a) the reviewer's
# per-round coverage demand cap, and (b) the new unsupported-generalization /
# filler check, in the writer, reviewer, and corrector prompts.
# ---------------------------------------------------------------------------


class TestCoverageDemandCap:
    """Rule 1 — the reviewer's coverage check must bound what it demands per
    round to at most two evidenced terms, ranked by fit/JD importance, never
    the full VERIFIED COVERAGE CHECK list at once."""

    @property
    def _prompt(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT
        return REVIEW_SYSTEM_PROMPT.lower()

    def test_caps_demand_at_two_terms_per_round(self):
        low = self._prompt
        assert "at most two" in low
        assert "per round" in low

    def test_ranks_by_fit_weight_or_jd_importance(self):
        low = self._prompt
        assert "fit weight" in low
        assert "jd importance" in low or "importance" in low

    def test_requires_evidence_cited_for_each_demanded_term(self):
        low = self._prompt
        assert "cite the specific profile evidence" in low or "cite" in low
        assert "not a valid demand" in low

    def test_uncited_term_must_be_waived_not_demanded(self):
        low = self._prompt
        assert "waived" in low
        assert "never demanded anyway" in low or "never demanded" in low

    def test_cap_does_not_relax_the_existing_approval_gate(self):
        """The demand cap bounds what is ASKED for per round — it must not be
        confused with a threshold/gating change: approved still stays false
        while any un-waived term exists."""
        low = self._prompt
        assert "approved stays false" in low or "approved=false" in low

    def test_run6_six_term_dump_is_named_as_the_ground_truth(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        assert "Cross-functional" in p or "cross-functional" in p.lower()
        assert "six" in p.lower() or "SIX" in p

    def test_checks_1_through_10_are_not_weakened_by_the_demand_cap(self):
        """Sanity: the demand-cap insertion must not have deleted or diluted
        earlier checks' key vocabulary."""
        low = self._prompt
        assert "invented dates" in low or "invented date" in low
        assert "invented employers" in low or "invented employer" in low
        assert "fabricated achievements" in low or "fabricated achievement" in low
        assert "minted figures" in low or "minted figure" in low
        assert "anchor" in low
        assert "cross-document consistency" in low


class TestUnsupportedGeneralizationCheck:
    """Rule 2 — every body sentence must say something specific about the
    candidate that traces to vault evidence; industry truisms and aspirational
    filler are not claims. Exemptions (greeting/closing, availability line,
    honest-gap paragraph, short connective clauses) must be named explicitly
    so the check cannot be used to strip an honest disclosure."""

    def test_writer_prompt_states_the_rule(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p
        low = p.lower()
        assert "unsupported generalization" in low
        assert "candidate profile" in low

    def test_writer_prompt_gives_the_run6_examples(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p
        assert "rigor end-to-end" in p or "end-to-end" in p
        assert "regulated industries" in p.lower()

    def test_writer_prompt_names_the_exemptions(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT as p
        low = p.lower()
        assert "greeting" in low and "closing" in low
        assert "availability" in low
        assert "connective clause" in low

    def test_reviewer_prompt_states_the_rule(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "unsupported generalization" in low
        assert "filler" in low

    def test_reviewer_prompt_names_the_exemptions(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "explicitly not this check" in low
        assert "greeting" in low
        assert "connective clause" in low

    def test_reviewer_prompt_forbids_using_the_check_against_honesty(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "never use this check" in low
        assert "honest gap" in low or "honesty" in low

    def test_reviewer_prompt_prefers_false_negatives(self):
        """When in doubt, do not flag — a false positive removes honest
        content, which the guardrails call worse than leaving padding in."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "when in doubt, do not flag" in low

    def test_corrector_prompt_states_the_rule(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p
        low = p.lower()
        assert "unsupported generalization" in low

    def test_corrector_prompt_never_shrinks_honest_disclosure(self):
        from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT as p
        low = p.lower()
        assert "honest gap disclosure" in low or "honesty" in low
        assert "worse than leaving" in low

    def test_checks_1_through_10_are_not_weakened_by_check_11(self):
        """Sanity: adding check 11 must not have deleted or diluted the
        earlier checks' key vocabulary."""
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "invented dates" in low or "invented date" in low
        assert "invented employers" in low or "invented employer" in low
        assert "fabricated achievements" in low or "fabricated achievement" in low
        assert "minted figures" in low or "minted figure" in low
        assert "anchor" in low
        assert "cross-document consistency" in low
