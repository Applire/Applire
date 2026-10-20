# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""787:L43 (ADR-059 floor over surface forms) — a denial in another inflection of
the same concept floors the ledger row at both ledger seams.

Reproduced on 4ee93c10 (before D2's y↔ies fold, b43f23e8): a recorded denial
"agile methodology experience" left a ledger row "agile methodologies" at
``direct``/claimable — ``_fold_variants`` folded a trailing -s only, so
"methodologies" never met "methodology". The fold is the shared presence
instrument (``ats_audit``); these are the stance seam tests on top of it. The
other half of L43 (a requirement whose ``surface_forms`` omit its component
terms) is the gap-analysis producer's (ADR-059 amended 2026-09-20) and is not
reachable from here by design — the last test pins that boundary.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.keyword_ledger import (  # noqa: E402
    _enforce_denial_stance,
    reevaluate_gap_ledger_against_vault,
)

_DENIAL = [{"concept": "agile methodology experience", "statement": "Keine Erfahrung mit agilen Methoden."}]


def _row(status: str = "direct", claimable: bool = True) -> dict:
    return {"concept": "agile methodologies", "status": status, "claimable": claimable,
            "surface_forms": ["agile methodologies", "Scrum"], "evidence": "x"}


def test_the_build_floor_reaches_the_plural_row():
    out = _enforce_denial_stance([_row()], _DENIAL, None)
    assert out[0]["claimable"] is False
    assert out[0]["status"] in ("gap", "denied")


def test_the_vault_recheck_never_lifts_the_plural_row():
    # The row's only vault evidence is prose (a bullet), not an attested entity
    # label — so nothing in the release corpus independently affirms it.
    vault = {
        "work_experience": [{"id": "w1", "company": "Acme", "role": "PM",
                             "responsibilities": ["Teamarbeit nach agile methodologies im Projektalltag"]}],
        "metadata": {"denied_concepts": _DENIAL},
    }
    out, changed = reevaluate_gap_ledger_against_vault([_row("gap", False)], vault)
    assert out[0]["claimable"] is False
    assert changed is False


def test_component_terms_outside_surface_forms_stay_out_of_reach():
    row = {"concept": "RAG methods", "status": "direct", "claimable": True,
           "surface_forms": ["RAG", "RAG methods"], "evidence": "x"}
    out = _enforce_denial_stance(
        [row], [{"concept": "vector databases", "statement": "s"}, {"concept": "retrieval pipeline", "statement": "s"}], None
    )
    # ADR-059 amended 2026-09-20: no topic radius — the bridge is surface_forms.
    assert out[0]["claimable"] is True
