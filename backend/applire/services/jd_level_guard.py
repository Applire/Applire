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

"""ADR-069 clause 4 — the deterministic level guard for the job-analysis review loop.

ADR-062 classification (declared per clause 6): this module computes a FACT.
Whether a concept sits in ``required_skills`` or ``nice_to_have_skills``, and
whether that placement changed between two review rounds, is a set-membership
diff over stored values — one correct answer, no prose read for meaning. The
JUDGEMENT (is this particular move justified by the posting's wording?) stays
with the model: the reviewer requests moves, the corrector performs and
DECLARES them in ``level_changes``. This guard only enforces that performed
moves were declared — silence keeps the previous level.

Earned by (provenance per applire-prompt-first): charter run 12,
``operations_marcus_de``, LLM log 2026-07-31 18:05:15-18:05:25 — the review
loop shipped an approved state in which "SAP", required by the posting
("Sicherer Umgang mit SAP"), had been silently demoted to
``nice_to_have_skills`` across correction rounds. A prompt disposition rule
alone is the weaker control (a prompt schema can be a dead control — #229),
so the invariant gets the ADR-064 treatment: the model states the fact, code
compares it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_LEVEL_FIELDS: dict[str, str] = {
    "required": "required_skills",
    "nice_to_have": "nice_to_have_skills",
}


def _norm(concept: Any) -> str:
    """Case- and whitespace-folded identity for a concept string."""
    if not isinstance(concept, str):
        return ""
    return " ".join(concept.casefold().split())


def _levels(draft: dict[str, Any]) -> dict[str, str]:
    """norm(concept) -> level for the two levelled lists of one draft.

    A concept present in BOTH lists reads as ``required`` (the stronger
    placement wins — duplicates across the lists are an upstream defect this
    guard must not amplify by picking the weaker reading).
    """
    out: dict[str, str] = {}
    for entry in draft.get("nice_to_have_skills") or []:
        key = _norm(entry)
        if key:
            out[key] = "nice_to_have"
    for entry in draft.get("required_skills") or []:
        key = _norm(entry)
        if key:
            out[key] = "required"
    return out


def _declared_moves(draft: dict[str, Any]) -> dict[str, str]:
    """norm(concept) -> declared target level from a draft's ``level_changes``."""
    declared: dict[str, str] = {}
    for change in draft.get("level_changes") or []:
        if not isinstance(change, dict):
            continue
        target = change.get("to")
        key = _norm(change.get("concept"))
        if key and target in _LEVEL_FIELDS:
            declared[key] = target
    return declared


def apply_jd_level_guard(
    settled: dict[str, Any], draft_history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Revert undeclared required/nice-to-have level moves in a settled JD analysis.

    Walks the review loop's drafts in order, maintaining the authorised level
    per concept: the initial extraction's placement, updated only by moves the
    corrector declared in that round's ``level_changes``. Concepts new in a
    later round adopt their placement (additions are content, not moves);
    concepts removed entirely stay removals (the guard never resurrects — a
    removal for fabrication is the reviewer's legitimate call). The settled
    draft's placements are then forced back to the authorised level wherever
    they differ, and the transport field ``level_changes`` is stripped.
    """
    if not draft_history:
        return settled

    authorised = _levels(draft_history[0])
    for draft in draft_history[1:]:
        declared = _declared_moves(draft)
        current = _levels(draft)
        for key, level in current.items():
            prior = authorised.get(key)
            if prior is None:
                authorised[key] = level
            elif level != prior and declared.get(key) == level:
                authorised[key] = level
            # else: undeclared move — authorised level stands.

    result = dict(settled)
    result.pop("level_changes", None)
    settled_levels = _levels(result)
    reverted: list[str] = []

    for key, level in settled_levels.items():
        target = authorised.get(key)
        if target is None or target == level:
            continue
        wrong_field = _LEVEL_FIELDS[level]
        right_field = _LEVEL_FIELDS[target]
        moved_entries = [
            e for e in (result.get(wrong_field) or []) if _norm(e) == key
        ]
        if not moved_entries:
            continue
        result[wrong_field] = [
            e for e in (result.get(wrong_field) or []) if _norm(e) != key
        ]
        right_list = list(result.get(right_field) or [])
        if all(_norm(e) != key for e in right_list):
            right_list.append(moved_entries[0])
        result[right_field] = right_list
        reverted.append(f"{moved_entries[0]!r}: {level} -> {target}")

    if reverted:
        logger.warning(
            "jd_level_guard: reverted %d undeclared level move(s) in the settled "
            "job analysis — %s (ADR-069 clause 4: a level move the corrector did "
            "not declare in level_changes keeps its prior level).",
            len(reverted),
            "; ".join(reverted),
        )
    return result
