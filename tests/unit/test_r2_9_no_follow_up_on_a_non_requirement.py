# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-9 (Hazelnut build 4, UX re-audit 2 felix Q7) — a posting term the match
does not count never earns a partial-coverage follow-up of its own.

Round 2: Felix answered the security topic; the session then spent the
cluster's second slot on "Auditability" — a member whose ledger row carries
``fit_weight == 0`` (the sidebar called it "a term from the posting, not a
counted requirement", WP-G's ``member_is_requirement``). The candidate read it
as being asked the same thing again.

The fact deciding it is the ledger's (``fit_weight``), computed by G's
``gap_coverage.member_is_requirement``; this module only reads it. Driven
through the REAL ``send_message`` path on the persisted analysis-row shape
(same faithful doubles as ``test_gap_followups_session``).

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_r2_9_no_follow_up_on_a_non_requirement.py -q
"""

import sys
from pathlib import Path

import pytest

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


def _term(concept: str) -> dict:
    """A posting term the match does not count (``fit_weight`` 0)."""
    return {**_row(concept), "fit_weight": 0.0, "sources": ["keyword"]}


async def _security_case(db, *, auditability_counts: bool):
    ledger = [
        _row("Security reviews"),
        _row("Auditability") if auditability_counts else _term("Auditability"),
    ]
    return await _seed(db, clusters=[
        _cluster(_INFRA, "Security & compliance", ["Security reviews", "Auditability"]),
    ], ledger=ledger)


@pytest.mark.asyncio
async def test_an_uncounted_posting_term_earns_no_follow_up(db):
    job, _profile, _ = await _security_case(db, auditability_counts=False)
    writer = _writer("Tell me about security reviews.", "And auditability?")
    created = await _gap_click(db, job, _INFRA, writer)

    resp = await _answer(
        db, created.session_id,
        "I ran the quarterly security reviews for our payment services at Acme.",
        _writing_bridge(add_skills=["Security reviews"]), writer,
    )

    assert len(writer.calls) == 1, "no second question on a term the match does not count"
    assert resp.question != "And auditability?"
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["asked"] == 1
    assert persisted["outcome"]["covered"] == ["Security reviews"]


@pytest.mark.asyncio
async def test_control_a_counted_requirement_still_earns_its_follow_up(db):
    job, _profile, _ = await _security_case(db, auditability_counts=True)
    writer = _writer("Tell me about security reviews.", "And auditability?")
    created = await _gap_click(db, job, _INFRA, writer)

    resp = await _answer(
        db, created.session_id,
        "I ran the quarterly security reviews for our payment services at Acme.",
        _writing_bridge(add_skills=["Security reviews"]), writer,
    )

    assert resp.question == "And auditability?"
    assert writer.calls[-1]["follow_up_focus"] == ["Auditability"]


# ---------------------------------------------------------------------------
# felix Q2 — the turn's recorded no's travel to the follow-up judgement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_with_a_recorded_no_earns_no_follow_up(db):
    """Round 2 (felix Q2): the answer said "I haven't built settlement or
    payout rails myself", and the follow-up asked about "Payment settlement"
    and "Payout flows" anyway. R2-9 handed the recorded no to the drafting call
    as a fact; delivery run 6 (D-30) showed the model still re-asks a no given
    in other words. Ruling A1-R8-1 = B: a recorded no closes the topic — no
    follow-up at all."""
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Payments", ["Checkout", "Payment settlement", "Payout flows"]),
    ], ledger=[_row("Checkout"), _row("Payment settlement"), _row("Payout flows")])
    writer = _writer("Tell me about payments.", "And settlement?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "Checkout at Acme daily. I haven't built settlement or payout rails myself.",
        _writing_bridge(add_skills=["Checkout"], deny=["settlement or payout rails"]), writer,
    )

    assert len(writer.calls) == 1, "the no closed the topic"
    assert resp.question != "And settlement?"


@pytest.mark.asyncio
async def test_control_a_turn_without_a_no_passes_no_denials(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(db, created.session_id, "Kubernetes in production at Acme.",
                  _writing_bridge(add_skills=["Kubernetes"]), writer)

    assert "recorded_denials" not in writer.calls[-1]


def test_the_follow_up_prompt_states_the_recorded_denials_as_a_fact():
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c", "label": "Payments", "gaps": ["Payment settlement"], "jd_skills": [],
               "jd_context": ""}
    msgs = [{"role": "assistant", "content": "Payments?"},
            {"role": "user", "content": "I haven't built settlement rails."}]
    with_fact = build_question_prompt(cluster, {}, msgs, follow_up_focus=["Payment settlement"],
                                      recorded_denials=["settlement rails"])
    without = build_question_prompt(cluster, {}, msgs, follow_up_focus=["Payment settlement"])

    assert 'has NOT done: "settlement rails"' in with_fact
    assert "never asked about again" in with_fact
    assert "has NOT done" not in without


@pytest.mark.asyncio
async def test_the_question_generator_renders_the_recorded_denials_into_the_draft_prompt():
    """The middle seam: ``question_generator_with_profile`` passes the no's to
    the prompt builder (the session tests above double the generator)."""
    from unittest.mock import AsyncMock, MagicMock

    from applire.services.interview_graph import question_generator_with_profile

    state = {
        "mode": "targeted", "critical_gaps": ["c"], "current_gap_index": 0,
        "gap_clusters_by_id": {"c": {"id": "c", "label": "Payments",
                                     "gaps": ["Payment settlement"], "jd_skills": [],
                                     "jd_context": ""}},
        "messages": [{"role": "assistant", "content": "Payments?"},
                     {"role": "user", "content": "I haven't built settlement rails."}],
    }
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={
        "question": "", "choices": None, "covered_by_answer": ["Payment settlement"],
    })
    out = await question_generator_with_profile(
        state, {}, provider, follow_up_focus=["Payment settlement"],
        recorded_denials=["settlement rails"],
    )

    prompt = provider.aparse_json.await_args.args[0]
    assert 'has NOT done: "settlement rails"' in prompt
    assert out["question"] == ""
    assert out["follow_up_remaining"] == []
