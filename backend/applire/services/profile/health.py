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
  - **span_provenance**  ← #764 (ADR-061 cl. 7 amended 2026-10-16): a skill span
                           labelled ``transcribed`` that no source text states
                           and that equals the role-date re-derivation — the
                           fingerprint of a span laundered before the
                           2026-10-07 fix (``span_provenance.laundered_spans``).
                           Always ``review``; carries its own recompute action.
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
    ImportNotApplied,
    HealthIssue,
    MasterProfileData,
    PendingConfirmation,
    ProfileHealthResponse,
)
from applire.services.profile.completeness import _entry_label
from applire.services.profile.completeness import field_gaps as completeness_field_gaps
# #604 — the entity-label ladder #626 introduced now lives in its own module,
# because the live interview's conflict card needs the identical resolution
# (see `entity_label.py`'s docstring). Imported, never re-derived.
from applire.services.profile.entity_label import entity_label as _entity_label
from applire.services.profile.entity_label import resolve_entity as _resolve_entity
from applire.services.profile.span_provenance import laundered_spans
from applire.services.profile.severity import (
    classify_conflict,
    classify_confidence,
    classify_reconciliation,
    escalate,
)
from applire.services.finding_registry import classify as _classify_kind
from applire.services.profile.decisions import conflict_shape
from applire.services.profile.entry_identity import open_pairs
from applire.utils.budget_unit import budget_needs_unit
from applire.utils.display import format_display_value


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
        # UAT-3d: the closed outcome list this dispute allows (ADR-099 cl. 11).
        outcomes=list(
            conflict_shape(conflict.section, conflict.field, conflict.entity_id)[1]
        ),
    )


#: G-5 / ruling V-5 (c) — confirmation families the Health hub answers with
#: the ask's own options (each answer is a complete act in the applier's
#: ``_apply_resolve_confirmation``); every other family goes to the interview.
#: D-8 — ``project_parent`` (family 6): the answer writes the project's position.
_ANSWERED_IN_PLACE = frozenset({"denial_scope", "project_parent"})


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
    context = confirmation.context if isinstance(confirmation.context, dict) else {}
    kind = context.get("kind") if isinstance(context.get("kind"), str) else None
    answerable = kind in _ANSWERED_IN_PLACE and bool(confirmation.options)
    return HealthIssue(
        id=f"confirmation:{confirmation.confirmation_id}",
        thread="confirmation",
        profile_mismatch_severity="review",
        summary=confirmation.question,
        field_ref=None,
        source_record_ref=confirmation.source or None,
        # G-5 / #604 — the reader's language, not the English plain field.
        question_i18n=confirmation.question_i18n or None,
        confirmation_id=confirmation.confirmation_id,
        confirmation_kind=kind,
        # V-5 (c): only a family whose answer is a complete act is answered in
        # place; every other family keeps "Beheben" (the interview).
        options=list(confirmation.options) if answerable else None,
        options_i18n=(
            list(confirmation.options_i18n)
            if answerable and confirmation.options_i18n
            and len(confirmation.options_i18n) == len(confirmation.options)
            else None
        ),
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
                budget_value=str(entry.budget_managed),
                entry_id=getattr(entry, "id", None),
            )
        )
    return issues


def _span_provenance_issues(profile: MasterProfileData) -> list[HealthIssue]:
    """#764 — one ``review`` issue per laundered transcribed skill span.

    ``id`` is keyed on the skill NAME, not ``Skill.id``: an id minted by the
    default factory on a blob that never persisted one would re-key on every
    load (the ``_unit_issues`` reasoning). ``skill_id`` still rides along — it
    is what the recompute action addresses, and the action re-checks the
    detector on the current vault, so a stale id is refused, never written.
    The English ``summary`` is the server fallback; the hub composes the
    sentence from the structured fields (``profileHealth.launderedSpan.*``).
    """
    return [
        HealthIssue(
            id=f"span_provenance:{span.skill_name}",
            thread="span_provenance",
            profile_mismatch_severity="review",
            summary=(
                f"{span.skill_name}: {span.span_years} years is marked as something you "
                f"said, but nothing you wrote or told us states it. Your role dates "
                f"give {span.recomputed_years} years."
            ),
            field_ref="skills.years_experience",
            source_record_ref=span.skill_name,
            skill_id=span.skill_id,
            skill_name=span.skill_name,
            span_years=span.span_years,
            recomputed_years=span.recomputed_years,
        )
        for span in laundered_spans(profile)
    ]


_EXCLUSIVE_CATEGORIES = frozenset({"technical", "soft"})


def _suspect_binding_issues(profile: MasterProfileData) -> list[HealthIssue]:
    """FS-5 (Hazelnut build 4, founder 2026-10-23; ADR-046 amended 2026-10-24) —
    a READ-ONLY check over the recorded skill bindings: a receipt whose
    incoming entry (as the document stated it) and its target sit on opposite
    sides of technical↔soft — the #801 guard's category test, which bindings
    written before the guard never met (the audit vault: "REST APIs" on
    Mentoring). One ``review`` issue per pair, never a write; answered by the
    existing acts ("Ja" stamps ``confirmed_at``, "Nein" is the #717 undo), so
    an answered or undone pair no longer shows."""
    skills = {s.id: s for s in profile.skills if getattr(s, "id", None)}
    trail = profile.metadata.enrichment_history if profile.metadata else []
    seen: set[tuple[str, str]] = set()
    issues: list[HealthIssue] = []
    for record in reversed(trail):
        for receipt in record.matched:
            if receipt.section != "skills" or receipt.basis == "name_table":
                continue
            key = (receipt.entity_id, receipt.incoming.strip().casefold())
            if key in seen:
                continue
            seen.add(key)  # the NEWEST receipt of a pair decides
            if receipt.undone_at is not None or receipt.confirmed_at is not None:
                continue
            target = skills.get(receipt.entity_id)
            incoming_category = (receipt.incoming_entry or {}).get("category")
            target_category = getattr(target, "category", None)
            if (
                target is None
                or incoming_category not in _EXCLUSIVE_CATEGORIES
                or target_category not in _EXCLUSIVE_CATEGORIES
                or incoming_category == target_category
            ):
                continue
            issues.append(HealthIssue(
                id=f"suspect_binding:{receipt.entity_id}:{key[1]}",
                thread="suspect_binding",
                profile_mismatch_severity="review",
                summary=f"Is {receipt.incoming} the same as {target.name}?",
                field_ref="skills.name",
                source_record_ref=record.id,
                section="skills",
                skill_id=receipt.entity_id,
                skill_name=target.name,
                incoming_name=receipt.incoming,
            ))
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
        # Ruling U-4 (2026-09-15, founder UAT on the Nougat RC) — the SAME lost
        # items must not render as TWO Health cards. Once a record carries a
        # `not_applied` witness (#615 onward), `_not_applied_issue` (V-6/V-7)
        # already names this fact — with WHY, per item — so this thread's loss
        # branch retires for that record rather than duplicating it at a
        # different (and inconsistent) severity. A record predating #615 has
        # no witness at all; its loss issue is the only record of the loss and
        # keeps firing exactly as before. The low-confidence branch below is
        # untouched — it fires from `record.confidence` alone, nothing to do
        # with `not_applied`.
        if record.not_applied:
            return None
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


#: How each `ImportNotApplied.reason` is explained to the candidate. The "and
#: why" half of founder ruling V-6 — a count with no reason is a worry, not a
#: report. English here for the same reason every other `summary` in this module
#: is English: it is the server-built fallback, and the localized composition
#: belongs to the reader (`lib/conflict-display.ts`'s convention).
_NOT_APPLIED_REASON = {
    "no_op_carried_entry": "no change carried it",
    "op_rejected": "the change was malformed and dropped",
    "summary_populated": "your summary was already written, so it was left alone",
    # M-1c (founder ruling M-1, 2026-09-09): the candidate stated something and
    # the turn produced nothing at all. Named as the loss it is, not as the
    # mechanism that caused it — the candidate did not choose the model.
    #
    # Ruling M5.1.4 (2026-09-11, E-3) split the event by the reconciler's own
    # `empty_reason`: the first two say what happened instead of only that
    # nothing happened, and the third — the bare `no_write` — is BOTH the
    # `nothing_actionable` branch AND the fail-safe for a model that emitted no
    # reason, which is why it is the one that tells the candidate what to do
    # next. Clause-shaped like every other reason here: the reader composes them
    # into `health.notAppliedSummary`'s sentence (`HealthPanel.tsx`).
    "no_write": (
        "nothing was recorded from what you said — say it again in one "
        "sentence, or enter it in your profile"
    ),
    "no_write_already_known": "that is already in your profile, as stated",
    "no_write_question_only": (
        "your question arrived — nothing was changed for it"
    ),
    # Bug #723 (ADR-063 amended 2026-09-18) — the three outcomes of an answered
    # attribution question that do NOT write. Each says what the candidate's own
    # decision did, because each of them is a decision they made or one the
    # system still needs from them; a silent metadata-only receipt for exactly
    # these is the harm the Bug filed.
    "confirmation_discarded": "you chose to discard it",
    "confirmation_held": (
        "it is waiting on one more question — which of your roles at that "
        "employer it belongs to"
    ),
    "confirmation_unresolvable": (
        "the entry it named is no longer there, so it was not written"
    ),
    "confirmation_already_present": "that entry already said it",
    # 783:L7/L60 (ADR-063 amended 2026-10-20) — an op named an entry the
    # profile does not hold, so its content landed nowhere.
    "target_unresolved": (
        "it was aimed at an entry your profile does not hold, so it was not "
        "written — add it to the right entry"
    ),
}


#: The language slots, named the way ruling V-2 named them on the dispute
#: surface. A hub that says "en" while the dispute says "your self-description
#: (English)" is two names for one thing, which is the class of drift #685 was
#: filed against.
_SUMMARY_SLOT_LABEL = {
    "de": "your self-description (German)",
    "en": "your self-description (English)",
}


def _item_label(item: ImportNotApplied) -> str:
    """One not-applied item, named as the candidate would recognise it."""
    if item.section == "professional_summary":
        return _SUMMARY_SLOT_LABEL.get(item.label, item.label)
    return item.label


def _not_applied_issue(record: EnrichmentRecord) -> HealthIssue | None:
    """Founder ruling V-6 (2026-09-09) — the `not_applied` receipt gets a reader.

    `commit_ops` has copied `ApplyResult.not_applied` onto every
    `EnrichmentRecord` since #615 (2026-08-28) and **nothing read it**: the two
    import doors derive `merge_status` from `MergeResult.not_applied` instead,
    the testimony door's wire field is a different computation
    (`witness.compute_not_applied`'s figure/op channel), and the frontend
    carried no such field at all. So the product persisted, for a year of
    releases, a durable record of "this did not land" that no surface showed —
    the exact shape #684 was filed against ("silent loss becomes visible
    state"). ADR-061's 2026-09-08 amendment then routed the summary drop onto
    the same channel and would have inherited the same silence.

    One issue per record rather than one per item: the candidate submitted ONE
    thing, and three receipts about it are three worries about one event. The
    items' own labels and reasons ride the summary.

    Severity is always ``review``: nothing is destroyed and nothing is wrong —
    the vault simply does not carry something the submission did. ``critical``
    is for values the candidate rejected still standing (SF-PROFILE.9's band).
    """
    # UAT-3d — an adopted item is no longer counted or shown; every item
    # adopted = the issue is gone. `adoptable` is computed here, never stored.
    # D-2 — an item judged to be an existing entry is never adoptable (it would
    # be a duplicate); the asked items lead, and an issue whose items are ALL
    # already present is built from them as a silent (class S) record.
    open_items = [
        i.model_copy(update={
            "adoptable": bool(i.entry) and i.adopted_at is None and not i.already_present_as
        })
        for i in record.not_applied
        if i.adopted_at is None
    ]
    if not open_items:
        return None
    asked = [i for i in open_items if not i.already_present_as]
    open_items = asked or open_items
    reasons = sorted(
        {_NOT_APPLIED_REASON.get(i.reason, i.reason) for i in open_items}
    )
    labels = [_item_label(i) for i in open_items][:3]
    # V-7 — the pieces the reader composes from. `summary` below stays as the
    # English fallback for any consumer that has not been updated.
    raw_labels = [i.label for i in open_items][:3]
    raw_reasons = sorted({i.reason for i in open_items})
    more = len(open_items) - len(labels)
    named = ", ".join(labels) + (f" and {more} more" if more > 0 else "")
    count = len(open_items)
    # #705 (founder UAT, 2026-09-15) — the FULL receipt, every item, sorted by
    # section (then label) for a stable grouped render. `raw_labels`/
    # `raw_reasons` above stay capped at three — they keep the V-7 hub
    # sentence working unchanged; this is the overlay's full list.
    items_detail = sorted(open_items, key=lambda i: (i.section or "", i.label))
    return HealthIssue(
        id=f"not_applied:{record.id}",
        thread="not_applied",
        profile_mismatch_severity="review",
        summary=(
            f"{count} {'item' if count == 1 else 'items'} from your "
            f"{record.source} did not reach your profile ({named}) — "
            f"{'; '.join(reasons)}"
        ),
        field_ref=", ".join(
            sorted({i.section for i in open_items if i.section})
        )
        or None,
        source_record_ref=record.id,
        not_applied_count=count,
        not_applied_source=record.source,
        not_applied_reasons=raw_reasons,
        not_applied_labels=raw_labels,
        not_applied_items=items_detail,
    )


#: D-1 (Hazelnut build 5 delivery run) — the sources of a document IMPORT. The
#: not_applied question reads the newest record that carries not-applied items
#: or is such an import, never the bare trail head: every other act (a
#: conflict answer, a unit, a merge, an adoption, an aspiration answer) writes
#: a record of its own, and a head rule with an allow-list of "transparent"
#: acts hid the question twice before this.
IMPORT_SOURCES = frozenset({"cv_upload", "cv_paste", "linkedin_import", "xing_import"})


def _duplicate_entry_issues(profile: MasterProfileData) -> list[HealthIssue]:
    """UAT-3d — one ``review`` issue per recognised-as-possibly-the-same pair of
    positions (``entry_identity.open_pairs``: identical stated dates, not kept
    separate, not judged different by the model). Answered by the pair
    resolver (``confirm_match``)."""
    issues: list[HealthIssue] = []
    for section, id_a, id_b in open_pairs(profile):
        by_id = {
            getattr(e, "id", None): e for e in (getattr(profile, section, None) or [])
        }
        a, b = by_id.get(id_a), by_id.get(id_b)
        if a is None or b is None:
            continue
        label_a, label_b = _entity_label(a), _entity_label(b)
        issues.append(
            HealthIssue(
                id=f"duplicate_entry:{section}:{id_a}:{id_b}",
                thread="duplicate_entry",
                profile_mismatch_severity="review",
                summary=(
                    f"{label_a or id_a} and {label_b or id_b} state the same dates "
                    f"— are they the same entry?"
                ),
                field_ref=f"{section}",
                section=section,
                entry_ids=[id_a, id_b],
                entity_label=label_a,
                other_entity_label=label_b,
            )
        )
    return issues


def _aspiration_issues(profile: MasterProfileData) -> list[HealthIssue]:
    """RULING B-1 — one ``review`` issue per current-role or current-project
    bullet (``section`` names which) the model judged
    as worded like a goal or a task still to do, unanswered
    (``aspiration.open_aspirations``). Answered by ``ask_achieved``."""
    from applire.services.profile.aspiration import issue_id, open_aspirations

    return [
        HealthIssue(
            id=issue_id(entry.id, key),
            thread="aspiration",
            profile_mismatch_severity="review",
            summary=f"Is this reached? '{text}' ({_entity_label(entry) or entry.id})",
            field_ref=section,
            section=section,
            entry_id=entry.id,
            entity_label=_entity_label(entry),
            aspiration_text=text,
        )
        for section, entry, key, text in open_aspirations(profile)
    ]


def _registry_kind(issue: HealthIssue) -> str:
    """The ``health:<kind>`` registry key of one issue (ADR-099)."""
    thread = issue.thread
    if thread == "conflict":
        # `conflict_shape` is the one decider; "merge" is only in the list shape.
        return "health:conflict_list" if "merge" in (issue.outcomes or []) else "health:conflict_scalar"
    if thread == "not_applied":
        if all(i.already_present_as for i in (issue.not_applied_items or [])):
            return "health:not_applied_already_present"  # D-2
        adoptable = any(i.adoptable for i in (issue.not_applied_items or []))
        return "health:not_applied" if adoptable else "health:not_applied_unadoptable"
    return f"health:{thread}"


def _classified(issue: HealthIssue) -> HealthIssue:
    """The ONE place a health issue gets its delivery class and resolver."""
    entry = _classify_kind(_registry_kind(issue))
    return issue.model_copy(update={"delivery_class": entry.cls, "resolver": entry.resolver})


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
    issues.extend(_span_provenance_issues(profile))
    issues.extend(_suspect_binding_issues(profile))  # FS-5
    issues.extend(_duplicate_entry_issues(profile))  # UAT-3d
    issues.extend(_aspiration_issues(profile))  # RULING B-1

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
        # Founder ruling V-6 — the TRAIL HEAD only, as ruled. An item that did
        # not land two merges ago may well have been supplied by a later one,
        # and a hub that reported it forever would nag about something already
        # fixed (the mirror of SF-PROFILE.2's "conflicts age forever"). The
        # residual is stated rather than hidden: a loss older than the newest
        # record is NOT surfaced here, and the durable receipt — plus the
        # WARNING each drop logs — remains the record of it.
        # D-1 — the head is the newest record that CARRIES not-applied items
        # (an import, a statement intake, a confirmation resolution) or is a
        # document import: a later import that applied everything still
        # clears the question (V-6), while an act that carries no receipt (a
        # conflict answer, a unit, a merge, an adoption) never hides it.
        head_record = next(
            (
                r for r in reversed(metadata.enrichment_history)
                if r.not_applied or r.source in IMPORT_SOURCES
            ),
            None,
        )
        if head_record is not None:
            head = _not_applied_issue(head_record)
            if head is not None:
                issues.append(head)

    profile_dict = profile.model_dump()
    if na_fields:
        profile_dict["_meta"] = {"na_fields": na_fields}

    field_gaps = completeness_field_gaps(profile_dict)
    completeness_entry = _classify_kind(
        "health:completeness" if field_gaps else "health:completeness_no_field_gaps"
    )
    return ProfileHealthResponse(
        issues=[_classified(i) for i in issues],
        completeness=CompletenessBlock(
            score=profile.calculate_completeness(),
            gaps=profile.completeness_gaps(),
            field_gaps=field_gaps,
            delivery_class=completeness_entry.cls,
            resolver=completeness_entry.resolver,
        ),
    )
