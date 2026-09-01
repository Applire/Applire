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
  - **confirmation**     ← unresolved ``metadata.pending_confirmations`` (#333) —
                           N-option ambiguities parked by import, testimony or
                           agent claims, always ``review``.
  - **accuracy** thread  ← merge ``enrichment_history`` records, severity from
                           ``escalate(classify_reconciliation, classify_confidence)``
                           (US161 data-loss delta + low merge confidence; US162).
  - **unit** thread      ← #382 (PO decision 2026-08-08): a work entry whose
                           ``budget_managed`` states no unit, and which the CV
                           therefore omits. Always ``review``.
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
    Certification,
    CompletenessBlock,
    Conflict,
    EducationEntry,
    EnrichmentRecord,
    ExperienceBase,
    HealthIssue,
    Language,
    MasterProfileData,
    PendingConfirmation,
    ProfileHealthResponse,
    Publication,
    SignatureStory,
    Skill,
)
from applire.services.profile.completeness import _entry_label
from applire.services.profile.completeness import field_gaps as completeness_field_gaps
from applire.services.profile.severity import (
    classify_conflict,
    classify_confidence,
    classify_reconciliation,
    escalate,
)
from applire.utils.budget_unit import budget_needs_unit
from applire.utils.display import format_display_value


def _resolve_entity(profile: MasterProfileData, entity_id: str | None) -> object | None:
    """The id-bearing profile entity ``entity_id`` names, or ``None`` (#626).

    Searches every section :func:`applire.services.profile.reconcile.apply.
    resolve_any` can target — work/project/volunteer plus the six sections
    #619 added it for — rather than trusting a ``Conflict.section`` string
    (defensive: cheap, and correct the day ``_apply_flag_conflict`` widens
    from its current experience-only ``resolve()`` to ``resolve_any``, the way
    ``_apply_set_field`` already did).

    ``None`` covers two legitimate cases the caller must not crash on: a
    profile-level conflict (``entity_id`` was never set — #218's own docstring:
    ``professional_summary`` / ``personal_info`` disputes have no entity) and a
    STALE id (the entity existed when the conflict was flagged but was since
    edited or removed — nothing sweeps ``metadata.pending_conflicts`` when its
    target entity disappears).
    """
    if not entity_id:
        return None
    for entry in (
        *profile.work_experience,
        *profile.projects,
        *profile.volunteer_activities,
        *profile.education,
        *profile.certifications,
        *profile.languages,
        *profile.publications,
        *profile.skills,
        *profile.signature_stories,
    ):
        if getattr(entry, "id", None) == entity_id:
            return entry
    return None


def _entity_label(entity: object | None) -> str | None:
    """Human label for a resolved id-bearing entity, or ``None`` (#626).

    Mirrors the isinstance ladder ``_section_for`` (reconcile/apply.py) uses
    for the reverse mapping (entity → section name). The "X @ Y" shape matches
    ``_unit_issues`` below (``completeness._entry_label``) exactly, so the
    Health hub speaks one convention for every entry label it shows — for the
    three ``ExperienceBase`` kinds via the polymorphic ``org_label()``
    (company / project name / organization), and by the equivalent "specific
    @ broader" pairing for the rest (degree @ institution, cert name @ issuing
    org). A single-value entity (language, publication title, skill name,
    story title) has no "@" counterpart and is shown bare.
    """
    if entity is None:
        return None
    if isinstance(entity, ExperienceBase):
        return _entry_label({"company": entity.org_label(), "role": entity.role})
    if isinstance(entity, EducationEntry):
        return _entry_label({"company": entity.institution, "role": entity.degree})
    if isinstance(entity, Certification):
        return _entry_label({"company": entity.issuing_organization, "role": entity.name})
    if isinstance(entity, Language):
        return entity.language
    if isinstance(entity, Publication):
        return entity.title
    if isinstance(entity, Skill):
        return entity.name
    if isinstance(entity, SignatureStory):
        return entity.title
    return None


def _conflict_issue(conflict: Conflict, profile: MasterProfileData) -> HealthIssue:
    """#626 — name the entry a conflict hangs off, not just the field.

    Before this, the summary read ``work_experience.end_date: '2019-12' vs
    '2020-01'`` with no way to tell WHICH job — the reported defect, verbatim.
    ``Conflict.entity_id`` (#218) already carried the answer; resolve it.
    """
    entity = _resolve_entity(profile, conflict.entity_id)
    label = _entity_label(entity)
    existing_display = format_display_value(conflict.existing_value)
    incoming_display = format_display_value(conflict.incoming_value)
    return HealthIssue(
        id=f"conflict:{conflict.conflict_id}",
        thread="conflict",
        profile_mismatch_severity=classify_conflict(conflict),
        summary=(
            f"{(label + ': ') if label else ''}"
            f"{conflict.section}.{conflict.field}: "
            f"'{existing_display}' "
            f"vs '{incoming_display}'"
        ),
        field_ref=conflict.field,
        source_record_ref=conflict.source,
        entity_label=label,
        section=conflict.section or None,
        field=conflict.field,
        existing_value_display=existing_display,
        incoming_value_display=incoming_display,
        # `conflict.source` is the INCOMING side's provenance only — see the
        # `HealthIssue.existing_source` field docstring for why the existing
        # side's provenance is not recoverable here.
        existing_source=None,
        incoming_source=conflict.source,
    )


def _confirmation_issue(confirmation: PendingConfirmation) -> HealthIssue:
    """#333 — a parked reconciler ambiguity, surfaced so the human can reach it.

    Parked confirmations were write-only: `submit_testimony` and CV import both
    persist them, but the hub composed its read from `pending_conflicts` alone,
    so nothing rendered and the hub's "Resolve" entry point into the
    profile-review interview never existed for them. The interview itself has
    walked and resolved confirmations since E037 PQ #4 (`_open_confirmations` →
    `build_confirmation_clusters` → `resolve_confirmation`); only the read that
    makes them visible was missing.

    Severity is always ``review``: an unanswered identity question is real and
    actionable but never blocks a document — ADR-041 amended reclassified the
    equivalent conflict class down from ``critical`` for the same reason.

    #626 checked and this does NOT share the conflict thread's "which entry"
    defect: a confirmation has no separate ``entity_id`` to resolve because
    every ``RequestConfirmation.question`` already embeds the entity identity
    in its own prose at construction time (e.g. ``"'Senior Developer at Acme
    Corp' looks close to an existing position (...)"`` — see the near-dupe and
    attribution confirmations in ``reconcile/apply.py`` and
    ``reconcile/attribution.py``). Nothing to resolve here; left unchanged.
    """
    return HealthIssue(
        id=f"confirmation:{confirmation.confirmation_id}",
        thread="confirmation",
        profile_mismatch_severity="review",
        summary=confirmation.question,
        field_ref=None,
        source_record_ref=confirmation.source or None,
    )


def _unit_issues(profile: MasterProfileData) -> list[HealthIssue]:
    """#382 (PO decision 2026-08-08, Option A) — one issue per work entry whose
    budget figure states no unit.

    Option A omits such a value from every delivered document. The PO condition
    on that omission is that it is **addressed to the user**, never silent, so
    this thread exists to say out loud what the CV is no longer saying: the
    figure is in the vault, it is not on the page, and one answer puts it back.

    Its own thread rather than ``accuracy``: nothing here is a merge defect or a
    disagreement between two sources. The value is exactly what the candidate
    said; what is missing is the unit that would make it mean something. Severity
    is ``review`` — real and actionable, but a document still generates (it
    simply omits the line), which is the same call ADR-041 amended made for the
    equivalent conflict class.

    ``source_record_ref`` carries the **entry label**, not ``WorkEntry.id``:
    ``id`` has a UUID default factory, so an entry persisted before that field
    existed is re-keyed on every load and could never satisfy ``HealthIssue.id``'s
    "stable, deterministic" contract. The label is also the join key
    ``completeness.field_gaps`` emits and the master profile page already knows,
    so the page can put the fix affordance next to the affected field.
    """
    issues: list[HealthIssue] = []
    for entry in profile.work_experience or []:
        if not budget_needs_unit(entry.budget_managed):
            continue
        label = _entry_label({"company": entry.company, "role": entry.role})
        issues.append(
            HealthIssue(
                id=f"unit:budget_managed:{label}",
                thread="unit",
                profile_mismatch_severity="review",
                # The stored value is quoted so the user recognises which figure
                # is meant — it is their own answer, not a system value.
                summary=(
                    f"budget_managed: '{entry.budget_managed}' states no unit, so it is "
                    f"omitted from generated documents ({label})"
                ),
                field_ref="work_experience.budget_managed",
                source_record_ref=label,
            )
        )
    return issues


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


def assess_health(
    profile: MasterProfileData,
    na_fields: list[str] | None = None,
) -> ProfileHealthResponse:
    """Compute the deterministic Tier-2 health read for a parsed profile.

    ``na_fields`` — an explicit override for the ``_meta.na_fields`` list.
    Since #505, ``_meta`` is a declared field that survives ``model_dump()``,
    so *profile* normally carries its own suppressions and this argument can be
    omitted; when supplied it replaces the block. Defaults to ``None`` — use
    whatever the profile carries.
    """
    issues: list[HealthIssue] = _unit_issues(profile)

    metadata = profile.metadata
    if metadata is not None:
        issues.extend(
            _conflict_issue(c, profile)
            for c in metadata.pending_conflicts
            if not c.resolved
        )
        issues.extend(
            _confirmation_issue(c)
            for c in metadata.pending_confirmations
            if not c.resolved
        )
        for record in metadata.enrichment_history:
            issue = _accuracy_issue(record)
            if issue is not None:
                issues.append(issue)

    profile_dict = profile.model_dump()
    if na_fields:
        profile_dict["_meta"] = {"na_fields": na_fields}

    return ProfileHealthResponse(
        issues=issues,
        completeness=CompletenessBlock(
            score=profile.calculate_completeness(),
            gaps=profile.completeness_gaps(),
            field_gaps=completeness_field_gaps(profile_dict),
        ),
    )
