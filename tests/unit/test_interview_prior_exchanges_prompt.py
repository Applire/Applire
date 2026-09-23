# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-089 clause 6 (an exception under ADR-058 clause 4) — earlier exchanges on
the SAME cluster (other sessions, or the partial-coverage follow-up) reach the
MODE A prompt as its input view, together with the rule that the question asks
about what those answers left open and that no choice restates them.

Covers, mirroring ``test_interview_quant_elicitation.py``'s own split:

  - build_question_prompt: byte-identical to the pre-ADR-089 call when neither
    ``prior_exchanges`` nor ``follow_up_focus`` is given (prompt stability,
    same guarantee US265 makes for its own params); the earlier-exchanges
    block renders oldest-first with the "already asked" rule; blank pairs
    contribute nothing; ``follow_up_focus`` names the open members INSIDE the
    ADR-084 untrusted-text fence (the focus list rides the posting's own
    cluster/ledger terms, point 25e below).
  - question_generator_with_profile: a follow-up focus goes through the MODE A
    prompt (never the "be more specific" retry prompt) and, going through it,
    never re-carries the US265 quantification or availability instructions
    even when the underlying evidence would otherwise flag them; a cluster
    whose own ``outcome.asked`` record shows it was already asked never gets
    the quantification ask on a later call either, with a control proving the
    fixture is flagged when that record is absent; prior_exchanges reach the
    real MODE A prompt end-to-end.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _quant_flagged_cluster() -> dict:
    """Same shape as test_interview_quant_elicitation.py's
    test_evidenced_figure_free_concept_is_flagged fixture — evidenced,
    figure-free, so detect_unquantified_concepts WOULD flag it."""
    return {
        "id": "cluster-1",
        "label": "Team Leadership",
        "category": "C",
        "gaps": ["CI/CD"],
        "jd_skills": [],
        "jd_context": "",
    }


def _quant_flagged_profile() -> dict:
    return {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": ["Introduced CI/CD practices across the team."],
                "achievements": [],
            }
        ]
    }


def _mode_a_state(cluster: dict) -> dict:
    return {
        "mode": "targeted",
        "critical_gaps": [cluster["id"]],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {cluster["id"]: cluster},
    }


# ---------------------------------------------------------------------------
# Part A — build_question_prompt: stability + rendering (pure, no LLM)
# ---------------------------------------------------------------------------


def test_no_new_params_is_byte_identical_to_the_default_call():
    """The ADR-089 params must reproduce EXACTLY what a pre-ADR-089 call
    produced — same guarantee US265 makes for quant_concepts/include_availability."""
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}
    messages = [{"role": "user", "content": "hi"}]

    baseline = build_question_prompt(cluster, profile, messages)
    explicit_none = build_question_prompt(
        cluster, profile, messages, prior_exchanges=None, follow_up_focus=None
    )
    explicit_empty = build_question_prompt(
        cluster, profile, messages, prior_exchanges=[], follow_up_focus=[]
    )
    assert baseline == explicit_none == explicit_empty


def test_prior_exchanges_render_oldest_first_with_the_already_asked_rule():
    from applire.prompts.interview import (
        _EARLIER_ANSWERS_ARE_NOT_CHOICES,
        build_question_prompt,
    )

    cluster = {"id": "c1", "label": "X", "gaps": ["Y"], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}
    messages = [{"role": "user", "content": "hi"}]
    pairs = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]

    out = build_question_prompt(cluster, profile, messages, prior_exchanges=pairs)

    assert "Earlier exchanges on this cluster" in out
    block1 = "Question: Q1\nAnswer: A1"
    block2 = "Question: Q2\nAnswer: A2"
    assert block1 in out
    assert block2 in out
    # Oldest first: Q1's block precedes Q2's block.
    assert out.index(block1) < out.index(block2)
    # Placed BEFORE "Recent conversation".
    assert out.index("Earlier exchanges on this cluster") < out.index("Recent conversation")
    # The already-asked instruction and the no-restate rule are present.
    assert "This cluster was already asked about" in out
    assert _EARLIER_ANSWERS_ARE_NOT_CHOICES in out
    # Still ends the same way as every MODE A prompt.
    assert out.endswith("Generate the JSON response.")


def test_blank_prior_pairs_contribute_nothing():
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}
    messages = [{"role": "user", "content": "hi"}]

    baseline = build_question_prompt(cluster, profile, messages)
    blank_pairs = [
        {"question": "", "answer": "A-only"},
        {"question": "Q-only", "answer": ""},
        {"question": "   ", "answer": "   "},
        {"question": None, "answer": None},
        {},
    ]
    out = build_question_prompt(cluster, profile, messages, prior_exchanges=blank_pairs)
    assert out == baseline


def test_follow_up_focus_names_the_open_members_inside_the_untrusted_fence():
    from applire.prompts.interview import (
        _EARLIER_ANSWERS_ARE_NOT_CHOICES,
        build_question_prompt,
    )
    from applire.services.untrusted_text import fenced_regions, is_covered

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}

    out = build_question_prompt(cluster, profile, [], follow_up_focus=["Terraform", "Helm"])

    assert "FOLLOW-UP on this cluster" in out
    # The open members reach the model inside the ADR-084 untrusted-text fence
    # — checked exactly the way _assert_covered checks it in
    # test_untrusted_embedding_points.py.
    assert is_covered(out, "Terraform")
    assert is_covered(out, "Helm")
    assert is_covered(out, "Terraform, Helm")
    # is_covered alone is not a sufficient gate here: build_question_prompt
    # ALWAYS emits an earlier, unrelated "GAP CLUSTER" Form-A fence, and
    # is_covered's weaker Form-B "precedence" fallback (SENTINEL appears
    # anywhere before the needle) is satisfied by THAT fence even when the
    # follow-up-focus span itself carries no fence of its own — proven by a
    # mutation run (fence_inline() removed from the focus join) that left
    # is_covered("Terraform")/("Helm") both True. Assert TRUE Form-A
    # containment directly: the joined focus string sits inside one of the
    # prompt's actual fenced regions, not merely after some sentinel.
    assert any("Terraform, Helm" in region for region in fenced_regions(out))
    # The no-restate sentence rides with the follow-up-focus lead too.
    assert _EARLIER_ANSWERS_ARE_NOT_CHOICES in out


# ---------------------------------------------------------------------------
# Part B — question_generator_with_profile: MODE A wiring (async, mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_up_focus_never_carries_the_quantification_or_availability_ask():
    """A follow-up focus goes through the MODE A prompt (never the "be more
    specific" retry prompt) and, once there, must never re-carry the US265
    quantification/availability asks — even when the cluster's own evidence
    would otherwise flag the quantification one."""
    from applire.services.interview_graph import question_generator_with_profile

    cluster = _quant_flagged_cluster()
    profile = _quant_flagged_profile()
    state = _mode_a_state(cluster)

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"question": "Tell me about CI/CD.", "choices": None, "approved": True}
    )
    provider.acomplete = AsyncMock()

    await question_generator_with_profile(
        state,
        profile,
        provider,
        gap_category="C",
        follow_up_focus=["CI/CD"],
        include_availability=True,
    )

    first_prompt = provider.aparse_json.call_args_list[0].args[0]
    assert "FOLLOW-UP on this cluster" in first_prompt
    assert "quantif" not in first_prompt.lower()
    assert "Availability opportunity" not in first_prompt
    # The focus routes through aparse_json (MODE A), never the acomplete-based
    # "be more specific" retry prompt.
    provider.acomplete.assert_not_called()


@pytest.mark.asyncio
async def test_a_cluster_asked_before_never_gets_the_quantification_ask_again():
    from applire.services.interview_graph import question_generator_with_profile

    profile = _quant_flagged_profile()

    asked_cluster = dict(_quant_flagged_cluster())
    asked_cluster["outcome"] = {"asked": 1, "covered": [], "declined": [], "session_ids": []}
    state_asked = _mode_a_state(asked_cluster)

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"question": "Tell me about CI/CD.", "choices": None, "approved": True}
    )
    await question_generator_with_profile(state_asked, profile, provider, gap_category="C")
    first_prompt = provider.aparse_json.call_args_list[0].args[0]
    assert "quantif" not in first_prompt.lower()

    # Control: the SAME cluster WITHOUT an outcome record must still be
    # flagged — proving the fixture actually triggers
    # detect_unquantified_concepts and the gating above is doing the work.
    fresh_cluster = _quant_flagged_cluster()
    state_fresh = _mode_a_state(fresh_cluster)
    control_provider = MagicMock()
    control_provider.aparse_json = AsyncMock(
        return_value={"question": "Tell me about CI/CD.", "choices": None, "approved": True}
    )
    await question_generator_with_profile(state_fresh, profile, control_provider, gap_category="C")
    control_prompt = control_provider.aparse_json.call_args_list[0].args[0]
    assert "quantif" in control_prompt.lower()


@pytest.mark.asyncio
async def test_prior_exchanges_reach_the_mode_a_prompt():
    from applire.services.interview_graph import question_generator_with_profile

    cluster = {"id": "cluster-x", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}
    state = _mode_a_state(cluster)

    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"question": "Q", "choices": None, "approved": True}
    )

    await question_generator_with_profile(
        state,
        profile,
        provider,
        gap_category="C",
        prior_exchanges=[{"question": "Q-old", "answer": "A-old"}],
    )

    first_prompt = provider.aparse_json.call_args_list[0].args[0]
    assert "Q-old" in first_prompt
    assert "A-old" in first_prompt
