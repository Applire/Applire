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
Merge result contract + reusable employer-identity / ordering helpers.

The lexical work-merge engine (``merge_profiles`` and its date/role/bullet
predicates) was retired in US184 once all import paths moved onto the ADR-046
reconciliation engine (``reconcile_import``). What remains here is the shared
surface that survivors still depend on:

- ``MergeResult`` — the result dataclass returned by the import bridge.
- ``company_names_match`` — employer-identity check reused by ``cv_diff``.
- ``_sort_work_by_date`` — reverse-chronological ordering reused by ``cv``.
"""
from dataclasses import dataclass, field

from applire.schemas.profile import (
    Conflict,
    FieldChange,
    MasterProfileData,
    PendingConfirmation,
    WorkEntry,
)


@dataclass
class MergeResult:
    merged_profile: MasterProfileData
    added: list[str] = field(default_factory=list)   # descriptions of auto-added / enriched items
    conflicts: list[Conflict] = field(default_factory=list)
    # ADR-040 / US145: structured, per-decision change records (one FieldChange per
    # auto-decision) so the "what changed & why" surfaces render from data, not from
    # parsing the human-readable `added` strings. `added` is retained for back-compat.
    changes: list[FieldChange] = field(default_factory=list)
    # US161 (ADR-041 amended) — per-entity {extracted, stored, delta}; observational.
    reconciliation: dict[str, dict[str, int]] = field(default_factory=dict)
    # E037 PQ #4 — import-time reconciler ambiguities (N-option questions). Carried
    # on their own channel rather than coerced into the 2-value `conflicts` shape,
    # so they surface as a clean question + per-option buttons in the profile-review
    # interview instead of a garbled string.
    pending_confirmations: list[PendingConfirmation] = field(default_factory=list)


_LEGAL_SUFFIXES = frozenset({
    "gmbh", "se", "ag", "ggmbh", "kg", "kgaa", "ohg", "gbr",
    "inc", "ltd", "llc", "corp", "plc", "bv", "nv", "sa",
})
_STOPWORDS = frozenset({
    "des", "der", "die", "das", "und", "von", "am", "im", "an",
    "the", "of", "and", "&", "co",
})


def _company_tokens(name: str) -> frozenset[str]:
    """Return significant tokens from a company name.

    Strips common legal-form suffixes and stopwords, then returns tokens
    with more than 3 characters so noise like 'Co', 'BRK', 'SE' don't drive
    false matches.
    """
    tokens: list[str] = []
    for raw in name.lower().split():
        tok = raw.strip(".,-()")
        if tok in _LEGAL_SUFFIXES or tok in _STOPWORDS:
            continue
        if len(tok) > 3:
            tokens.append(tok)
    return frozenset(tokens)


def _company_names_match(a: str, b: str) -> bool:
    """Return True when a and b likely refer to the same employer.

    Exact match (fast path) or subset match: one name's significant-token
    set is a subset of the other's, indicating one is a shortened form of
    the same organisation (e.g. 'Roche' ⊆ 'Roche Diagnostics GmbH').
    """
    a_l = a.strip().lower()
    b_l = b.strip().lower()
    if a_l == b_l:
        return True
    ta = _company_tokens(a)
    tb = _company_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


# Public alias — employer-identity check shared with cv_diff (which maps a
# generated CV's work entries back onto the stored master profile).
company_names_match = _company_names_match


def _sort_work_by_date(entries: list[WorkEntry]) -> list[WorkEntry]:
    """Sort work entries reverse-chronologically (most recent first).

    Entries with no end_date (ongoing roles) sort first (treated as 9999-12).
    """
    def _key(e: WorkEntry) -> str:
        if not e.end_date:
            return "9999-12"
        return (e.end_date + "-12")[:7]

    return sorted(entries, key=_key, reverse=True)
