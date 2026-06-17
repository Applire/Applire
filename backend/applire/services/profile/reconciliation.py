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
US161 (E033 / ADR-041 amended) — merge count-reconciliation.

Counts how many data points were *extracted* from an incoming CV vs how many are
*stored* (represented) in the merged profile, per entity. A positive delta means
an extracted item is neither newly added nor matched to an existing one — i.e.
silent merge data-loss (FMEA JF-M-3.3).

Deterministic, no LLM. OBSERVATIONAL only (ADR-013): inspects the merge result,
never changes what gets merged.
"""
from __future__ import annotations

from applire.schemas.profile import MasterProfileData


def _norm(value: object) -> str:
    return (str(value) if value is not None else "").strip().casefold()


def _key(entry: object, *attrs: str) -> tuple[str, ...]:
    return tuple(_norm(getattr(entry, a, "")) for a in attrs)


# Per-entity identity: the field(s) the additive merge keys on. An incoming item is
# "stored" if an item with the same identity exists in the merged profile.
_ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "work_experience": ("company", "start_date"),
    "skills": ("name",),
    "certifications": ("name",),
    "education": ("institution", "degree"),
}


def compute_merge_reconciliation(
    incoming: MasterProfileData, merged: MasterProfileData
) -> dict[str, dict[str, int]]:
    """Per-entity {extracted, stored, delta} for an additive merge.

    ``extracted`` = distinct identities in *incoming*; ``stored`` = those also
    represented in *merged*; ``delta`` = extracted − stored (lost items).
    """
    result: dict[str, dict[str, int]] = {}
    for entity, attrs in _ENTITY_KEYS.items():
        incoming_keys = {_key(e, *attrs) for e in getattr(incoming, entity)}
        merged_keys = {_key(e, *attrs) for e in getattr(merged, entity)}
        extracted = len(incoming_keys)
        stored = len(incoming_keys & merged_keys)
        result[entity] = {
            "extracted": extracted,
            "stored": stored,
            "delta": extracted - stored,
        }
    return result
