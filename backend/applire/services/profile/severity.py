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
US162 (E033 / ADR-041 amended) — profile-mismatch severity classifier.

Tags each POST-MERGE (Tier-2) profile issue with ``profile_mismatch_severity``
= ``info | review | critical`` so the Master Profile Health hub (US160/US164)
and the JD-gap interview (US163) can route it. Deterministic, no LLM.

Trigger classes (one classifier each, composed by US160's health endpoint):
  - deferred Tier-1 gate (US167)          → always ``critical`` (``GATE_SEVERITY``)
  - merge data-loss reconciliation delta  → ``critical`` above a tunable threshold,
                                            else ``review`` for any non-zero loss
  - post-merge conflict (dates/titles)    → ``review``; cosmetic fields → ``info``
  - low merge confidence (< threshold)    → ``review``

Architecture boundary (ADR-041 amended / epic Task 3):
- Tier-2 axis ONLY. The destructive Tier-1 cases (not-a-CV, name divergence) are
  gated *before* the merge commits in US167 — this module only *reports* an
  already-deferred gate as ``critical``; it never re-runs the gate.
- ``profile_mismatch_severity`` is deliberately distinct from the ADR-021
  *reviewer* severity — do not conflate the two.
- Thresholds live in ``constants`` and are read at call time so they stay
  env-overridable (ADR-035 precedent).
"""
from __future__ import annotations

from typing import Literal

from applire import constants
from applire.schemas.profile import Conflict

ProfileMismatchSeverity = Literal["info", "review", "critical"]

# Higher number = more severe; used by ``escalate`` to combine triggers.
SEVERITY_ORDER: dict[ProfileMismatchSeverity, int] = {
    "info": 0,
    "review": 1,
    "critical": 2,
}

# A parked/unresolved Tier-1 gate (US167) is the canonical non-deferrable issue.
GATE_SEVERITY: ProfileMismatchSeverity = "critical"

# Conflict fields that represent a genuine factual contradiction worth a review
# (reclassified from critical by ADR-041 amended). Everything else is cosmetic.
_REVIEW_CONFLICT_FIELDS = frozenset(
    {"start_date", "end_date", "title", "position", "role"}
)


def classify_reconciliation(
    reconciliation: dict[str, dict[str, int]] | None,
) -> ProfileMismatchSeverity | None:
    """Severity for a merge count-reconciliation block (US161).

    Sums the per-entity ``delta`` (extracted-but-not-stored data points). Total
    strictly above ``MERGE_DATALOSS_CRITICAL_THRESHOLD`` → ``critical``; any
    smaller non-zero loss → ``review``; no loss → ``None`` (not an issue).
    """
    if not reconciliation:
        return None
    total_delta = sum(
        max(0, entity.get("delta", 0)) for entity in reconciliation.values()
    )
    if total_delta == 0:
        return None
    if total_delta > constants.MERGE_DATALOSS_CRITICAL_THRESHOLD:
        return "critical"
    return "review"


def classify_conflict(conflict: Conflict) -> ProfileMismatchSeverity:
    """Severity for a post-merge conflict (ADR-013 ``master_profile.conflicts``).

    Date/title contradictions → ``review`` (reclassified from critical by
    ADR-041 amended); cosmetic field differences → ``info``.
    """
    if conflict.field in _REVIEW_CONFLICT_FIELDS:
        return "review"
    return "info"


def classify_confidence(confidence: float | None) -> ProfileMismatchSeverity | None:
    """Severity from a merge ``EnrichmentRecord.confidence``.

    Below ``MERGE_CONFIDENCE_REVIEW_THRESHOLD`` → ``review``; at/above it, or a
    record with no confidence (e.g. a manual edit) → ``None``.
    """
    if confidence is None:
        return None
    if confidence < constants.MERGE_CONFIDENCE_REVIEW_THRESHOLD:
        return "review"
    return None


def escalate(
    *severities: ProfileMismatchSeverity | None,
) -> ProfileMismatchSeverity | None:
    """Return the highest severity among the triggers, ignoring ``None``.

    Lets US160 combine several triggers on one issue (e.g. a low-confidence merge
    that also lost data) into a single ``profile_mismatch_severity``.
    """
    present = [s for s in severities if s is not None]
    if not present:
        return None
    return max(present, key=lambda s: SEVERITY_ORDER[s])
