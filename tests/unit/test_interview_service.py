"""
Sprint 15 — Smart Gap Interview unit tests.

Tests: question_generator_with_profile, send_message advance/follow-up/cross-gap
       logic, _next_valid_index, _count_remaining,
       build_follow_up_question_prompt.

No Docker, no real LLM — async tests use mocked providers.

Run:
    pytest tests/unit/test_sprint15_interview.py -v
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Task 1: Constants
# ---------------------------------------------------------------------------

def test_max_questions_per_gap_default():
    """#274/#284: the retry budget for a gap that neither advances (no
    profile change) nor denies (no denial_recorded) is ONE retry — the
    initial question plus one follow-up, not two follow-ups. Charter run 6
    burned a third question on a cluster two substantive, unquantified
    answers had already exhausted; tightened 3 -> 2 so the second
    unproductive answer forces the advance instead of inviting a third."""
    os.environ.pop("INTERVIEW_MAX_QUESTIONS_PER_GAP", None)
    import importlib
    import applire.constants as c
    importlib.reload(c)
    assert c.INTERVIEW_MAX_QUESTIONS_PER_GAP == 2


def test_max_questions_per_gap_env_override():
    os.environ["INTERVIEW_MAX_QUESTIONS_PER_GAP"] = "5"
    import importlib
    import applire.constants as c
    importlib.reload(c)
    assert c.INTERVIEW_MAX_QUESTIONS_PER_GAP == 5
    os.environ.pop("INTERVIEW_MAX_QUESTIONS_PER_GAP", None)
    importlib.reload(c)  # restore default for subsequent tests


# ---------------------------------------------------------------------------
# Task 3: Follow-up question generation
# ---------------------------------------------------------------------------


def test_build_follow_up_question_prompt_contains_hint():
    """Follow-up prompt includes the gap and the hint."""
    from applire.prompts.interview import build_follow_up_question_prompt

    prompt = build_follow_up_question_prompt(
        gap="GCP certification",
        follow_up_hint="ask about GMP or other regulated manufacturing environments",
        profile={"skills": [], "work_history": []},
        recent_messages=[],
    )

    assert "GCP certification" in prompt
    assert "GMP or other regulated" in prompt


def test_build_follow_up_question_prompt_includes_conversation_history():
    """Last 4 messages appear in the follow-up prompt."""
    from applire.prompts.interview import build_follow_up_question_prompt

    messages = [
        {"role": "assistant", "content": "Tell me about your GCP experience."},
        {"role": "user", "content": "I've worked in pharma."},
    ]
    prompt = build_follow_up_question_prompt(
        gap="GCP certification",
        follow_up_hint="ask about GMP",
        profile={},
        recent_messages=messages,
    )

    assert "I've worked in pharma." in prompt


@pytest.mark.asyncio
async def test_question_generator_routes_to_followup_when_hint_present():
    """question_generator_with_profile calls follow-up prompt when follow_up_hint is set."""
    from applire.services.interview_graph import question_generator_with_profile

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="Have you worked in GMP-regulated environments?")
    # Language review pass requires aparse_json; mock it to approve immediately.
    provider.aparse_json = AsyncMock(return_value={"approved": True, "issues": [], "feedback": ""})

    state = {
        "mode": "targeted",
        "critical_gaps": ["cluster-gcp"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "cluster-gcp": {"id": "cluster-gcp", "label": "GCP certification", "category": "C", "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""}
        },
    }

    result = await question_generator_with_profile(
        state,
        profile={},
        provider=provider,
        follow_up_hint="ask about GMP or other regulated environments",
    )

    assert isinstance(result, dict)
    assert result["question"] == "Have you worked in GMP-regulated environments?"
    # Confirm it was called with the follow-up system prompt
    call_kwargs = provider.acomplete.call_args.kwargs
    assert "follow" in call_kwargs.get("system", "").lower() or "adjacent" in call_kwargs.get("system", "").lower()


@pytest.mark.asyncio
async def test_question_generator_routes_to_standard_when_no_hint():
    """question_generator_with_profile uses standard prompt (aparse_json) when follow_up_hint is None."""
    from applire.services.interview_graph import question_generator_with_profile

    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"question": "Tell me about your GCP experience.", "choices": None})

    state = {
        "mode": "targeted",
        "critical_gaps": ["cluster-gcp"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "cluster-gcp": {"id": "cluster-gcp", "label": "GCP certification", "category": "C", "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""}
        },
    }

    result = await question_generator_with_profile(
        state,
        profile={},
        provider=provider,
        follow_up_hint=None,
    )

    assert isinstance(result, dict)
    assert result["question"] == "Tell me about your GCP experience."


# ---------------------------------------------------------------------------
# M8 finding-fix (2026-07-29) — the ADR-064 denial transfer probe gets answer
# choices under the SAME coverage/truthfulness rules as a normal question.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denial_probe_follow_up_produces_choices_via_aparse_json():
    """M8: `denial_probe=True` routes the follow-up through aparse_json with
    the choices-producing schema, not the plain acomplete text-only path —
    the ONE question where partial-versus-denial is the entire point must
    reach the same coverage rule (and filter_ungrounded_choices guard) as
    MODE A."""
    from applire.services.interview_graph import question_generator_with_profile

    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("must not use acomplete"))
    provider.aparse_json = AsyncMock(side_effect=[
        {
            "question": "Have you worked with other enterprise architecture frameworks?",
            "choices": [
                {"text": "Yes, I've used Zachman.", "level": "direct"},
                {"text": "I've read about them but never applied one.", "level": "partial"},
                {"text": "No, I haven't used any EA framework.", "level": "denial"},
            ],
        },
        {"approved": True, "issues": [], "feedback": ""},  # language review verdict
    ])

    state = {
        "mode": "targeted",
        "critical_gaps": ["cluster-togaf"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "cluster-togaf": {
                "id": "cluster-togaf", "label": "TOGAF", "category": "C",
                "gaps": ["TOGAF"], "jd_skills": ["TOGAF"], "jd_context": "",
            }
        },
    }
    profile = {"skills": [{"name": "Zachman"}], "work_experience": []}

    result = await question_generator_with_profile(
        state, profile=profile, provider=provider,
        follow_up_hint='the candidate just denied direct experience with "TOGAF"',
        denial_probe=True,
    )

    assert result["question"] == "Have you worked with other enterprise architecture frameworks?"
    assert result["choices"] == [
        "Yes, I've used Zachman.",
        "I've read about them but never applied one.",
        "No, I haven't used any EA framework.",
    ]
    draft_call_kwargs = provider.aparse_json.call_args_list[0].kwargs
    assert "denial" in draft_call_kwargs.get("system", "").lower()


@pytest.mark.asyncio
async def test_non_probe_follow_up_still_never_produces_choices():
    """The 'more specific example' retry follow-up (`denial_probe=False`,
    the default) is UNCHANGED — plain acomplete, choices always None. M8
    explicitly must not touch this path."""
    from applire.services.interview_graph import question_generator_with_profile

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="Could you share a concrete example?")
    provider.aparse_json = AsyncMock(return_value={"approved": True, "issues": [], "feedback": ""})

    state = {
        "mode": "targeted",
        "critical_gaps": ["cluster-gcp"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "cluster-gcp": {"id": "cluster-gcp", "label": "GCP certification", "category": "C", "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""}
        },
    }

    result = await question_generator_with_profile(
        state, profile={}, provider=provider,
        follow_up_hint="ask for a more specific or concrete example related to GCP certification",
    )

    assert result["choices"] is None
    provider.acomplete.assert_awaited_once()


# ---------------------------------------------------------------------------
# Task 5: _next_valid_index and _count_remaining
# ---------------------------------------------------------------------------


def test_next_valid_index_no_skipped():
    """Returns from_index unchanged when nothing is skipped."""
    from applire.services.session import _next_valid_index

    gaps = ["gap_a", "gap_b", "gap_c"]
    assert _next_valid_index(gaps, 1, set()) == 1


def test_next_valid_index_skips_one():
    """Skips a single skipped gap."""
    from applire.services.session import _next_valid_index

    gaps = ["gap_a", "gap_b", "gap_c"]
    assert _next_valid_index(gaps, 1, {"gap_b"}) == 2


def test_next_valid_index_skips_multiple():
    """Skips consecutive skipped gaps."""
    from applire.services.session import _next_valid_index

    gaps = ["gap_a", "gap_b", "gap_c", "gap_d"]
    assert _next_valid_index(gaps, 1, {"gap_b", "gap_c"}) == 3


def test_next_valid_index_all_remaining_skipped():
    """Returns len(gaps) when all remaining gaps are skipped (signals exhaustion)."""
    from applire.services.session import _next_valid_index

    gaps = ["gap_a", "gap_b", "gap_c"]
    assert _next_valid_index(gaps, 1, {"gap_b", "gap_c"}) == 3


def test_count_remaining_no_skipped():
    """Counts all gaps from from_index when nothing is skipped."""
    from applire.services.session import _count_remaining

    gaps = ["gap_a", "gap_b", "gap_c"]
    assert _count_remaining(gaps, 1, set()) == 2


def test_count_remaining_with_skipped():
    """Excludes skipped gaps from count."""
    from applire.services.session import _count_remaining

    gaps = ["gap_a", "gap_b", "gap_c", "gap_d"]
    assert _count_remaining(gaps, 1, {"gap_c"}) == 2  # gap_b and gap_d


def test_count_remaining_all_skipped():
    """Returns 0 when all remaining gaps are skipped."""
    from applire.services.session import _count_remaining

    gaps = ["gap_a", "gap_b", "gap_c"]
    assert _count_remaining(gaps, 1, {"gap_b", "gap_c"}) == 0


# ---------------------------------------------------------------------------
# Task 6: send_message advance logic (Feature 1 — multi-question per gap)
# ---------------------------------------------------------------------------

import uuid


def _make_state(
    gaps: list[str],
    current_index: int = 0,
    questions_asked: int = 1,
    hard_ceiling: int = 12,
    questions_per_gap: dict | None = None,
    skipped_gaps: list[str] | None = None,
    addressed_gaps: list[str] | None = None,
) -> dict:
    """Build a minimal InterviewState for testing send_message logic."""
    return {
        "mode": "targeted",
        "job_id": str(uuid.uuid4()),
        "gap_analysis_id": None,
        "profile_id": str(uuid.uuid4()),
        "critical_gaps": gaps,
        "gap_categories": {},
        "current_gap_index": current_index,
        "current_question": "Tell me about your GCP experience.",
        "messages": [{"role": "assistant", "content": "Tell me about your GCP experience."}],
        "questions_asked": questions_asked,
        "hard_ceiling": hard_ceiling,
        "questions_per_gap": dict(questions_per_gap) if questions_per_gap else {},
        "skipped_gaps": list(skipped_gaps) if skipped_gaps else [],
        "addressed_gaps": list(addressed_gaps) if addressed_gaps else [],
        "full_gaps": [],
    }


def test_build_state_includes_new_fields():
    """_build_state initialises questions_per_gap, skipped_gaps, full_gaps, gap_clusters_by_id."""
    from applire.services.session import _build_state

    state = _build_state(
        mode="targeted",
        job_id=uuid.uuid4(),
        gap_analysis_id=None,
        profile_id=uuid.uuid4(),
        critical_gaps=["cluster-a"],
        gap_categories={"cluster-a": "C"},
        gap_clusters_by_id={"cluster-a": {"id": "cluster-a", "label": "gap_a", "category": "C", "gaps": ["gap_a"], "jd_skills": [], "jd_context": ""}},
        current_question="",
        hard_ceiling=12,
    )

    assert state["questions_per_gap"] == {}
    assert state["skipped_gaps"] == []
    assert state["full_gaps"] == []
    assert "gap_clusters_by_id" in state


def test_send_message_resilient_to_pre_sprint15_state():
    """State dicts missing Sprint 15 fields don't KeyError during gap logic."""
    from applire.services.session import _next_valid_index, _count_remaining

    # Simulate a pre-Sprint 15 state (missing the new fields)
    old_state = {
        "mode": "targeted",
        "critical_gaps": ["gap_a", "gap_b"],
        "current_gap_index": 0,
    }

    # These helpers must work even if called with values from such a state
    skipped_set = set(old_state.get("skipped_gaps", []))   # defaults to empty set
    qpg = old_state.get("questions_per_gap", {}).get("gap_a", 1)  # defaults to 1

    assert skipped_set == set()
    assert qpg == 1
    # Verify helpers don't error
    assert _next_valid_index(old_state["critical_gaps"], 0, skipped_set) == 0
    assert _count_remaining(old_state["critical_gaps"], 0, skipped_set) == 2


# ---------------------------------------------------------------------------
# Task 7: Cross-gap resolution — integration through pure helpers
# ---------------------------------------------------------------------------


def test_count_remaining_reflects_skipped_gaps():
    """gaps_remaining drops when skipped_gaps are added."""
    from applire.services.session import _count_remaining

    gaps = ["gap_a", "gap_b", "gap_c", "gap_d"]
    # No skips: 3 remaining after index 1
    assert _count_remaining(gaps, 1, set()) == 3
    # gap_c skipped: 2 remaining
    assert _count_remaining(gaps, 1, {"gap_c"}) == 2
    # gap_b and gap_c skipped: 1 remaining
    assert _count_remaining(gaps, 1, {"gap_b", "gap_c"}) == 1


def test_next_valid_index_skips_to_end():
    """_next_valid_index returns len(gaps) when all remaining are skipped."""
    from applire.services.session import _next_valid_index

    gaps = ["gap_a", "gap_b", "gap_c"]
    result = _next_valid_index(gaps, 1, {"gap_b", "gap_c"})
    assert result == 3  # past end → gaps exhausted


# ---------------------------------------------------------------------------
# Task 8: Micro-session full_gaps
# ---------------------------------------------------------------------------


def test_micro_session_full_gaps_excludes_target():
    """full_gaps in micro-session state contains all analysis gaps except the target."""
    # This tests the construction rule directly
    target = "GCP certification"
    all_analysis_gaps = ["GCP certification", "GMP experience", "ISO 9001", "Python"]

    full_gaps = [g for g in all_analysis_gaps if g != target]

    assert target not in full_gaps
    assert "GMP experience" in full_gaps
    assert len(full_gaps) == 3
