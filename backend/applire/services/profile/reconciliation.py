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

"""
US161 (E033 / ADR-041 amended 2026-08-28, #615) — merge count-reconciliation.

Counts how many data points were *extracted* from an incoming CV vs how many
are *stored* (represented) in the merged profile, per list-valued content
section. A positive delta means an extracted item is neither newly added nor
recognised as carried — i.e. silent merge data-loss (FMEA JF-M-3.3).

Deterministic, no LLM. OBSERVATIONAL only (ADR-013): inspects the merge
result, never changes what gets merged.

**One computation feeds both outputs (#615).** `stored` is no longer a bare
key-intersection between `incoming` and `merged` — that undercounted a merge
that legitimately carries a fact without restating its identity (an
`add_bullets`-only second-source merge, refuter B's BLOCKER1) and, in the
other direction, said nothing about entries parked on a visible confirmation
channel. `stored = extracted - len(items of that section)`, where `items`
is computed ONCE, by the SAME carried-predicate the import doors' own
`not_applied` fact uses
(`reconcile.import_witness.compute_import_not_applied`) — passed in by the
caller, not recomputed here, so the Health hub's severity and the doors'
`not_applied` list can never disagree about the same merge (a refuter showed
the two halves contradicting each other under the first draft, which ran an
unrelated bare-intersection count here while the fact list got the richer
predicate).

Only `reason="no_op_carried_entry"` items are counted into a section's delta.
A `reason="op_rejected"` item (`section=None`) is a parse-time drop that is
not tied to any one incoming entry — folding it in here as well would risk
double-counting the SAME underlying loss when the corresponding incoming
entry independently also fails the carried-predicate.

Entity list: every list-valued content section, keyed on the committer's OWN
`reconcile/apply.py:_ENTRY_NATURAL_KEYS` table (ADR-077 clause 1's "the same
entry" identity, already used for `ReplaceSection`/id-preservation) — the
same normaliser (`apply.py:_norm`, NFC -> strip -> casefold), imported here
rather than a second copy. Widened from the original 5 entities
(work_experience, skills, certifications, education, projects) to all 9:
languages, publications, volunteer_activities and signature_stories used to
go uncounted entirely (#332's shape) — the captured #615 record lost 2
languages that never showed up in this delta.

Two scalar sections (`personal_info`, `professional_summary`) stay OUTSIDE
this count — a `set_*` op either lands, conflicts, or (for `personal_info`
specifically, per `_apply_set_personal_info`'s fill-only silent no-op on an
already-populated field) is dropped with no receipt on ANY channel; the
latter is a real, independently-tracked gap (refuter B MAJOR 2), not covered
by this widening.
"""
from __future__ import annotations

from typing import Sequence

from applire.schemas.profile import ImportNotApplied, MasterProfileData
from applire.services.profile.reconcile.apply import _norm
from applire.services.profile.reconcile.import_witness import WITNESS_KEYS


def _key(entry: object, *attrs: str) -> tuple[str, ...]:
    return tuple(_norm(getattr(entry, a, "")) for a in attrs)


def compute_merge_reconciliation(
    incoming: MasterProfileData,
    merged: MasterProfileData,
    not_applied: Sequence[ImportNotApplied],
) -> dict[str, dict[str, int]]:
    """Per-section {extracted, stored, delta} for an additive merge.

    ``extracted`` = distinct natural-key identities in *incoming* (a literal
    duplicate incoming entry counts once — the SAME dedup
    ``import_witness.compute_import_not_applied`` applies before evaluating
    its carried-predicate, so the two numbers stay consistent by
    construction). ``not_applied`` is that function's already-computed
    output for this SAME (incoming, merged, ops) triple — pass it in, do not
    recompute it here.
    """
    counted_by_section: dict[str, int] = {}
    for item in not_applied:
        if item.reason != "no_op_carried_entry" or item.section is None:
            continue
        counted_by_section[item.section] = counted_by_section.get(item.section, 0) + 1

    result: dict[str, dict[str, int]] = {}
    for entity, attrs in WITNESS_KEYS.items():
        # WITNESS_KEYS = the committer's natural key, plus `start_date` on the
        # engagement sections (B1: a repeat stint is a distinct data point).
        extracted = len({_key(e, *attrs) for e in getattr(incoming, entity)})
        delta = counted_by_section.get(entity, 0)
        result[entity] = {
            "extracted": extracted,
            "stored": extracted - delta,
            "delta": delta,
        }
    return result
