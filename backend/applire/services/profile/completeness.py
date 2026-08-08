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

"""Unified, role-aware completeness model for Master Profile work-experience entries.

This module is the single source of truth for BOTH the completeness score
(previously ``calculate_completeness``) and the gap list (previously
``gap_detector_mode_c``).  It is pure Python — no LLM calls, no DB, no
imports from ``interview_graph`` — to avoid circular dependencies.

Public API
----------
FLOOR_FIELDS          Always-expected fields for every work entry.
CONDITIONAL_FIELDS    Fields gated by role-conditional annotation.
expected_fields_for   Merge floor + role-annotated fields for one entry.
field_present         Single-field presence check (field-type aware).
entry_expected_present (present, expected) counts for one entry.
work_experience_richness  Mean present/expected across all entries.
field_gaps            Ordered gap strings; semantic superset of gap_detector_mode_c.
"""

from __future__ import annotations

from applire.utils.budget_unit import budget_needs_unit

__all__ = [
    "FLOOR_FIELDS",
    "CONDITIONAL_FIELDS",
    "expected_fields_for",
    "field_present",
    "entry_expected_present",
    "work_experience_richness",
    "field_gaps",
]

# ---------------------------------------------------------------------------
# Field sets
# ---------------------------------------------------------------------------

FLOOR_FIELDS: tuple[str, ...] = ("start_date", "end_date", "achievements")
"""Always expected for every work entry, regardless of role."""

CONDITIONAL_FIELDS: tuple[str, ...] = ("team_size", "budget_managed", "industry_context")
"""WorkEntry-only fields gated by the role-conditional annotation."""

# Emission priority for gap strings (controls order within each entry).
_EMISSION_ORDER: tuple[str, ...] = (
    "achievements",
    "team_size",
    "budget_managed",
    "industry_context",
    "start_date",
    "end_date",
)


# ---------------------------------------------------------------------------
# expected_fields_for
# ---------------------------------------------------------------------------

def expected_fields_for(entry: dict) -> list[str]:
    """Return the ordered list of fields expected to be present for *entry*.

    Floor fields come first (``start_date``, ``end_date``, ``achievements``),
    then any conditional fields the role annotation requests.

    If ``entry['expected_fields']`` is absent (``None`` / missing key) — meaning
    the entry has never been analysed — the function returns floor-only as a
    lean fallback (under-ask, not over-ask).

    ``entry['expected_fields']`` values that are not in ``CONDITIONAL_FIELDS``
    are silently dropped (future-proof / data-hygiene guard).
    """
    annotation: list[str] | None = entry.get("expected_fields")

    conditional: list[str]
    if annotation is None:
        # Never analysed → lean fallback
        conditional = []
    else:
        # Filter to known conditional fields, preserving annotation order
        cond_set = set(CONDITIONAL_FIELDS)
        conditional = [f for f in annotation if f in cond_set]

    return list(FLOOR_FIELDS) + conditional


# ---------------------------------------------------------------------------
# field_present
# ---------------------------------------------------------------------------

def field_present(entry: dict, field: str) -> bool:
    """Return True when *field* is considered "present" in *entry*.

    Rules (match gap_detector_mode_c semantics):
    - ``achievements``: value must be a non-empty list (``not entry.get(...)``
      is the gap signal, so ``bool(value)`` is the presence signal).
    - ``team_size``: value must not be ``None`` (0 is valid / present).
    - ``end_date``: present when non-empty OR the entry is marked as the
      current position (``is_current is True``, #155) — a current job has no
      end date by convention and must not be re-asked.
    - ``budget_managed``: present only when the wording carries a UNIT (#382).
      A bare ``"6000000"`` leaves the question the field was asked to answer
      still open — six million of what — and since Option A omits such a value
      from every delivered document, calling it "present" would score a profile
      as complete for a fact the CV cannot state. Marking it missing is what
      routes it to the enrichment interview, whose ``budget_managed`` question
      already asks for "size / currency". Never a guess: the unit is read from
      the candidate's own wording (``utils.budget_unit``) or asked for.
    - Everything else (``industry_context``, ``start_date``): truthy —
      non-empty string / not None.
    """
    value = entry.get(field)
    if field == "achievements":
        return bool(value)  # non-empty list
    if field == "team_size":
        return value is not None  # 0 is a valid team size
    if field == "end_date":
        # #155 — is_current=True means "ongoing"; null end_date is then intentional.
        return bool(value) or entry.get("is_current") is True
    if field == "budget_managed":
        return bool(value) and not budget_needs_unit(value)
    # industry_context, start_date
    # stricter than the old 'is None': an empty string counts as missing
    return bool(value)


# ---------------------------------------------------------------------------
# entry_expected_present
# ---------------------------------------------------------------------------

def entry_expected_present(entry: dict) -> tuple[int, int]:
    """Return ``(present_count, expected_count)`` for *entry*.

    Uses ``expected_fields_for`` to determine the expected set, then
    ``field_present`` for each field.
    """
    expected = expected_fields_for(entry)
    present = sum(1 for f in expected if field_present(entry, f))
    return present, len(expected)


# ---------------------------------------------------------------------------
# work_experience_richness
# ---------------------------------------------------------------------------

def work_experience_richness(work_experience: list[dict]) -> float:
    """Mean richness (present / expected) across all work-experience entries.

    Returns ``0.0`` when the list is empty.  Entries whose expected set is
    empty are treated as fully present (ratio = 1.0) to avoid division by zero
    and to not penalise entries that have no requirements at all.
    """
    if not work_experience:
        return 0.0

    total = 0.0
    for entry in work_experience:
        present, expected = entry_expected_present(entry)
        ratio = present / expected if expected > 0 else 1.0
        total += ratio

    return total / len(work_experience)


# ---------------------------------------------------------------------------
# _entry_label  (internal — mirrors gap_detector_mode_c label logic exactly)
# ---------------------------------------------------------------------------

def _entry_label(entry: dict) -> str:
    """Compute the display label for a work-experience entry.

    Reproduces the exact logic from ``gap_detector_mode_c``::

        company = (entry.get("company") or "").strip()
        role    = (entry.get("role") or entry.get("title") or "").strip()
        label   = f"{role} @ {company}".strip(" @")
    """
    company = (entry.get("company") or "").strip()
    role = (entry.get("role") or entry.get("title") or "").strip()
    return f"{role} @ {company}".strip(" @")


# ---------------------------------------------------------------------------
# field_gaps
# ---------------------------------------------------------------------------

def field_gaps(profile: dict, scope: str | None = None) -> list[str]:
    """Return ordered gap strings for the profile, role-aware.

    This function is a **semantic superset** of ``gap_detector_mode_c``
    (``interview_graph.py``).  Preserved parity points:

    * Entry label: ``f"{role} @ {company}".strip(" @")``
    * Scope filter: ``"work_experience:<company>:<role>"`` — case-insensitive.
    * ``_meta.na_fields`` suppression applied before appending.
    * ``professional_summary`` tail: emitted only when ``scope is None`` and
      ``work_experience`` is non-empty (exact gap-string: ``"professional_summary"``).

    Additions beyond the old detector:
    * ``start_date`` / ``end_date`` **floor-field gaps** are now emitted (the
      old detector never checked these fields).
    * ``team_size`` / ``budget_managed`` / ``industry_context`` gaps are
      **role-aware**: they are only emitted when the entry's annotation
      (``expected_fields``) requests them, suppressing noise for IC entries.
    """
    na_fields: set[str] = set(
        (profile.get("_meta") or {}).get("na_fields", [])
    )
    gaps: list[str] = []
    work_experience: list[dict] = profile.get("work_experience") or []

    for entry in work_experience:
        label = _entry_label(entry)

        # Scope filter — mirrors gap_detector_mode_c exactly
        if scope:
            parts = scope.split(":", 2)
            if len(parts) == 3:
                scope_company, scope_role = parts[1].strip(), parts[2].strip()
                entry_company = (entry.get("company") or "").strip()
                entry_role = (entry.get("role") or entry.get("title") or "").strip()
                if (
                    entry_company.lower() != scope_company.lower()
                    or entry_role.lower() != scope_role.lower()
                ):
                    continue

        expected_set: set[str] = set(expected_fields_for(entry))

        # Emit gaps in _EMISSION_ORDER, limited to expected fields
        for field in _EMISSION_ORDER:
            if field not in expected_set:
                continue
            if field_present(entry, field):
                continue
            gap_str = f"{field}: {label}"
            if gap_str not in na_fields:
                gaps.append(gap_str)

    # Professional summary tail — exact parity with gap_detector_mode_c
    if scope is None and work_experience:
        summary_gap = "professional_summary"
        if not profile.get("professional_summary") and summary_gap not in na_fields:
            gaps.append(summary_gap)

    return gaps
