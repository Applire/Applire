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
US160 (E033 / ADR-041 amended) — deterministic Profile Health assessment.

``assess_health`` is a pure function over a parsed ``MasterProfileData``: it
composes the Tier-2 health sources into one structured read with **no LLM and no
DB access**, so the Master Profile Health hub (US164) and the JD-gap interview
(US163) can act on a single contract.

Sources (epic Task 5):
  - **conflict** thread  ← unresolved ``metadata.pending_conflicts`` (ADR-013),
                           severity-tagged by ``classify_conflict``.
  - **accuracy** thread  ← merge ``enrichment_history`` records, severity from
                           ``escalate(classify_reconciliation, classify_confidence)``
                           (US161 data-loss delta + low merge confidence; US162).
  - **completeness**     ← ``calculate_completeness`` score + ``completeness_gaps``
                           (E026 / US104) — score-only, never severity-tagged.

Architecture boundary (ADR-041 amended / epic Task 5):
- Deterministic, no LLM. Reads only existing durable Master-Profile state — never
  the 7-day upload (ADR-005).
- The destructive Tier-1 gate (US167) is *not* surfaced here; it is escalated into
  the interview by US163. This endpoint reports the additive Tier-2 axis only.
"""
from __future__ import annotations

from applire.schemas.profile import (
    CompletenessBlock,
    Conflict,
    EnrichmentRecord,
    HealthIssue,
    MasterProfileData,
    ProfileHealthResponse,
)
from applire.services.profile.severity import (
    classify_conflict,
    classify_confidence,
    classify_reconciliation,
    escalate,
)


def _conflict_issue(conflict: Conflict) -> HealthIssue:
    return HealthIssue(
        id=f"conflict:{conflict.conflict_id}",
        thread="conflict",
        profile_mismatch_severity=classify_conflict(conflict),
        summary=(
            f"{conflict.section}.{conflict.field}: "
            f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
        ),
        field_ref=conflict.field,
        source_record_ref=conflict.source,
    )


def _reconciliation_loss(reconciliation: dict[str, dict[str, int]] | None) -> int:
    if not reconciliation:
        return 0
    return sum(max(0, entity.get("delta", 0)) for entity in reconciliation.values())


def _accuracy_issue(record: EnrichmentRecord) -> HealthIssue | None:
    """An accuracy issue for a merge that lost data and/or merged at low confidence.

    Clean, high-confidence, no-loss merges (and non-merge records like manual
    edits) emit nothing — ``escalate`` returns ``None`` and we skip the record.
    """
    severity = escalate(
        classify_reconciliation(record.reconciliation),
        classify_confidence(record.confidence),
    )
    if severity is None:
        return None

    lost = _reconciliation_loss(record.reconciliation)
    if lost:
        affected = sorted(
            section
            for section, entity in (record.reconciliation or {}).items()
            if entity.get("delta", 0) > 0
        )
        summary = (
            f"Merge from {record.source} did not retain {lost} extracted "
            f"item(s) ({', '.join(affected)})"
        )
        field_ref = ", ".join(affected) or None
    else:
        pct = f"{record.confidence:.0%}" if record.confidence is not None else "?"
        summary = f"Low-confidence merge from {record.source} ({pct})"
        field_ref = None

    return HealthIssue(
        id=f"accuracy:{record.id}",
        thread="accuracy",
        profile_mismatch_severity=severity,
        summary=summary,
        field_ref=field_ref,
        source_record_ref=record.id,
    )


def assess_health(profile: MasterProfileData) -> ProfileHealthResponse:
    """Compute the deterministic Tier-2 health read for a parsed profile."""
    issues: list[HealthIssue] = []

    metadata = profile.metadata
    if metadata is not None:
        issues.extend(
            _conflict_issue(c)
            for c in metadata.pending_conflicts
            if not c.resolved
        )
        for record in metadata.enrichment_history:
            issue = _accuracy_issue(record)
            if issue is not None:
                issues.append(issue)

    return ProfileHealthResponse(
        issues=issues,
        completeness=CompletenessBlock(
            score=profile.calculate_completeness(),
            gaps=profile.completeness_gaps(),
        ),
    )
