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

"""US184 — import-path bridge over the ADR-046 reconciler.

CV / LinkedIn / PDF / cv-paste import reconciles a WHOLE incoming MasterProfileData
into the existing profile via one reconcile() + apply_ops(), returning the existing
MergeResult shape so the upload/import call sites, the ADR-042 snapshot, and the
response contract are unchanged. Drop-in for the retired lexical merge_profiles."""
from __future__ import annotations

import logging

from pydantic import BaseModel

from applire.exceptions import LLMTruncatedError
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import (
    Certification,
    Conflict,
    FieldChange,
    MasterProfileData,
    PendingConfirmation,
    _coerce_partial_date,
)
from applire.services.profile.merge import MergeResult
from applire.services.profile.reconcile.apply import (
    ApplyResult,
    _added,
    _fill_empties,
    _merged,
    apply_ops,
)
from applire.services.profile.reconcile.dedupe import classify_certification_dupe
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.services.profile.reconcile.ops import CommitOp, RequestConfirmation
from applire.services.profile.reconciliation import compute_merge_reconciliation
from applire.services.skill_enrichment import enrich_skills_deterministic

logger = logging.getLogger(__name__)

# US190 (ADR-047 §1) — dependency-ordered section groups for the segmented
# fallback. Order is load-bearing: experiences come FIRST so that, once applied,
# they carry real ids and a later slice's skills can reference them by id rather
# than via a cross-batch local ref the independent skill call cannot know (Open
# Q#3). Identity scalars carry no refs and ride last. Together the groups cover
# every reconcilable content section exactly once (no incoming content is
# dropped); `metadata` is profile bookkeeping, not mergeable CV input, so it is
# deliberately excluded — the single-call path never reconciles it either.
_BATCH_SECTION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("work_experience", "projects", "volunteer_activities"),
    # signature_stories rides the second group: stories reference experiences
    # (evidence ids), so experiences must already exist — same reason skills
    # come after the engagement group (ADR-055; latent-drop close E046).
    ("skills", "certifications", "languages", "education", "publications",
     "signature_stories"),
    ("personal_info", "professional_summary"),
)


def _to_pending_confirmation(rc: RequestConfirmation, source: str) -> PendingConfirmation:
    """Carry an engine ambiguity (a ``RequestConfirmation``) onto the import path's
    confirmation channel — question + each option intact (E037 PQ #4).

    The retired ``_to_conflict`` force-fit this into the 2-value ``Conflict`` shape
    (section='', the whole question truncated into ``field``, the option *list*
    comma-joined into ``incoming_value``), which rendered as a garbled sentence. A
    ``Conflict`` structurally cannot represent an N-option ask, so the ambiguity
    keeps its own shape and surfaces as a question + per-option buttons in the
    profile-review interview — exactly like the non-import (interview-turn) path."""
    return PendingConfirmation(
        question=rc.question or "",
        options=list(rc.options),
        context=dict(rc.context),
        source=source,
    )


def _slice_incoming(incoming: MasterProfileData) -> list[MasterProfileData]:
    """Split ``incoming`` into dependency-ordered partial profiles (US190).

    Each slice carries ONLY its group's sections (the rest left at their schema
    defaults), so each reconcile call's NEW INFORMATION — and thus its op batch —
    is small enough to fit under an output cap. Groups whose every section is
    empty are skipped (no point spending a call on nothing). The slices together
    cover every reconcilable content section of ``incoming`` exactly once, so no
    incoming content is lost across the batches."""
    slices: list[MasterProfileData] = []
    for group in _BATCH_SECTION_GROUPS:
        if all(_section_is_empty(getattr(incoming, name)) for name in group):
            continue
        fields = {name: getattr(incoming, name) for name in group}
        slices.append(MasterProfileData(**fields))
    return slices


def _section_is_empty(value: object) -> bool:
    """Is this incoming section devoid of real data? A list section is empty when
    it has no entries; a scalar model section (personal_info / professional_summary)
    is empty when it equals a freshly default-constructed instance — Pydantic model
    instances are always truthy, so a plain bool() check would never skip them."""
    if isinstance(value, list):
        return not value
    if isinstance(value, BaseModel):
        return value == value.__class__()
    return value in (None, "")


async def _reconcile_import_batched(
    existing: MasterProfileData,
    incoming: MasterProfileData,
    source: str,
    provider: LLMProvider,
    lang: str,
) -> tuple[ApplyResult, list[RequestConfirmation], list[CommitOp], list[str]]:
    """Segmented fallback for a reconcile that truncated as one big call (ADR-047).

    Reconciles ``incoming`` one dependency-ordered slice at a time — ONE
    ``aparse_json`` per slice — applying each before the next so the evolving
    profile (with the just-created experiences' ids) conditions later slices.
    These are N independent single-shot calls, not a multi-turn tool loop
    (ADR-046 §3's rejection stands). Returns an accumulated ``ApplyResult``,
    the folded ambiguities (matching the single-call path's tail), and —
    #615 (ADR-063 amended 2026-08-28) — every slice's own emitted ops and
    parse-rejected raw ops, ACCUMULATED across the whole batch: "the ops of
    all slices are the emitted ops" is what the carried-predicate
    (``import_witness.compute_import_not_applied``) needs to see the same
    evidence on the segmented path that it sees on the fast path.

    A slice that *itself* truncates re-raises ``LLMTruncatedError`` — segmentation
    is the floor, not an infinite recursion; the upload then fails that file
    cleanly rather than persisting a half-merge (honest failure, ADR-047 §4)."""
    current = existing
    changes: list = []
    conflicts: list[Conflict] = []
    demotions: list = []
    ambiguities: list[RequestConfirmation] = []
    all_ops: list[CommitOp] = []
    all_rejected_ops: list[str] = []
    for slice_info in _slice_incoming(incoming):
        result = await reconcile(current, slice_info, source, provider, lang)
        applied = apply_ops(current, result.ops, source)
        current = applied.profile
        changes.extend(applied.changes)
        conflicts.extend(applied.conflicts)
        # #485 — an import slice that carries a retraction demotes like any
        # other door; the receipt accumulates on its own list (see
        # ApplyResult.demotions) so the import's `added` summary keeps meaning
        # "what this document ADDED".
        demotions.extend(applied.demotions)
        ambiguities.extend(result.ambiguities)
        ambiguities.extend(applied.pending_confirmations)
        all_ops.extend(result.ops)
        all_rejected_ops.extend(result.rejected_ops)
    accumulated = ApplyResult(
        profile=current, changes=changes, conflicts=conflicts, demotions=demotions
    )
    return accumulated, ambiguities, all_ops, all_rejected_ops


def _union_certifications(
    merged: MasterProfileData,
    incoming: MasterProfileData,
    changes: list[FieldChange],
) -> None:
    """#190 (dupe instrument fixed for #618) — deterministically UNION
    ``incoming.certifications`` into ``merged``.

    Certifications are FACTUAL data (like contact info): the binding F7 decision
    (``tests/unit/test_cv_certifications.py``) copies them verbatim through
    deterministic code, never a routed-through-an-LLM JSON schema. The extractor /
    reconciler LLM tends to misroute cert names that also look like frameworks/
    standards (ITIL, CPSA/iSAQB, "Expert for Computersystemvalidation") into
    ``skills``, silently dropping them from ``certifications``. This pass is the
    durable guarantee against that loss: whatever op batch the LLM emitted, every
    incoming certification is present on the merged profile afterwards.

    #618: this used to call the section-agnostic ``classify_dupe`` on name alone
    — the same generic instrument used for education/languages/etc, which knows
    nothing about certification-specific variance (EN/DE cross-language pairs, the
    ®/™ symbol fold, or an issuer as a tie-breaker). A two-source import (FlowCV +
    LinkedIn) produced verbatim duplicates the generic instrument could not see:
    "Expert for Computersystemvalidation" / "Experte für Computervalidierung",
    "ITIL Foundation Level" / "ITIL® Foundation", and a Software-Architect
    cognate pair — all MATCH under the certification-aware instrument below,
    0/3 under the old one. This was an ADR-066 ("one logical operation, one
    implementation") seam: three readers of certification identity
    (``_apply_upsert_certification`` in apply.py, ``import_witness``'s
    ``compute_import_not_applied``, and this function) had settled on two
    different instruments. This is the third reader switched onto
    ``classify_certification_dupe`` — the SAME instrument the other two already
    use, anchored on ``credential_id`` first, then name (certification-folded)
    + issuing_organization.

    The append-on-non-MATCH trade is UNCHANGED: anything the guard does not
    confidently call the same cert — DISTINCT, or AMBIGUOUS (folded-name SAME
    but a *confirmed different* issuing_organization, or a bare single-token
    containment) — is still appended rather than dropped. Preserving a possible
    near-duplicate is the correct trade against silent data loss for a factual,
    user-verifiable field; this function has no confirmation channel available
    to it (unlike ``_apply_upsert_certification``, which can raise a
    ``RequestConfirmation`` instead), so AMBIGUOUS can only mean "keep both,
    don't guess" here. Swapping the instrument shrinks the AMBIGUOUS/appended
    set — it no longer includes the 3 pairs above — without loosening that
    guarantee: a genuine cross-source org conflict on an otherwise-matching
    name still surfaces as two entries, exactly as before. ``merged`` is the
    apply_ops deep copy, so mutating it here is safe; incoming certs are
    deep-copied in so the two profiles never share objects.
    """
    for cert in incoming.certifications:
        verdict = classify_certification_dupe(
            name=cert.name,
            issuing_organization=cert.issuing_organization,
            credential_id=cert.credential_id,
            existing=merged.certifications,
            name_getter=lambda c: c.name,
            org_getter=lambda c: c.issuing_organization,
            credential_id_getter=lambda c: c.credential_id,
        )
        if verdict.match is not None:
            changed = _fill_empties(verdict.match, {
                "issuing_organization": cert.issuing_organization,
                "date_obtained": _coerce_partial_date(cert.date_obtained),
                "expiry_date": _coerce_partial_date(cert.expiry_date),
                "credential_id": cert.credential_id,
                "credential_url": cert.credential_url,
            })
            if changed:
                changes.append(_merged("certifications", "name", None, verdict.match.name))
            continue
        merged.certifications.append(cert.model_copy(deep=True))
        changes.append(_added("certifications", "name", cert.name))


def _carry_skill_enrichment(
    merged: MasterProfileData, incoming: MasterProfileData
) -> None:
    """#327 — carry a pre-merge ESTIMATE across the op round-trip.

    ``enrich_skills_deterministic`` re-establishes every ``computed`` duration
    from the merged profile's own dated evidence, which is the better number and
    always wins. What it cannot recover is a phase-2 estimate: that came from an
    LLM call made on ``incoming`` before reconciliation, and re-running the
    estimator here would cost a second call on every merging import.

    So for a merged skill the deterministic pass could not evidence, and only
    when it still has no duration at all, the same-named incoming skill's
    ``years_experience``/``source`` are copied over. Without this a second
    import silently BLANKS durations the user already saw after the first one.
    Name match is exact (post-``_norm``) — ``skills_near_dupe`` is the
    reconciler's merge instrument, and guessing here could attach one skill's
    tenure to another.
    """
    from applire.services.ats_audit import _norm

    by_name = {
        _norm(s.name): s
        for s in incoming.skills
        if s.years_experience is not None
    }
    if not by_name:
        return
    for skill in merged.skills:
        if skill.years_experience is not None or skill.source == "computed":
            continue
        source_skill = by_name.get(_norm(skill.name))
        if source_skill is None:
            continue
        skill.years_experience = source_skill.years_experience
        skill.source = source_skill.source or skill.source


async def reconcile_import(
    existing: MasterProfileData,
    incoming: MasterProfileData,
    source: str,
    provider: LLMProvider,
    lang: str = "en",
) -> MergeResult:
    """Reconcile a WHOLE incoming profile into ``existing`` via the ADR-046 engine.

    Drop-in for the lexical ``merge_profiles``: one ``reconcile`` call + one
    deterministic ``apply_ops``, returning the existing ``MergeResult`` shape.

    Degrades to empty ops on generic LLM noise (``apply_ops`` is pure and
    ``compute_merge_reconciliation`` is deterministic), but DELIBERATELY lets a
    ``LLMTruncatedError`` propagate: a truncated reconcile means the op batch was
    cut off, so silently applying the partial set would drop a whole CV's content.
    The upload/import caller catches it and fails that file cleanly rather than
    persisting a half-merge (see routers/profile.py).

    US190 (ADR-047 §1): a truncated *single* call no longer fails the merge — it
    switches to the segmented fallback (``_reconcile_import_batched``) instead of
    blindly doubling the budget into a timeout (the same 'switch to segmented'
    recovery US188/US189 use for CV tailoring). The fast path stays the default;
    segmentation engages only when the one big call does not fit."""
    try:
        result = await reconcile(existing, incoming, source, provider, lang)
        applied = apply_ops(existing, result.ops, source)
        ambiguities = list(result.ambiguities) + list(applied.pending_confirmations)
        emitted_ops: list = list(result.ops)
        rejected_ops: list[str] = list(result.rejected_ops)
    except LLMTruncatedError:
        logger.warning(
            "reconcile: single-call merge hit the output cap; switching to segmented "
            "batched fallback instead of failing the import (ADR-047)"
        )
        applied, ambiguities, emitted_ops, rejected_ops = await _reconcile_import_batched(
            existing, incoming, source, provider, lang
        )
    # #190 — deterministic certification passthrough (defense in depth). Runs on
    # BOTH the fast path and the segmented fallback (both funnel through `applied`),
    # AFTER apply_ops, so no incoming cert is lost to an LLM misroute into skills.
    _union_certifications(applied.profile, incoming, applied.changes)
    # #327 — deterministic skill-provenance recovery, at the same seam and for
    # the same reason as the certification passthrough above. ``enrich_skills``
    # runs on ``incoming`` BEFORE this call, but the merged profile is rebuilt
    # from the ADR-046 op vocabulary and ``UpsertSkill`` carries no
    # ``years_experience`` and no ``source`` — so every skill the reconciler
    # minted reached the vault with a null provenance (33 of 67 skills on a
    # three-document import). Adding those fields to the op is the wrong fix:
    # the reconciler LLM would then be emitting computed provenance, which
    # ADR-062 reserves for code.
    #
    # Both passes below are pure — this seam costs NO extra LLM call.
    applied.profile = enrich_skills_deterministic(applied.profile)
    _carry_skill_enrichment(applied.profile, incoming)
    # E037 PQ #4 — ambiguities ride the confirmation channel (question + options
    # intact); they are NO LONGER coerced into the 2-value Conflict shape, which
    # garbled the dialog. Real two-value disputes still come through `conflicts`.
    conflicts = list(applied.conflicts)
    pending_confirmations = [_to_pending_confirmation(a, source) for a in ambiguities]
    added = [
        (c.new_value if isinstance(c.new_value, str) else f"{c.section}.{c.field}")
        for c in applied.changes
    ]
    # #615 (ADR-063 amended 2026-08-28, second entry) — ONE computation feeds
    # both the door-level fact and the count-reconciliation delta (ADR-041
    # amended the same day): `emitted_ops` is the fast path's `result.ops` OR
    # the segmented path's ops accumulated across every slice, so the
    # carried-predicate sees the identical evidence either way.
    not_applied = compute_import_not_applied(
        incoming, applied.profile, emitted_ops, rejected_ops=rejected_ops
    )
    return MergeResult(
        merged_profile=applied.profile,
        added=added,
        conflicts=conflicts,
        # #485 — demotion receipts ride with the merge's change trail (ADR-059
        # clause 1) but stay out of `added` above, which is the "what did this
        # document contribute" summary.
        changes=applied.changes + applied.demotions,
        reconciliation=compute_merge_reconciliation(incoming, applied.profile, not_applied),
        pending_confirmations=pending_confirmations,
        not_applied=not_applied,
    )
