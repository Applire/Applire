# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — 786:290(c): the `cv.role_budget` finding never offers the
CV's only carrier of a claimable concept as a plain drop.

UX audit round 3: the terminal corrector dropped "Schnittstelle zu Einkauf,
Qualitätssicherung und Supply Chain …" because the budget finding listed it as the
lowest-ranked candidate; the next round's `cv.claim_missing` asked for "Supply Chain"
back. A candidate whose removal would open `cv.claim_missing` is listed last and says
"merge it into a related bullet, never drop it". Synthetic data.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_here = Path(__file__).parent
for p in (_here, _here.parent.parent / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from test_e068_cv_pipeline_v2 import W1, W2, _budget, _by_check, _checks, _prose, _vault  # noqa: E402

SC = "Diente als Schnittstelle zu Einkauf und Supply Chain."
KVP = "Moderierte die KVP-Workshops der Montage."
PLAIN = "Pflegte die Schichtpläne."


def _row(concept):
    return {"concept": concept, "surface_forms": [concept], "status": "direct",
            "claimable": True, "fit_weight": 1.0, "sources": ["required"]}


def _case(bullets):
    vault = _vault()
    vault["work_experience"][0]["responsibilities"] = [
        "Leitete die Fertigung mit zwölf Mitarbeitenden.", SC, KVP, PLAIN,
    ]
    prose = _prose()
    prose["work"][0]["bullets"] = ["Senkte die Ausschussquote um 18 Prozent.", *bullets]
    findings = _by_check(_checks(
        prose, vault=vault, budget=_budget(**{W1: 2, W2: 3}),
        keyword_ledger=[_row("Supply Chain"), _row("KVP")],
    ))
    return findings["cv.role_budget"].text.split("Lowest-ranked by the budget ranking")[1]


def test_the_only_carrier_of_a_claimable_concept_is_labelled_merge_never_drop():
    cands = _case([KVP, SC])
    assert "Supply Chain" in cands
    assert "(the only bullet carrying “Supply Chain” — merge it into a related bullet, never drop it)" in cands


def test_a_plain_candidate_comes_first_and_carries_no_label():
    cands = _case([SC, PLAIN])
    if SC[:40] in cands:  # both offered: the plain one first
        assert cands.index(PLAIN[:20]) < cands.index(SC[:40])
    assert "“Pflegte die Schichtpläne.”" in cands and "Pflegte die Schichtpläne.” (the only" not in cands


def test_without_a_ledger_the_candidates_are_unchanged():
    vault = _vault()
    prose = _prose()
    prose["work"][0]["bullets"] = ["Senkte die Ausschussquote um 18 Prozent.", KVP, SC]
    text = _by_check(_checks(prose, vault=vault, budget=_budget(**{W1: 2, W2: 3})))["cv.role_budget"].text
    assert "never drop it" not in text
