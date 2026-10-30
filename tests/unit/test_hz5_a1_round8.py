# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5, round 8 (D-30, D-3).

The prompt pins keep each rule in the call it was measured in (gpt-6-luna,
thinking off, n >= 5 per arm — numbers in the PR body). The session tests pin
ruling A1-R8-1 = B: a turn that recorded a no earns no partial-coverage
follow-up, so the same limit is never asked twice nor listed twice.
"""

from applire.prompts.profile_decisions import ASPIRATION_SYSTEM
from applire.prompts.review_question_language import (
    QUESTION_LANGUAGE_REFINEMENT_PROMPT,
    QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT,
)


# D-30 — fixed technical terms stay as the posting spells them.
def test_the_language_review_names_what_is_not_a_mismatch():
    s = QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT
    assert "WHAT IS NOT A MISMATCH" in s
    for term in ('"Cloud"', '"Copilot"', '"API"', '"Product Owner"', '"Cloud-Dienste"'):
        assert term in s
    # the rule sits before the blocking definition it narrows
    assert s.index("WHAT IS NOT A MISMATCH") < s.index("WHAT IS BLOCKING IN THIS PASS")


def test_the_language_rewrite_translates_only_what_the_feedback_names():
    s = QUESTION_LANGUAGE_REFINEMENT_PROMPT
    assert "Translate ONLY the phrases the feedback names" in s
    assert "even \\\nwhen the feedback asks for it" in s or "when the feedback asks for it" in s


# D-3 — ongoing work on a thing that exists is not an aspiration.
def test_the_aspiration_judge_reads_ongoing_work_on_an_existing_thing_as_a_duty():
    s = ASPIRATION_SYSTEM
    never = s.index("NEVER listed:")
    rule = s.index("building, developing or improving the person's own product")
    team = s.index("is always such a duty")
    assert never < rule and never < team
    # the true positives keep their own rule
    assert '"Establish AI governance' in s
    assert "aiming for over 60%" in s
    # a "build up a platform" mandate example primed the false positive; it stays out
    assert "Build up a data platform" not in s


# D-30, ruling A1-R8-1 = B — a no closes the topic.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_gap_followups_session import (  # noqa: E402, F401 — db is a fixture
    _INFRA,
    _answer,
    _cluster,
    _gap_click,
    _latest_cluster,
    _row,
    _seed,
    _writer,
    _writing_bridge,
    db,
)


async def _assistants_case(db):
    """The run-6 shape on synthetic data: the cluster names the posting's wide
    term ("AI copilots") next to members the answer covers; the answer covers
    those and denies the narrower thing in its own words."""
    return await _seed(db, clusters=[
        _cluster(_INFRA, "AI assistants", ["AI agents", "AI copilots", "Agentic workflows"]),
    ], ledger=[_row("AI agents"), _row("AI copilots"), _row("Agentic workflows")])


@pytest.mark.asyncio
async def test_a_no_in_narrower_words_closes_the_topic_and_is_one_limit(db):
    job, _profile, _ = await _assistants_case(db)
    writer = _writer("Tell me about AI agents, copilots and agentic workflows.",
                     "Beyond coding assistants, have you worked with AI copilots?")
    created = await _gap_click(db, job, _INFRA, writer)
    bridge = _writing_bridge(add_skills=["AI agents", "Agentic workflows"],
                             deny=["coding assistants"])
    resp = await _answer(
        db, created.session_id,
        "At Acme I built AI agents with agentic workflows. "
        "With coding assistants I have no experience.",
        bridge, writer,
    )

    assert len(writer.calls) == 1, "no second question on the limit just given"
    assert resp.question != "Beyond coding assistants, have you worked with AI copilots?"
    assert resp.complete is True, "the interview moved on (the only cluster is done)"
    assert bridge.await_count == 1, "one answer turn, so one recorded no"
    from applire.models.profile import MasterProfile
    from sqlalchemy import select

    vault = (await db.execute(select(MasterProfile).where(MasterProfile.id == _profile.id))).scalar_one()
    await db.refresh(vault)
    limits = [d["concept"] for d in (vault.profile_json.get("metadata") or {}).get("denied_concepts") or []]
    assert limits == ["coding assistants"], "the summary has exactly one limit to name"
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["asked"] == 1


@pytest.mark.asyncio
async def test_a_no_about_another_member_also_closes_the_topic(db):
    """The accepted cost (founder, A1-R8-1): the open member loses its one
    follow-up when the same answer recorded any no."""
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Platform", ["Kubernetes", "Terraform", "Helm"]),
    ], ledger=[_row("Kubernetes"), _row("Terraform"), _row("Helm")])
    writer = _writer("Tell me about the platform stack.", "And Terraform and Helm?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(
        db, created.session_id,
        "Kubernetes in production at Acme. Bookkeeping I have never done.",
        _writing_bridge(add_skills=["Kubernetes"], deny=["Bookkeeping"]), writer,
    )

    assert len(writer.calls) == 1


@pytest.mark.asyncio
async def test_control_an_answer_without_a_no_keeps_its_follow_up(db):
    """Mutation guard's other side: without a recorded no the open members
    still earn their one follow-up (ADR-089 cl. 2 unchanged)."""
    job, _profile, _ = await _assistants_case(db)
    writer = _writer("Tell me about AI agents, copilots and agentic workflows.",
                     "And AI copilots?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "At Acme I built AI agents with agentic workflows.",
        _writing_bridge(add_skills=["AI agents", "Agentic workflows"]), writer,
    )

    assert resp.question == "And AI copilots?"
    assert writer.calls[-1]["follow_up_focus"] == ["AI copilots"]
