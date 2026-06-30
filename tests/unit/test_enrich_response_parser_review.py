"""
Unit tests for ResponseParser reviewer prompts (profile enrichment, Mode C).

Verifies:
1. Prompt builders render correctly and include expected content.
2. review_and_refine approves a good draft on the first review call.
3. review_and_refine retries when the reviewer rejects the draft.
4. review_and_refine returns the last draft when max_retries is exhausted.

No Docker, no DB, no real LLM.

Run:
    pytest tests/unit/test_enrich_response_parser_review.py -v
"""
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.review_interview_response import (
    RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
    build_response_parser_review_prompt,
)
from applire.services.reviewer import review_and_refine

# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------

_GAP = "Experience with Kubernetes orchestration"
_QUESTION = "Can you describe a project where you deployed workloads with Kubernetes?"
_ANSWER = (
    "Yes, at Acme GmbH I managed a 12-node cluster on AWS EKS. "
    "We ran 50+ microservices and I personally set up the Helm charts."
)
_GOOD_DRAFT = {
    "skills_to_add": ["Kubernetes", "AWS EKS", "Helm"],
    "work_history_to_add": [
        {
            "company": "Acme GmbH",
            "role": None,
            "start_date": None,
            "end_date": None,
            "bullets": [
                "Managed 12-node Kubernetes cluster on AWS EKS",
                "Set up Helm charts for 50+ microservices",
            ],
        }
    ],
    "certifications_to_add": [],
    "languages_to_add": [],
    "education_to_add": [],
    "gap_resolution": "full",
    "follow_up_hint": None,
    "gaps_also_addressed": [],
}


# ---------------------------------------------------------------------------
# Prompt builder smoke tests
# ---------------------------------------------------------------------------


class TestResponseParserReviewPromptBuilder:
    def test_system_prompt_is_nonempty_string(self):
        assert isinstance(RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT, str)
        assert len(RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT) > 100

    def test_system_prompt_references_approved_field(self):
        assert "approved" in RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT

    def test_build_prompt_returns_nonempty_string(self):
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, _GOOD_DRAFT)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_build_prompt_includes_gap(self):
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, _GOOD_DRAFT)
        assert _GAP in result

    def test_build_prompt_includes_question(self):
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, _GOOD_DRAFT)
        assert _QUESTION in result

    def test_build_prompt_includes_answer(self):
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, _GOOD_DRAFT)
        assert "Acme GmbH" in result

    def test_build_prompt_includes_serialised_draft(self):
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, _GOOD_DRAFT)
        assert "Kubernetes" in result
        assert "Helm" in result

    def test_build_prompt_serialises_unicode_correctly(self):
        """Ensure ensure_ascii=False — non-ASCII in draft must survive serialisation."""
        draft_with_unicode = {**_GOOD_DRAFT, "skills_to_add": ["Führungskompetenz"]}
        result = build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, draft_with_unicode)
        assert "Führungskompetenz" in result


# ---------------------------------------------------------------------------
# review_and_refine integration with response-parser prompts
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider():
    return AsyncMock()


def _make_reviewer_fn(gap: str, question: str, answer: str):
    """Factory: returns a reviewer_prompt_fn(source, draft) -> str closure."""

    def reviewer_prompt_fn(source: str, draft: dict) -> str:
        # source is unused here — gap/question/answer are captured in closure
        return build_response_parser_review_prompt(gap, question, answer, draft)

    return reviewer_prompt_fn


def _make_generator_fn():
    """Simple generator retry prompt that includes feedback."""

    def generator_prompt_fn(previous_draft: dict, feedback: str, source: str) -> str:
        return (
            f"Previous extraction had issues: {feedback}\n\n"
            f"Source answer:\n{source}\n\n"
            f"Previous draft:\n{json.dumps(previous_draft, ensure_ascii=False)}\n\n"
            "Re-extract the structured profile data, fixing the issues above."
        )

    return generator_prompt_fn


@pytest.mark.asyncio
async def test_approves_good_draft_on_first_review(mock_provider):
    """review_and_refine returns the initial draft when the reviewer approves immediately."""
    mock_provider.aparse_json.return_value = {
        "approved": True,
        "issues": [],
        "feedback": "",
    }

    result = await review_and_refine(
        source=_ANSWER,
        draft=_GOOD_DRAFT,
        generator_prompt_fn=_make_generator_fn(),
        generator_system="Extract structured profile data.",
        reviewer_prompt_fn=_make_reviewer_fn(_GAP, _QUESTION, _ANSWER),
        reviewer_system=RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
        provider=mock_provider,
        max_retries=2,
    )

    assert result == _GOOD_DRAFT
    # Only one LLM call: the initial reviewer — no generator retry
    assert mock_provider.aparse_json.call_count == 1


@pytest.mark.asyncio
async def test_retries_when_reviewer_rejects(mock_provider):
    """review_and_refine issues a generator retry when the reviewer rejects the draft."""
    original_draft = {
        **_GOOD_DRAFT,
        "work_history_to_add": [
            {
                "company": "Acme GmbH",
                "role": None,
                "start_date": None,
                "end_date": None,
                "bullets": ["Worked with Kubernetes"],  # too vague
            }
        ],
    }
    revised_draft = {
        **_GOOD_DRAFT,
        "work_history_to_add": [
            {
                "company": "Acme GmbH",
                "role": None,
                "start_date": None,
                "end_date": None,
                "bullets": [
                    "Managed 12-node Kubernetes cluster on AWS EKS",
                    "Set up Helm charts for 50+ microservices",
                ],
            }
        ],
    }

    mock_provider.aparse_json.side_effect = [
        # Reviewer call 1: reject — bullet is too vague
        {
            "approved": False,
            "issues": ["Bullet 'Worked with Kubernetes' is too vague — user stated specific cluster size and tooling"],
            "feedback": "Replace vague bullet with specific details: 12-node EKS cluster and Helm charts",
        },
        # Generator retry 1: returns revised draft
        revised_draft,
        # Reviewer call 2: approve
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source=_ANSWER,
        draft=original_draft,
        generator_prompt_fn=_make_generator_fn(),
        generator_system="Extract structured profile data.",
        reviewer_prompt_fn=_make_reviewer_fn(_GAP, _QUESTION, _ANSWER),
        reviewer_system=RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
        provider=mock_provider,
        max_retries=2,
    )

    assert result == revised_draft
    assert mock_provider.aparse_json.call_count == 3


@pytest.mark.asyncio
async def test_returns_last_draft_when_max_retries_exhausted(mock_provider, caplog):
    """review_and_refine returns the last generated draft and logs a warning when retries run out."""
    original_draft = {**_GOOD_DRAFT, "skills_to_add": ["Kubernetes", "Docker Swarm"]}
    retry1 = {**_GOOD_DRAFT, "skills_to_add": ["Kubernetes", "AWS EKS"]}
    retry2 = {**_GOOD_DRAFT, "skills_to_add": ["Kubernetes", "AWS EKS", "Helm"]}

    mock_provider.aparse_json.side_effect = [
        # attempt 0: reviewer rejects
        {
            "approved": False,
            "issues": ["Docker Swarm not mentioned in answer"],
            "feedback": "Remove Docker Swarm — user did not mention it",
        },
        # attempt 0: generator retry → retry1
        retry1,
        # attempt 1: reviewer rejects again
        {
            "approved": False,
            "issues": ["Missing Helm which was explicitly mentioned"],
            "feedback": "Add Helm to skills_to_add",
        },
        # attempt 1: generator retry → retry2
        retry2,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source=_ANSWER,
            draft=original_draft,
            generator_prompt_fn=_make_generator_fn(),
            generator_system="Extract structured profile data.",
            reviewer_prompt_fn=_make_reviewer_fn(_GAP, _QUESTION, _ANSWER),
            reviewer_system=RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
            provider=mock_provider,
            max_retries=2,
        )

    assert result == retry2
    assert mock_provider.aparse_json.call_count == 4
    assert "exhausted" in caplog.text


@pytest.mark.asyncio
async def test_reviewer_receives_current_draft_not_original(mock_provider):
    """After a generator retry, the reviewer must receive the revised draft, not the original."""
    original_draft = {"skills_to_add": ["Kubernetes"], "gap_resolution": "partial"}
    revised_draft = {"skills_to_add": ["Kubernetes", "AWS EKS", "Helm"], "gap_resolution": "full"}

    reviewer_calls: list[dict] = []

    def capturing_reviewer(source: str, draft: dict) -> str:
        reviewer_calls.append(draft)
        return build_response_parser_review_prompt(_GAP, _QUESTION, _ANSWER, draft)

    mock_provider.aparse_json.side_effect = [
        # Reviewer call 1: reject original
        {"approved": False, "issues": ["incomplete"], "feedback": "Add EKS and Helm"},
        # Generator retry: produce revised
        revised_draft,
        # Reviewer call 2: approve revised
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source=_ANSWER,
        draft=original_draft,
        generator_prompt_fn=_make_generator_fn(),
        generator_system="Extract structured profile data.",
        reviewer_prompt_fn=capturing_reviewer,
        reviewer_system=RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
        provider=mock_provider,
        max_retries=2,
    )

    assert result == revised_draft
    assert len(reviewer_calls) == 2
    assert reviewer_calls[0] == original_draft
    assert reviewer_calls[1] == revised_draft


@pytest.mark.asyncio
async def test_generator_retry_receives_feedback_from_reviewer(mock_provider):
    """The generator retry prompt must include the reviewer's feedback string."""
    original_draft = {"skills_to_add": ["Kubernetes"], "gap_resolution": "partial"}
    revised_draft = {"skills_to_add": ["Kubernetes", "AWS EKS"], "gap_resolution": "full"}

    generator_calls: list[tuple] = []

    def capturing_generator(previous_draft: dict, feedback: str, source: str) -> str:
        generator_calls.append((previous_draft, feedback))
        return f"retry prompt with feedback: {feedback}"

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["missing EKS"], "feedback": "Add AWS EKS from user's answer"},
        revised_draft,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    await review_and_refine(
        source=_ANSWER,
        draft=original_draft,
        generator_prompt_fn=capturing_generator,
        generator_system="Extract structured profile data.",
        reviewer_prompt_fn=_make_reviewer_fn(_GAP, _QUESTION, _ANSWER),
        reviewer_system=RESPONSE_PARSER_REVIEW_SYSTEM_PROMPT,
        provider=mock_provider,
        max_retries=2,
    )

    assert len(generator_calls) == 1
    prev_draft_arg, feedback_arg = generator_calls[0]
    assert prev_draft_arg == original_draft
    assert feedback_arg == "Add AWS EKS from user's answer"
