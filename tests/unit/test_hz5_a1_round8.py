# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5, round 8 (D-30, D-3).

The prompt pins keep each rule in the call it was measured in (gpt-6-luna,
thinking off, n >= 5 per arm — numbers in the PR body); the session tests pin
the one fact D-30 rests on (a member the turn's own no names is not drafted
again — already true through the ledger's declared-denial floor).
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


# D-30 — a member the turn's own no NAMES is never drafted again. The ledger's
# declared-denial floor already closes it before the follow-up is built (a
# guard added at the draft was a mutation survivor: this path never reaches it);
# these pin that behaviour next to the synonym case the founder rules on.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_gap_followups_session import (  # noqa: E402, F401 — db is a fixture
    _INFRA,
    _answer,
    _cluster,
    _gap_click,
    _row,
    _seed,
    _writer,
    _writing_bridge,
    db,
)


async def _platform_case(db):
    return await _seed(db, clusters=[
        _cluster(_INFRA, "Platform", ["Kubernetes", "Terraform", "Helm"]),
    ], ledger=[_row("Kubernetes"), _row("Terraform"), _row("Helm")])


@pytest.mark.asyncio
async def test_a_member_the_answer_denied_by_name_is_not_drafted_again(db):
    job, _profile, _ = await _platform_case(db)
    writer = _writer("Tell me about the platform stack.", "And Helm?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "Kubernetes in production at Acme. With Terraform I have no experience.",
        _writing_bridge(add_skills=["Kubernetes"], deny=["Terraform"]), writer,
    )

    assert writer.calls[-1]["follow_up_focus"] == ["Helm"], "the denied member left the focus"
    assert resp.question == "And Helm?"


@pytest.mark.asyncio
async def test_every_open_member_denied_asks_no_follow_up(db):
    job, _profile, _ = await _platform_case(db)
    writer = _writer("Tell me about the platform stack.", "And Helm?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(
        db, created.session_id,
        "Kubernetes in production at Acme. Terraform and Helm I have never used.",
        _writing_bridge(add_skills=["Kubernetes"], deny=["Terraform", "Helm"]), writer,
    )

    assert len(writer.calls) == 1, "nothing left to ask: no follow-up draft at all"


@pytest.mark.asyncio
async def test_control_a_no_about_something_else_keeps_the_focus(db):
    job, _profile, _ = await _platform_case(db)
    writer = _writer("Tell me about the platform stack.", "And Terraform and Helm?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(
        db, created.session_id,
        "Kubernetes in production at Acme. Bookkeeping I have never done.",
        _writing_bridge(add_skills=["Kubernetes"], deny=["Bookkeeping"]), writer,
    )

    assert writer.calls[-1]["follow_up_focus"] == ["Terraform", "Helm"]
    assert writer.calls[-1]["recorded_denials"] == ["Bookkeeping"]
