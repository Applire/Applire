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

"""Deterministic near-duplicate detection for profile entry sections (#177).

ADR-046 (amended 2026-07-16) generalises the skills path's three-band policy
(#172) to every entity section:

* MATCH      — every evidenced identity field is exact or a strict near-dupe
               → safe to auto-merge (fill empties, never overwrite)
* AMBIGUOUS  — related only by bare single-token containment on some field
               → RequestConfirmation, never guess
* DISTINCT   — append as a new entry

Built on the shared tokeniser (ats_audit.skill_tokens), so formatting and
morphological variants land on one token set. Section-agnostic on purpose: a
future entry kind inherits the policy by declaring its identity fields at its
call site instead of re-implementing a predicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from applire.services.ats_audit import (
    skill_tokens,
    skills_near_dupe,
    skills_single_token_containment,
)

_DISTINCT, _AMBIG, _SAME = 0, 1, 2


def _field_relation(a: str | None, b: str | None, *, containment_is_same: bool) -> int | None:
    """Relation of one identity-field pair; None = no evidence (a side is empty)."""
    if not a or not b:
        return None
    if skill_tokens(a) == skill_tokens(b) or skills_near_dupe(a, b):
        return _SAME
    if skills_single_token_containment(a, b):
        # Closed domains (languages; org names with/without their legal form)
        # may treat containment as identity; open domains ask the user.
        return _SAME if containment_is_same else _AMBIG
    return _DISTINCT


@dataclass
class DupeVerdict:
    match: Any | None = None
    ambiguous: list[Any] = field(default_factory=list)


def classify_dupe(
    incoming: dict[str, str | None],
    existing_entries: list[Any],
    getters: dict[str, Callable[[Any], str | None]],
    *,
    containment_is_same: bool = False,
) -> DupeVerdict:
    verdict = DupeVerdict()
    for entry in existing_entries:
        relations = [
            _field_relation(incoming.get(name), getter(entry),
                            containment_is_same=containment_is_same)
            for name, getter in getters.items()
        ]
        evidenced = [r for r in relations if r is not None]
        if not evidenced or any(r == _DISTINCT for r in evidenced):
            continue
        if all(r == _SAME for r in evidenced):
            verdict.match = entry
            return verdict
        verdict.ambiguous.append(entry)
    return verdict


def _month(iso_date: str) -> str:
    return iso_date[:7]


def classify_engagement_dupe(
    *,
    org: str | None,
    role: str | None,
    start_date: str | None,
    existing: list[Any],
    org_getter: Callable[[Any], str | None],
) -> DupeVerdict:
    """New-entry guard for ExperienceBase engagements (work/project/volunteer).

    The LLM reconciler owns entity identity (ADR-046) — this fires only when it
    said "new entry" (no target). MATCH needs a strong signal: org near-dupe AND
    equal start month on both sides. Org near-dupe with a matching/contained
    role but absent or differing dates is AMBIGUOUS → confirmation.

    ADR-046 (amended 2026-07-16, #177 review): bare single-token org containment
    ('Ford' ⊂ 'Ford Foundation') is NEVER identity here — two distinct employers
    can share one token. It always routes to AMBIGUOUS (ask), regardless of
    dates; only a 2+-token containment or a full near-dupe counts as SAME and
    can go on to the date-based MATCH check below.

    ADR-046 (amended 2026-07-16, #181 review): once the org is a strong match
    (SAME) but the start months don't confirm one stint, the only way to APPEND
    silently is a clearly DISTINCT role — that's a genuine second position at the
    same employer. Any weaker role signal (near/exact role, or NO role evidence at
    all) is ambiguous → ask. The old rule appended silently when role was absent,
    which could hide a duplicate whenever the reconciler omitted the role.
    """
    verdict = DupeVerdict()
    for entry in existing:
        org_rel = _field_relation(org, org_getter(entry), containment_is_same=False)
        if org_rel == _AMBIG:
            verdict.ambiguous.append(entry)
            continue
        if org_rel != _SAME:
            continue
        entry_start = getattr(entry, "start_date", None)
        if start_date and entry_start and _month(start_date) == _month(entry_start):
            verdict.match = entry
            return verdict
        role_rel = _field_relation(role, getattr(entry, "role", None),
                                   containment_is_same=False)
        # Strong org, unconfirmed dates: append only when the roles clearly differ
        # (_DISTINCT). Otherwise — including no role evidence (role_rel is None) — ask.
        if role_rel != _DISTINCT:
            verdict.ambiguous.append(entry)
    return verdict
