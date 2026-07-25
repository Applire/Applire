# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#196 — the role-attribution matcher (Oracle v2, ADR-052 §6).

Deterministic, like every matcher in this package: a claim rendered under one
position whose backing evidence belongs exclusively to another is
misattribution — mechanically decidable from ``Claim.source_experience_id``
(the US187 ``TailoredWorkEntry.id`` back-reference) and
``EvidenceUnit.owner_ids``.
"""
from __future__ import annotations

from applire.services.oracle.matchers.vault import EvidenceUnit


def find_foreign_owner(
    source_id: str | None,
    units: list[EvidenceUnit],
    same_employer_ids: frozenset[str] = frozenset(),
) -> EvidenceUnit | None:
    """The unit proving misattribution, or ``None`` when attribution is fine.

    Flags only when EVERY backing unit is owned and none belongs to the
    claim's rendered experience (or a SAME-EMPLOYER sibling of it, #237
    round-3). A unit clears the claim when it is role-agnostic (no owners),
    owned by the rendered experience — including associated projects, whose
    units carry the parent work id (US187 nesting) — or owned by another
    position at the SAME company (``same_employer_ids``, from
    ``VaultIndex.same_employer_ids``): a claim anchored to one internal role
    (e.g. the current-role tie-break) whose evidence lives on a DIFFERENT
    tenure at the identical employer is an ordinary attribution gap, not a
    cross-EMPLOYER blend — the misattribution verdict is reserved for
    evidence belonging exclusively to a genuinely different company/project.
    Claims without a rendered-position anchor are never flagged (fail open).
    """
    if not source_id or not units:
        return None
    allowed = same_employer_ids | {source_id}
    if any(not u.owner_ids or (u.owner_ids & allowed) for u in units):
        return None
    return units[0]
