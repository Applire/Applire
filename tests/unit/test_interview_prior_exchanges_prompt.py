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


def test_a_declined_member_is_never_in_the_ask_or_denial_scope():
    """ADR-089 clause 3 + Lead A's note: `constituent_evidence` flags EVERY
    member, so a DECLINED member would land in "Still unevidenced … ask openly"
    and in the denial-choice scope. Only OPEN members (`gaps`) may be asked; a
    declined one is named once, as declined, and never asked again."""
    from applire.prompts.interview import build_question_prompt

    cluster = {
        "id": "cluster-infra", "label": "Cloud infrastructure", "category": "C",
        "gaps": ["Helm", "Pulumi"], "jd_skills": [], "jd_context": "",
        "outcome": {"asked": 1, "covered": ["Kubernetes"], "declined": ["Terraform"],
                    "session_ids": []},
    }
    profile = {"skills": [{"name": "Kubernetes"}], "work_experience": []}
    out = build_question_prompt(cluster, profile, [], gap_category="C")

    ask_line = next(line for line in out.splitlines() if line.startswith("Gap type:"))
    assert "Still unevidenced: Helm, Pulumi." in ask_line
    assert "Terraform" not in ask_line
    assert "evidences: Kubernetes." in ask_line, "a covered member stays 'never re-question'"
    denial_line = next(line for line in out.splitlines() if "only valid scope for a denial" in line)
    assert "Terraform" not in denial_line
    assert "Helm, Pulumi" in denial_line
    declined_line = next(line for line in out.splitlines() if line.startswith("Already declined"))
    assert "Terraform" in declined_line and "never ask about these again" in declined_line


def test_a_follow_up_focus_member_is_never_listed_as_do_not_requestion():
    """A focus member the profile happens to mention must not be told
    "never re-question the evidenced ones" in the same prompt that asks about
    it by name (step-3 contradiction)."""
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c", "label": "Obs", "category": "C", "gaps": ["Grafana", "SLOs"],
               "jd_skills": [], "jd_context": "",
               "outcome": {"asked": 1, "covered": ["Prometheus"], "declined": [], "session_ids": []}}
    profile = {"skills": [{"name": "Prometheus"}, {"name": "Grafana"}], "work_experience": []}
    out = build_question_prompt(cluster, profile, [], gap_category="C",
                                follow_up_focus=["Grafana", "SLOs"])
    ask_line = next(line for line in out.splitlines() if line.startswith("Gap type:"))
    assert "evidences: Prometheus." in ask_line
    assert "Grafana" not in ask_line.split("Still unevidenced")[0]


# ---------------------------------------------------------------------------
# Ruling M-1 — the drafting call judges what the answer covered in other words
# ---------------------------------------------------------------------------


def test_follow_up_prompt_asks_for_the_covered_by_answer_judgement():
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    out = build_question_prompt(cluster, {"skills": [], "work_experience": []}, [],
                                follow_up_focus=["Terraform", "Helm"])

    assert '"covered_by_answer"' in out
    assert "IN OTHER WORDS" in out
    # The rule's two sides — what counts, and the level line that keeps a
    # related or lower-level answer from reading as covered.
    assert "another language" in out
    assert "clearly lower level" in out
    assert 'set "question" to ""' in out


def _judging_provider(first_reply: dict) -> MagicMock:
    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=[first_reply, {"approved": True}])
    provider.acomplete = AsyncMock()
    return provider


def _focus_state() -> dict:
    cluster = {"id": "c1", "label": "Infrastructure as code", "category": "C",
               "gaps": ["Terraform", "Helm"], "jd_skills": [], "jd_context": ""}
    return _mode_a_state(cluster)


@pytest.mark.asyncio
async def test_all_candidates_covered_returns_no_question_and_skips_the_language_review():
    from applire.services.interview_graph import question_generator_with_profile

    provider = _judging_provider({
        "question": "Tell me about Terraform and Helm.", "choices": None,
        "covered_by_answer": ["Terraform", "helm "],
    })
    out = await question_generator_with_profile(
        _focus_state(), {"skills": [], "work_experience": []}, provider,
        gap_category="C", follow_up_focus=["Terraform", "Helm"], lang="en",
    )

    assert out["question"] == ""
    assert out["follow_up_remaining"] == []
    assert out["covered_by_answer"] == ["Terraform", "Helm"]
    assert provider.aparse_json.await_count == 1, "no language review for a question nobody sees"


@pytest.mark.asyncio
async def test_a_partly_covered_focus_keeps_the_rest_in_focus_order():
    from applire.services.interview_graph import question_generator_with_profile

    provider = _judging_provider({
        "question": "And Helm — have you packaged charts?", "choices": None,
        "covered_by_answer": ["TERRAFORM"],
    })
    out = await question_generator_with_profile(
        _focus_state(), {"skills": [], "work_experience": []}, provider,
        gap_category="C", follow_up_focus=["Terraform", "Helm"], lang="en",
    )

    assert out["question"] == "And Helm — have you packaged charts?"
    assert out["covered_by_answer"] == ["Terraform"]
    assert out["follow_up_remaining"] == ["Helm"]


@pytest.mark.parametrize(
    "returned",
    [["Kubernetes"], "Terraform", None, [None, 3, ""], {"Terraform": True}],
    ids=["name-outside-focus", "bare-string", "missing", "junk-items", "object"],
)
def test_only_focus_names_in_a_list_count_as_covered(returned):
    """Code computes facts about the model's output, never a judgement of its
    own: a name outside the focus, or a field that is not a list of strings,
    leaves the literal focus unchanged — the behaviour before ruling M-1."""
    from applire.services.interview_graph import split_follow_up_focus

    assert split_follow_up_focus(["Terraform", "Helm"], returned) == ([], ["Terraform", "Helm"])


def test_no_follow_up_focus_carries_no_split_keys():
    """A cluster's opening question is untouched: the split keys ride only on
    a follow-up."""
    import asyncio

    from applire.services.interview_graph import question_generator_with_profile

    provider = _judging_provider({"question": "Tell me about Terraform.", "choices": None})
    out = asyncio.run(question_generator_with_profile(
        _focus_state(), {"skills": [], "work_experience": []}, provider,
        gap_category="C", lang="en",
    ))
    assert "follow_up_remaining" not in out and "covered_by_answer" not in out
