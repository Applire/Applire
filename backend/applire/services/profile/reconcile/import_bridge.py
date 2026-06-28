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
from applire.schemas.profile import Conflict, MasterProfileData
from applire.services.profile.merge import MergeResult
from applire.services.profile.reconcile.apply import ApplyResult, apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import RequestConfirmation
from applire.services.profile.reconciliation import compute_merge_reconciliation

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
    ("skills", "certifications", "languages", "education", "publications"),
    ("personal_info", "professional_summary"),
)


def _to_conflict(rc: RequestConfirmation, source: str) -> Conflict:
    """Map an engine ambiguity (a RequestConfirmation) onto the import path's
    Conflict shape so it surfaces on the existing conflict-resolution UI.

    The confirmation's question becomes the (truncated) ``field`` label and its
    options become the ``incoming_value`` to choose from; ``existing_value`` is
    ``None`` because an ambiguity has no single prior value to contrast against."""
    return Conflict(
        section="",
        field=(rc.question[:64] if rc.question else "ambiguity"),
        existing_value=None,
        incoming_value=rc.options,
        source=source,
        suggested_resolution=rc.question or None,
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
) -> tuple[ApplyResult, list[RequestConfirmation]]:
    """Segmented fallback for a reconcile that truncated as one big call (ADR-047).

    Reconciles ``incoming`` one dependency-ordered slice at a time — ONE
    ``aparse_json`` per slice — applying each before the next so the evolving
    profile (with the just-created experiences' ids) conditions later slices.
    These are N independent single-shot calls, not a multi-turn tool loop
    (ADR-046 §3's rejection stands). Returns an accumulated ``ApplyResult`` plus
    the folded ambiguities, matching the single-call path's tail.

    A slice that *itself* truncates re-raises ``LLMTruncatedError`` — segmentation
    is the floor, not an infinite recursion; the upload then fails that file
    cleanly rather than persisting a half-merge (honest failure, ADR-047 §4)."""
    current = existing
    changes: list = []
    conflicts: list[Conflict] = []
    ambiguities: list[RequestConfirmation] = []
    for slice_info in _slice_incoming(incoming):
        result = await reconcile(current, slice_info, source, provider, lang)
        applied = apply_ops(current, result.ops, source)
        current = applied.profile
        changes.extend(applied.changes)
        conflicts.extend(applied.conflicts)
        ambiguities.extend(result.ambiguities)
        ambiguities.extend(applied.pending_confirmations)
    accumulated = ApplyResult(profile=current, changes=changes, conflicts=conflicts)
    return accumulated, ambiguities


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
    except LLMTruncatedError:
        logger.warning(
            "reconcile: single-call merge hit the output cap; switching to segmented "
            "batched fallback instead of failing the import (ADR-047)"
        )
        applied, ambiguities = await _reconcile_import_batched(
            existing, incoming, source, provider, lang
        )
    conflicts = list(applied.conflicts) + [_to_conflict(a, source) for a in ambiguities]
    added = [
        (c.new_value if isinstance(c.new_value, str) else f"{c.section}.{c.field}")
        for c in applied.changes
    ]
    return MergeResult(
        merged_profile=applied.profile,
        added=added,
        conflicts=conflicts,
        changes=applied.changes,
        reconciliation=compute_merge_reconciliation(incoming, applied.profile),
    )
