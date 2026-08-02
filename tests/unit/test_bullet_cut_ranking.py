# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-072 clauses 1 & 4 — the deterministic tail's cut ranking, and its audit trail.

Ground truth (#423, charter run A, 2026-08-01 13:07). The Weberit role shipped at
a logged ceiling of ``max 5 (tier: top)`` carrying 6 settled bullets, and the cap
deleted

    "Verantwortung für den Sauberraumbereich (Kunststoff- und
     Kosmetik-Verpackungen) seit 2021"

— the candidate's ONLY packaging evidence, against a JD whose employer is a
packaging manufacturer. It ranked last on both of the cap's criteria: a bare
year is not a quantified figure, and it was listed last. All four blind
reviewers across two runs made its absence their single shared reservation.

The correction is a THIRD criterion ABOVE figure-presence: a bullet that is the
only carrier of a claimable Keyword-Ledger concept is cut after a bullet whose
concepts are also carried elsewhere in the document. It only reorders what is
cut among bullets the writer already wrote — it never asks for content, which
is the property that distinguishes it from the reverted #303 predicate.
"""
import logging
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.bullet_cuts import rank_cuts  # noqa: E402
from applire.services.cv_budget import (  # noqa: E402
    BudgetResult,
    BulletTier,
    RoleBudget,
    compute_bullet_budgets,
    condense_to_budget,
)

# The run-A shapes, verbatim where the wording is what makes the point.
PACKAGING = (
    "Verantwortung für den Sauberraumbereich "
    "(Kunststoff- und Kosmetik-Verpackungen) seit 2021"
)
FIGURE_A = "Ausschussquote von 4,1 % auf 1,8 % gesenkt"
FIGURE_B = "Durchsatz um 12 % erhöht"
LEAN = "Lean Management in der Fertigung eingeführt"

# One group per claimable ledger entry — the shape ``BudgetResult.claimable_concepts``
# carries (``keyword_ledger.retention_forms`` per entry, never flattened: #386).
PACKAGING_GROUP = ("Verpackungen", "Verpackungsindustrie")
LEAN_GROUP = ("Lean Management", "Lean")


# --- rank_cuts: the ranking itself ------------------------------------------


def test_sole_carrier_outranks_a_figure_bullet_whose_concept_is_covered():
    """#423 run A, minimised. The packaging bullet carries no figure and is
    listed last, so BOTH of the old criteria condemn it — yet it is the only
    carrier of a claimable concept, while the Lean bullet's concept is also in
    the skills list. The Lean bullet is the correct cut."""
    texts = [FIGURE_A, LEAN, PACKAGING]
    tiers = [(True, -0), (False, -1), (False, -2)]  # (carries_figure, -order)
    cuts = rank_cuts(
        texts, tiers, keep=2,
        concept_groups=[PACKAGING_GROUP, LEAN_GROUP],
        external_text="Lean Management\nSix Sigma",  # the skills list
    )
    assert [c.index for c in cuts] == [1]
    assert cuts[0].text == LEAN
    assert cuts[0].sole_carrier is False


def test_a_sole_carrier_outranks_a_figure_bullet_carrying_no_claimable_concept():
    """The decision this clause actually forks on, pinned deliberately.

    #377 established that a quantified bullet must never lose to filler that
    merely repeats a ledger surface form. This clause keeps that — a bullet
    whose concept is covered elsewhere gets NO protection — but it does let a
    figure-less SOLE carrier outrank a figure bullet that carries no claimable
    concept at all, so the figure is the content lost.

    That trade is the panel's, not ours: all four blind reviewers across two
    runs named the missing packaging evidence as their single reservation, and
    none of them asked for another number. It is also bounded — protection can
    only change WHICH bullet is cut when the ceiling binds, never how many.

    Within the unprotected tier the #377 order is untouched, which is what
    keeps the two rules compatible rather than merely sequenced.
    """
    texts = [FIGURE_A, FIGURE_B, PACKAGING]
    tiers = [(True, -0), (True, -1), (False, -2)]
    cuts = rank_cuts(texts, tiers, keep=2,
                     concept_groups=[PACKAGING_GROUP], external_text="")
    assert [c.index for c in cuts] == [1]
    assert cuts[0].text == FIGURE_B


def test_the_last_two_carriers_of_one_concept_are_never_both_cut():
    """The status is recomputed after every removal, not once up front. Two
    bullets carry the packaging concept, so NEITHER is a sole carrier at the
    start; a status computed once would cut both and lose the concept — the
    exact harm the clause exists to prevent."""
    texts = [FIGURE_A, FIGURE_B, PACKAGING, "Verpackungslinien umgerüstet"]
    tiers = [(True, -0), (True, -1), (False, -2), (False, -3)]
    cuts = rank_cuts(
        texts, tiers, keep=2,
        concept_groups=[PACKAGING_GROUP],
        external_text="",
    )
    survivors = {i for i in range(4)} - {c.index for c in cuts}
    assert 2 in survivors or 3 in survivors, "the concept lost its last carrier"
    # Round 1 cuts the later of the two figure-less carriers; round 2 finds the
    # other one newly protected and takes a figure bullet instead.
    assert [c.index for c in cuts] == [3, 1]


def test_without_a_ledger_the_ranking_is_exactly_the_figure_order():
    """#377 regression: with no claimable concepts nothing is protected, and
    the pass must behave precisely as it did before this clause — figure-less
    first, later-listed first."""
    texts = ["a", "b", "Reduced deployment time by 40%"]
    tiers = [(False, -0), (False, -1), (True, -2)]
    cuts = rank_cuts(texts, tiers, keep=1, concept_groups=[], external_text="")
    assert [c.index for c in cuts] == [1, 0]


def test_a_concept_carried_by_no_surviving_bullet_protects_nothing():
    """A claimable concept absent from every bullet must not perturb the
    ranking — protection is carrier-based, not membership-based."""
    texts = ["a", "b", "Reduced deployment time by 40%"]
    tiers = [(False, -0), (False, -1), (True, -2)]
    cuts = rank_cuts(
        texts, tiers, keep=1,
        concept_groups=[("Kubernetes",)],
        external_text="",
    )
    assert [c.index for c in cuts] == [1, 0]


def test_protection_cannot_change_how_many_bullets_are_cut():
    """The named risk in ADR-072's consequences: a mis-classified ledger
    concept can only change WHICH bullet is cut, never the count. Every bullet
    here is a sole carrier and the ceiling still binds."""
    texts = ["Verpackungen A", "Lean Management B", "Six Sigma C"]
    tiers = [(False, -0), (False, -1), (False, -2)]
    cuts = rank_cuts(
        texts, tiers, keep=1,
        concept_groups=[PACKAGING_GROUP, LEAN_GROUP, ("Six Sigma",)],
        external_text="",
    )
    assert len(cuts) == 2
    assert all(c.sole_carrier is True for c in cuts)


def test_no_op_when_already_within_budget():
    cuts = rank_cuts(["a", "b"], [(False, -0), (False, -1)], keep=5,
                     concept_groups=[], external_text="")
    assert cuts == []


# --- BudgetResult.claimable_concepts: which concepts are protectable --------


def _work(eid: str, bullets: list[str]) -> dict:
    return {
        "id": eid, "company": "Weberit", "position": "Produktionsleiter",
        "start_date": "2021-01", "is_current": True, "bullets": bullets,
    }


def test_only_claimable_ledger_entries_become_protectable_concepts():
    """A gap concept must NEVER protect a bullet: the bullet carrying an
    honest-gap term is over-claiming, and protecting it would make the page
    budget the last instrument in the pipeline to reward that."""
    from datetime import date

    ledger = [
        {"concept": "Verpackungen", "claimable": True,
         "surface_forms": ["Verpackungsindustrie"], "sources": ["required"]},
        {"concept": "SAP S/4HANA", "claimable": False, "surface_forms": ["SAP"]},
        {"concept": "Führungsspanne ~120 MA", "claimable": True, "bar": {"value": 120}},
    ]
    budget = compute_bullet_budgets(
        [_work("r1", [PACKAGING])], ledger, target_pages=2, today=date(2026, 8, 2),
    )
    # ``retention_forms`` yields surface_forms first, then the concept name.
    assert budget.claimable_concepts == (("Verpackungsindustrie", "Verpackungen"),)


def test_a_positioning_only_entry_protects_its_substitute_not_the_jd_term():
    """ADR-048 amended 2026-07-27 — the same rule ``retention_forms`` already
    encodes for the flat list: the bullet worth protecting is the one standing
    in for the requirement, never the term the candidate cannot claim."""
    from datetime import date

    ledger = [{"concept": "TOGAF", "claimable": True,
               "surface_forms": ["TOGAF 9"], "adjacent_evidence": "arc42"}]
    budget = compute_bullet_budgets(
        [_work("r1", ["arc42-Dokumentation etabliert"])], ledger,
        target_pages=2, today=date(2026, 8, 2),
    )
    assert budget.claimable_concepts == (("arc42",),)


# --- condense_to_budget: the page-overrun path -------------------------------


def _budget(role_ceilings: dict[str, int], concept_groups=()) -> BudgetResult:
    return BudgetResult(
        roles={eid: RoleBudget(work_entry_id=eid, tier="mid", max_bullets=c)
               for eid, c in role_ceilings.items()},
        tiers={"top": BulletTier("top", 5, 4), "mid": BulletTier("mid", 3, 2),
               "bottom": BulletTier("bottom", 1, 0)},
        target_pages=2, region="DACH",
        claimable_concepts=tuple(tuple(g) for g in concept_groups),
    )


def test_condense_protects_the_sole_carrier_across_the_whole_document():
    data = {
        "contact": {"name": "X"}, "skills": ["Lean Management", "Six Sigma"],
        "work_history": [{"id": "r1", "bullets": [FIGURE_A, LEAN, PACKAGING]}],
    }
    out, changed = condense_to_budget(
        data, _budget({"r1": 2}, [PACKAGING_GROUP, LEAN_GROUP]), 1,
    )
    assert changed
    assert out["work_history"][0]["bullets"] == [FIGURE_A, PACKAGING]


def test_condense_sees_coverage_in_another_role_it_has_already_cut():
    """Coverage is a whole-document question, and the document shrinks as the
    pass runs. Role r2's bullet is the packaging concept's other carrier; once
    r2 has been cut down to nothing carrying it, r1's bullet is the sole
    carrier and must be protected."""
    data = {
        "contact": {"name": "X"}, "skills": [],
        "work_history": [
            {"id": "r2", "bullets": ["Verpackungslinien umgerüstet", FIGURE_B]},
            {"id": "r1", "bullets": [FIGURE_A, PACKAGING]},
        ],
    }
    out, _ = condense_to_budget(data, _budget({"r2": 1, "r1": 1}, [PACKAGING_GROUP]), 1)
    kept = out["work_history"][0]["bullets"] + out["work_history"][1]["bullets"]
    assert any("Verpackung" in b or "Sauberraum" in b for b in kept)


def test_condense_with_no_claimable_concepts_is_unchanged():
    """#377's contract, unaltered on the legacy/no-ledger path."""
    data = {"contact": {"name": "X"}, "skills": [], "work_history": [
        {"id": "r1", "bullets": ["Filed weekly reports", "Watered the plants",
                                 "Reduced deployment time by 40%"]}]}
    out, _ = condense_to_budget(data, _budget({"r1": 1}), 1)
    assert out["work_history"][0]["bullets"] == ["Reduced deployment time by 40%"]


# --- clause 4: every deletion in the tail leaves a trace ---------------------


def test_every_condensed_bullet_is_logged_with_pass_predicate_and_text(caplog):
    """ADR-072 clause 4. #423 cost four runs and a full input replay to
    attribute *because the deletion left no trace* — the log line names the
    pass, the removed content and the predicate that fired."""
    data = {"contact": {"name": "X"}, "skills": [], "work_history": [
        {"id": "r1", "bullets": [FIGURE_A, LEAN]}]}
    with caplog.at_level(logging.INFO, logger="applire.services.bullet_cuts"):
        condense_to_budget(data, _budget({"r1": 1}), 1)
    lines = [r.getMessage() for r in caplog.records if "TAIL_DELETE" in r.getMessage()]
    assert len(lines) == 1
    assert "condense_to_budget" in lines[0]
    assert "role_id='r1'" in lines[0] and "ceiling=1" in lines[0]
    assert LEAN in lines[0]
    assert "sole_carrier=False" in lines[0]


def test_cutting_a_protected_bullet_is_logged_louder_than_an_ordinary_cut(caplog):
    """When the ceiling is tighter than the protected set, the clause cannot be
    honoured — and that is exactly the case that must not be silent."""
    data = {"contact": {"name": "X"}, "skills": [], "work_history": [
        {"id": "r1", "bullets": ["Verpackungen verantwortet", "Lean Management eingeführt"]}]}
    with caplog.at_level(logging.INFO, logger="applire.services.bullet_cuts"):
        condense_to_budget(data, _budget({"r1": 1}, [PACKAGING_GROUP, LEAN_GROUP]), 1)
    protected = [r for r in caplog.records
                 if "TAIL_DELETE" in r.getMessage() and "sole_carrier=True" in r.getMessage()]
    assert len(protected) == 1
    assert protected[0].levelno == logging.WARNING


@pytest.mark.parametrize("keep", [0, 1, 2, 3])
def test_the_survivor_count_is_exact_for_every_ceiling(keep):
    texts = [FIGURE_A, LEAN, PACKAGING]
    tiers = [(True, -0), (False, -1), (False, -2)]
    cuts = rank_cuts(texts, tiers, keep=keep,
                     concept_groups=[PACKAGING_GROUP, LEAN_GROUP], external_text="")
    assert len(texts) - len(cuts) == max(keep, 0)
    assert len({c.index for c in cuts}) == len(cuts)
