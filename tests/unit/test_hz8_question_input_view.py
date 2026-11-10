# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:197 (Hazelnut build 8, WP-I) — what the interview question writer is told about the
candidate, and what the question text may state.

The question writer composed a listed technology and a role bullet into a claim neither
makes ("Your profile shows hands-on work with MuleSoft-based integration platforms") and
read our own earlier question as evidence on the next turn. Replay on the post-merge E2E
input (n=5 per arm, Sonnet 5.5 + gpt-5.6-luna): question text upgrading a listed tag to
work 8/15 -> 2/15 (Sonnet), 6/15 -> 0/15 (luna); B-cluster questions citing a real bullet
unchanged (Sonnet 10/10 -> 10/10, luna 9/10 -> 8/10).
"""
from __future__ import annotations

import json

from applire.prompts.interview import (
    DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    _QUESTION_STATES_ONLY_WHAT_IS_STATED,
    _capped_profile_summary,
    build_denial_probe_question_prompt,
    build_question_prompt,
)

PROFILE = {
    "skills": [
        {"id": "a1", "name": "Kafka", "years_experience": 8, "source": "computed", "status": "confirmed",
         "experience_refs": ["0d40be2c-1618-4886-bd6e-a0d24665f585"]},
        {"id": "a2", "name": "Python", "years_experience": 6, "source": "transcribed"},
        {"id": "a3", "name": "Terraform", "years_experience": 3, "source": "llm_estimated"},
        "Go",
    ],
    "work_experience": [
        {"company": "Nordwerk Logistik GmbH", "role": "Integration Lead", "technologies": ["Kafka", "Python"],
         "responsibilities": ["Ran the central integration platform for four warehouses."], "achievements": []},
    ],
}
CLUSTER = {"id": "c-k8s", "label": "Kubernetes", "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"],
           "jd_context": "The role expects Kubernetes knowledge."}
MESSAGES = [{"role": "assistant", "content": "Your profile shows hands-on Kafka platform work. Any Kubernetes?"},
            {"role": "user", "content": "No Kubernetes so far."}]


def _summary() -> dict:
    return json.loads(_capped_profile_summary(PROFILE))


def test_skills_reach_the_writer_as_names_without_bookkeeping_or_a_computed_span():
    s = _summary()
    assert "skills" not in s
    assert s["skills_listed"] == ["Kafka", "Python (6 years, stated)", "Terraform", "Go"]
    flat = json.dumps(s)
    for leaked in ("0d40be2c", "confirmed", "computed", "llm_estimated", "experience_refs", '"a1"'):
        assert leaked not in flat


def test_role_technologies_are_marked_as_listed_names():
    role = _summary()["work_experience"][0]
    assert role["technologies_listed"] == ["Kafka", "Python"] and "technologies" not in role
    assert role["responsibilities"] == ["Ran the central integration platform for four warehouses."]


def test_question_prompt_carries_the_legend_and_marks_our_own_questions():
    p = build_question_prompt(CLUSTER, PROFILE, MESSAGES, gap_category="C")
    assert "NAMES the profile lists" in p and "Candidate profile summary:\n" not in p
    assert "Applire (question): Your profile shows hands-on Kafka platform work." in p
    assert "Candidate: No Kubernetes so far." in p
    assert "Assistant:" not in p and "User:" not in p
    assert "only Candidate lines are evidence" in p
    assert "Why the role asks (our one-line summary, not the posting's words): The role expects" in p
    assert "JD context:" not in p


def test_the_denial_probe_shares_the_view_and_the_conversation_marking():
    p = build_denial_probe_question_prompt("Kubernetes", "container platforms", PROFILE, MESSAGES)
    assert "NAMES the profile lists" in p and '"skills_listed"' in p
    assert "Applire (question):" in p and "Assistant:" not in p


def test_both_question_system_prompts_govern_what_the_question_text_states():
    for system in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        assert _QUESTION_STATES_ONLY_WHAT_IS_STATED in system
        assert system.index(_QUESTION_STATES_ONLY_WHAT_IS_STATED) < system.index("Output ONLY a valid JSON")
    assert "never as hands-on work" in _QUESTION_STATES_ONLY_WHAT_IS_STATED
    assert "never repeat its premise" in _QUESTION_STATES_ONLY_WHAT_IS_STATED
