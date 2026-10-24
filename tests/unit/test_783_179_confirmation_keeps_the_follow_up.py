# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:179 (Hazelnut build 4) — a confirmation raised on a gap answer no longer
closes that gap without its follow-up turn.

UX audit Marcus: the SAP answer covered SAP PP and raised a skill-merge
confirmation; the confirmation was asked, answered, and the interview moved on
— the partial-coverage follow-up the answer had earned (SAP MM still open) was
never asked: the gap got 1 of its 2 turns. 787:L7 stopped the confirmation
from spending the budget; this is the other half: after the confirmation is
resolved, the follow-up the answer earned is asked.

Driven through the REAL ``send_message`` path (same faithful doubles as
``test_gap_followups_session``); the bridge double also raises one
confirmation, like the reconciler's merge ambiguity.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_gap_followups_session import (  # noqa: E402, F401 — db is a fixture
    _INFRA,
    _answer,
    _gap_click,
    _latest_cluster,
    _leave_open,
    _seed,
    _writer,
    _writing_bridge,
    db,
)


def _confirming(bridge):
    """Wrap a faithful bridge double so its turn also raises a confirmation."""
    from applire.services.profile.reconcile.ops import RequestConfirmation

    inner = bridge.side_effect

    async def _run(db, **kwargs):
        result = await inner(db, **kwargs)
        result.pending_confirmations = [RequestConfirmation(
            question="Add 'Kubernetes' as a separate skill?",
            options=["Add it as a separate skill", "Neither — don't add it"],
            context={"incoming": "Kubernetes"},
        )]
        return result

    return AsyncMock(side_effect=_run)


@pytest.mark.asyncio
async def test_after_the_confirmation_the_earned_follow_up_is_asked(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)

    first = await _answer(
        db, created.session_id, "I ran Kubernetes clusters in production at Acme.",
        _confirming(_writing_bridge(add_skills=["Kubernetes"])), writer,
    )
    assert first.question == "Add 'Kubernetes' as a separate skill?", "fixture: the confirmation comes first"

    second = await _answer(db, created.session_id, "Neither — don't add it",
                           _writing_bridge(), writer)

    assert second.complete is False, "the gap is not closed by the confirmation"
    assert second.question == "And Terraform?"
    assert writer.calls[-1]["follow_up_focus"] == ["Terraform"]
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["gaps"] == ["Terraform"]


@pytest.mark.asyncio
async def test_control_a_confirmation_on_a_fully_covering_answer_still_completes(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "unexpected")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(
        db, created.session_id, "Kubernetes and Terraform daily at Acme.",
        _confirming(_writing_bridge(add_skills=["Kubernetes", "Terraform"])), writer,
    )
    second = await _answer(db, created.session_id, "Neither — don't add it",
                           _writing_bridge(), writer)

    assert second.complete is True
    assert len(writer.calls) == 1


@pytest.mark.asyncio
async def test_a_gap_left_open_while_the_confirmation_was_on_screen_gets_no_follow_up(db):
    """Ruling K-1 reaches the parked follow-up: the gate is re-read at ask time."""
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(
        db, created.session_id, "I ran Kubernetes clusters in production at Acme.",
        _confirming(_writing_bridge(add_skills=["Kubernetes"])), writer,
    )
    await _leave_open(db, job.id, _INFRA)
    second = await _answer(db, created.session_id, "Neither — don't add it",
                           _writing_bridge(), writer)

    assert second.question != "And Terraform?"
    assert len(writer.calls) == 1
