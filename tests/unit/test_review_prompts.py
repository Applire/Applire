"""
Smoke tests for review prompt builders — verify they render without error.

No LLM calls. No Docker.

Run:
    pytest tests/unit/test_review_prompts.py -v
"""
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
        )
        assert "Remove duplicate at index 1" in result
        assert "Patch the JSON" in result

    def test_build_retry_prompt_includes_previous_draft(self):
        from applire.prompts.profile_extraction import build_retry_prompt

        previous = {"work_history": [{"company": "Acme"}]}
        result = build_retry_prompt(
            previous_draft=previous,
            feedback="fix it",
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


class TestCVTailoringGeneratorPrompts:
    def test_build_user_prompt_returns_nonempty_string(self):
        from applire.prompts.cv_tailoring import build_user_prompt

        result = build_user_prompt(_SAMPLE_JOB, _SAMPLE_PROFILE, [], [])
        assert isinstance(result, str)
        assert "Backend Engineer" in result

    def test_build_retry_prompt_includes_feedback(self):
        from applire.prompts.cv_tailoring import build_retry_prompt

        result = build_retry_prompt(
            previous_draft=_SAMPLE_TAILORED_CV,
            feedback="Remove fabricated Kubernetes bullet in work_history[0]",
        )
        assert "Remove fabricated Kubernetes bullet" in result
        assert "Patch the JSON" in result

    def test_build_retry_prompt_includes_previous_draft(self):
        from applire.prompts.cv_tailoring import build_retry_prompt

        result = build_retry_prompt(
            previous_draft=_SAMPLE_TAILORED_CV,
            feedback="fix",
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
