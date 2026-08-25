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

"""ADR-077 clause 4 / SF-PIN.1 — the PARTITION contract on `rank_cuts`.

Pin carriers never enter the removable set: they are partitioned out BEFORE
ranking, the `keep` ceiling applies to the rest only, and when pins alone
exceed the ceiling the ceiling is violated by design (that violation IS
"pin beats budget"). NOT a ranking tier — a tier is silently defeated by a
tight ceiling (the sole-carrier WARNING boundary this row exists for).
"""

from applire.services.bullet_cuts import apply_cuts, rank_cuts

TEXTS = [
    "Pinned fact bullet with the user's priority",
    "Strong figure bullet: raised revenue 40%",
    "Filler bullet with no particular merit",
    "Second filler bullet",
]
# Ascending tiers: lower cuts first. Give the pinned bullet the WORST tier so
# only the partition (never the ranking) can save it.
TIERS = [(0, 0), (1, -1), (0, -2), (0, -3)]


def test_pinned_carrier_never_enters_the_removable_set():
    cuts = rank_cuts(TEXTS, TIERS, keep=2, pinned={0})
    cut_indices = {c.index for c in cuts}
    assert 0 not in cut_indices
    survivors = apply_cuts(TEXTS, cuts)
    assert TEXTS[0] in survivors and len(survivors) == 2


def test_keep_applies_to_the_rest_only():
    # keep=3 with one pin: the pin occupies one budget slot, two rest survive.
    cuts = rank_cuts(TEXTS, TIERS, keep=3, pinned={0})
    assert len(cuts) == 1 and cuts[0].index != 0


def test_ceiling_yields_when_pins_alone_exceed_it():
    # Two pins against keep=1: both pins survive — the ceiling is violated by
    # design; only the rest is cut (to zero).
    cuts = rank_cuts(TEXTS, TIERS, keep=1, pinned={0, 1})
    survivors = apply_cuts(TEXTS, cuts)
    assert survivors == [TEXTS[0], TEXTS[1]]  # keep=1 violated: 2 survive


def test_a_pinned_survivor_covers_its_concepts_for_the_rest():
    # The pinned bullet carries the concept; the rest bullet repeating it is
    # NOT a sole carrier (the concept survives with the pin) and stays cuttable.
    texts = ["Pinned: Kubernetes migration lead", "Also mentions Kubernetes", "Filler"]
    tiers = [(0,), (-1,), (0,)]
    cuts = rank_cuts(
        texts,
        tiers,
        keep=1,
        concept_groups=[["Kubernetes"]],
        pinned={0},
    )
    cut_indices = {c.index for c in cuts}
    assert 1 in cut_indices  # not protected as sole carrier
    assert not any(c.sole_carrier for c in cuts if c.index == 1)


def test_without_pins_the_contract_is_unchanged():
    cuts = rank_cuts(TEXTS, TIERS, keep=2)
    assert len(cuts) == 2  # exactly down to the ceiling
