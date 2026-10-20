# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""787:L11 — founder ruling V-1 = B (Hazelnut build 2, 2026-10-20).

A bare skills-list entry still lifts an open gap to claimable (the gap counts the
user sees do not change), but the lifted row carries what backs it:
``narrative_backed`` is re-stamped at the lift seam, and the writers' KEYWORD
LEDGER block marks a row no role bullet, achievement or story states with
``LISTED_ONLY_MARKER``. The writers' rule on that marker is D1's prompt work.

Reproduction (before): the re-check lifted ``Kubernetes`` from a skills-only
vault to ``direct`` and left ``narrative_backed`` as the build stamped it (absent
on a hand-built row, read as True → no marker, no liability).
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.keyword_ledger import (  # noqa: E402
    LISTED_ONLY_MARKER,
    reevaluate_gap_ledger_against_vault,
    render_ledger_prompt_block,
)


def _vault() -> dict:
    return {
        "skills": [{"name": "Kubernetes"}, {"name": "Python"}],
        "work_experience": [
            {"id": "w1", "company": "Acme", "role": "Dev", "responsibilities": ["Built REST APIs in Python"]}
        ],
    }


def _gap(concept: str) -> dict:
    return {"concept": concept, "status": "gap", "claimable": False, "surface_forms": [concept],
            "sources": ["required"]}


def test_a_bare_skill_name_still_lifts_but_is_marked_listed_only():
    out, changed = reevaluate_gap_ledger_against_vault([_gap("Kubernetes"), _gap("Python")], _vault())
    rows = {e["concept"]: e for e in out}
    assert changed
    assert rows["Kubernetes"]["claimable"] is True and rows["Kubernetes"]["status"] == "direct"
    assert rows["Kubernetes"]["narrative_backed"] is False
    assert rows["Python"]["claimable"] is True
    assert rows["Python"]["narrative_backed"] is True


def test_a_lift_by_a_bullet_clears_a_stale_build_time_false():
    stale = {**_gap("Python"), "narrative_backed": False}
    out, _ = reevaluate_gap_ledger_against_vault([stale], _vault())
    assert out[0]["claimable"] is True and out[0]["narrative_backed"] is True


def test_the_writer_block_marks_only_the_listed_only_row():
    out, _ = reevaluate_gap_ledger_against_vault([_gap("Kubernetes"), _gap("Python")], _vault())
    block = render_ledger_prompt_block(out)
    k_line = next(line for line in block.splitlines() if line.strip().startswith("- Kubernetes"))
    p_line = next(line for line in block.splitlines() if line.strip().startswith("- Python"))
    assert LISTED_ONLY_MARKER in k_line
    assert LISTED_ONLY_MARKER not in p_line


def test_a_pre_260_row_without_the_key_gets_no_marker():
    row = {"concept": "Go", "status": "direct", "claimable": True, "surface_forms": ["Go"], "evidence": "Go"}
    assert LISTED_ONLY_MARKER not in render_ledger_prompt_block([row])
