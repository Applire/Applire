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
