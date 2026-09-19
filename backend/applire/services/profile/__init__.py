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

import hashlib
import json
import logging
import uuid

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import CV_EXTRACTION_MAX_TOKENS, LLM_REVIEW_MAX_RETRIES
from applire.models.profile import MasterProfile
from applire.models.uploads import UploadRecord
from applire.prompts.cv_extraction import (
    CV_EXTRACTION_REFINEMENT_PROMPT,
    GENERIC_CV_EXTRACTION_PROMPT,
    build_generic_prompt,
)
from applire.prompts.profile_extraction import (
    SYSTEM_PROMPT,
    PROFILE_EXTRACTION_REFINEMENT_PROMPT,
    build_retry_prompt as _build_extraction_retry_prompt,
    build_user_prompt,
)
from applire.prompts.review_profile_extraction import (
    REVIEW_SYSTEM_PROMPT as _EXTRACTION_REVIEW_SYSTEM_PROMPT,
    build_review_prompt as _build_extraction_review_prompt,
)
from applire.prompts.review_cv_extraction import (
    CV_EXTRACTION_REVIEW_SYSTEM_PROMPT as _CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
    build_cv_extraction_review_prompt as _build_cv_extraction_review_prompt,
    build_cv_extraction_retry_prompt as _build_cv_extraction_retry_prompt,
)
from applire.providers.embedding.base import EmbeddingProvider
from applire.providers.embedding.noop import NoopEmbeddingProvider
from applire.providers.llm.base import LLMProvider
from applire.services.linkedin import parse_linkedin_pdf, parse_linkedin_zip
from applire.services.profile.commit import (
    CommitProvenance,
    EnrichPolicy,
    SnapshotClass,
    commit_ops,
)
from applire.services.profile.extract_segmented import extract_with_fallback
from applire.services.profile.field_edit import build_replace_section_op
from applire.services.profile.reconcile.import_bridge import reconcile_import
from applire.services.profile.reconcile.ops import ApplyImportMerge
from applire.services.profile.resolution import (
    build_resolve_confirmation_op,
    build_resolve_field_op,
)
from applire.services.reviewer import review_and_refine
from applire.services.skill_enrichment import enrich_skills, enrich_skills_deterministic
from applire.schemas.profile import (
    CompletenessBlock,
    Conflict,
    ConflictSummary,
    CVUploadResponse,
    EnrichmentRecord,
    FieldChange,
    ImportMergeStatus,
    ImportNotApplied,
    MasterProfileData,
    MasterProfileResponse,
    MatchReceipt,
    PendingConfirmation,
    ProfileChangesResponse,
    ProfileHealthResponse,
    ProfileImportResponse,
    ProfileMetadata,
    StagedResolveResponse,
    OBJECT_SECTIONS,
    VAULT_SECTIONS,
)
from applire.services.profile.expectations import annotate_expected_fields
from applire.services.profile.health import assess_health

_DEFAULT_EMBEDDING_PROVIDER = NoopEmbeddingProvider()


def _profile_to_embedding_text(profile_json: dict) -> str:
    """Produce a compact text representation of a profile for embedding."""
    parts: list[str] = []

    personal = profile_json.get("personal_info") or {}
    if personal.get("name"):
        parts.append(personal["name"])

    summary = profile_json.get("professional_summary")
    if summary:
        parts.append(str(summary))

    for exp in (profile_json.get("work_experience") or []):
        role = exp.get("role") or ""
        company = exp.get("company") or ""
        if role or company:
            parts.append(f"{role} at {company}".strip())
        desc = exp.get("description") or ""
        if desc:
            parts.append(desc)

    skills = [s.get("name") or "" for s in (profile_json.get("skills") or [])]
    if skills:
        parts.append("Skills: " + ", ".join(s for s in skills if s))

    return "\n".join(parts)


async def _compute_embedding(
    profile_json: dict,
    embedding_provider: EmbeddingProvider,
) -> list[float] | None:
    """Compute an embedding for a profile; returns None for noop/zero vectors."""
    text = _profile_to_embedding_text(profile_json)
    if not text.strip():
        return None
    try:
        vector = await embedding_provider.embed(text)
    except Exception:
        logger.warning("Profile embedding generation failed; storing NULL.", exc_info=True)
        return None
    # Don't persist zero-vectors (noop provider) — NULL signals "not computed".
    if all(v == 0.0 for v in vector):
        return None
    return vector


# The manually editable section vocabulary. It moved to `schemas/profile.py`
# with #480 PR 3 so the `ReplaceSection` op can validate against the SAME set
# without importing this package (an import cycle) — the guard belongs to the
# op, not to one door. These names stay as the module's public handles: the MCP
# tool builds its description from `_VALID_SECTIONS`, and the set is unchanged.
_VALID_SECTIONS = VAULT_SECTIONS
_OBJECT_SECTIONS = OBJECT_SECTIONS


def extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def _linkedin_to_text(linkedin_json: dict) -> str:
    return json.dumps(linkedin_json, ensure_ascii=False, indent=2)


def _to_response(record: MasterProfile) -> MasterProfileResponse:
    profile_data = MasterProfileData.model_validate(record.profile_json)
    conflicts = (
        profile_data.metadata.pending_conflicts
        if profile_data.metadata
        else []
    )
    return MasterProfileResponse(
        id=record.id,
        profile=profile_data,
        completeness=profile_data.calculate_completeness(),
        stats=profile_data.calculate_stats(),
        merge_conflicts=conflicts,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_import_response(
    record: MasterProfile,
    *,
    merge_status: ImportMergeStatus = "applied",
    not_applied: list[ImportNotApplied] | None = None,
    matched: list[MatchReceipt] | None = None,
) -> ProfileImportResponse:
    """Same construction as :func:`_to_response`, plus the import fact (#615,
    ADR-063 amended 2026-08-28). A SEPARATE builder, not a parameter on
    ``_to_response``: that function also serves ``GET /api/profile`` (a plain
    read, no merge in scope) and ``PATCH /{section}`` (a manual field edit,
    not a merge) — refuter B, MAJOR 1. Neither of those doors can honestly
    say ``merge_status``, so only the import call sites in
    ``_import_from_text`` build this. Reuses ``_to_response``'s already-
    validated fields (never re-dumps/re-parses the profile)."""
    base = _to_response(record)
    return ProfileImportResponse(
        id=base.id,
        profile=base.profile,
        completeness=base.completeness,
        stats=base.stats,
        merge_conflicts=base.merge_conflicts,
        created_at=base.created_at,
        updated_at=base.updated_at,
        merge_status=merge_status,
        not_applied=list(not_applied or []),
        # #674 line 72 — the merge's `match_existing` receipts reach the import
        # doors' response, the same way `not_applied` does.
        matched=list(matched or []),
        # #367 — the same number under the name the import callers read.
        completeness_score=base.completeness,
    )


async def _get_latest(db: AsyncSession) -> MasterProfile | None:
    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _make_enrichment_record(
    source: str,
    section: str = "*",
    action: str = "added",
    old_value: object = None,
    new_value: object = None,
    session_id: str | None = None,
    confidence: float | None = None,
) -> EnrichmentRecord:
    return EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source=source,
        source_session_id=session_id,
        changes=[
            FieldChange(
                section=section,
                field=section,
                action=action,
                old_value=old_value,
                new_value=new_value,
            )
        ],
        confidence=confidence,
    )


def _enrichment_from_merge(merge_result, source, session_id: str | None = None) -> EnrichmentRecord:
    """Build an EnrichmentRecord carrying the merge's *structured* per-decision changes
    (US145 / ADR-040) so the "what changed & why" surfaces render from data. Falls back
    to a single summary FieldChange only if the merge produced no structured changes."""
    changes = list(merge_result.changes) if merge_result.changes else [
        FieldChange(
            section="*", field="*", action="merged",
            new_value={"added": merge_result.added, "conflicts": len(merge_result.conflicts)},
            rationale="Profile updated from this source.",
            rationale_key="profile_updated",
        )
    ]
    return EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source=source,
        source_session_id=session_id,
        changes=changes,
        reconciliation=merge_result.reconciliation or None,
        # #615 (ADR-063 amended 2026-08-28, second entry) — the merge's own
        # carried-predicate facts, persisted beside `reconciliation`.
        not_applied=list(merge_result.not_applied),
        matched=list(getattr(merge_result, "matched", None) or []),  # #707
    )


def _parked_identity(value: object) -> str:
    """A stable, hashable identity string for an arbitrary parked-item value.

    Conflict values are `Any` (str, dict, list), so they cannot go straight into
    a set. JSON with sorted keys is order-insensitive for dicts and cheap."""
    return json.dumps(value, sort_keys=True, default=str)


def _confirmation_key(confirmation: PendingConfirmation) -> tuple:
    return (confirmation.question.strip().casefold(), tuple(confirmation.options))


def _conflict_key(conflict: Conflict) -> tuple:
    return (
        conflict.section,
        conflict.field,
        _parked_identity(conflict.existing_value),
        _parked_identity(conflict.incoming_value),
    )


def _surviving_parked_items(
    existing_data: MasterProfileData,
    round_conflicts: list[Conflict],
    round_confirmations: list[PendingConfirmation],
) -> tuple[list[Conflict], list[PendingConfirmation]]:
    """The pending metadata lists an import round may write, preserving every
    still-open item parked by an earlier round or another write path.

    E045 (adversarial M2) established that both import doors replace
    `metadata.pending_conflicts` / `pending_confirmations` wholesale each round.
    The guard it added preserved exactly one source (`agent_interview`), which
    left every other parked item — `testimony` (`submit_testimony`), and any
    unanswered ambiguity from a *previous* import — destroyed by the next CV
    upload (#333). A parked item is the human's open question; discarding it is
    indistinguishable from silently dropping the claim it stands for.

    So: every unresolved existing item survives, whatever its source, and the
    round's own items are appended only where they are not the same open
    question again (`_confirmation_key` / `_conflict_key`) — otherwise a repeat
    upload would accumulate one duplicate per round. The preserved item wins the
    tie so its `confirmation_id`/`conflict_id` stays live for an in-flight
    resolve. Resolved items are not carried forward; both resolve paths delete
    rather than flag, so this only ever drops history that is already answered.
    """
    meta = existing_data.metadata
    if meta is None:
        return list(round_conflicts), list(round_confirmations)

    kept_conflicts = [c for c in meta.pending_conflicts if not c.resolved]
    kept_confirmations = [c for c in meta.pending_confirmations if not c.resolved]
    seen_conflicts = {_conflict_key(c) for c in kept_conflicts}
    seen_confirmations = {_confirmation_key(c) for c in kept_confirmations}

    return (
        kept_conflicts
        + [c for c in round_conflicts if _conflict_key(c) not in seen_conflicts],
        kept_confirmations
        + [
            c
            for c in round_confirmations
            if _confirmation_key(c) not in seen_confirmations
        ],
    )


def _apply_import_metadata(
    merged: MasterProfileData,
    existing_data: MasterProfileData,
    merge_result,
    *,
    created_via: str,
    created_at: datetime,
) -> None:
    """The metadata an IMPORT owns, on the profile it is about to commit.

    Deliberately short, and it stays short: everything a merge used to set here
    that every OTHER write path also needs — the completeness recompute, both
    clocks, the enrichment trail — belongs to ``commit_ops`` since #480 PR 2 and
    is no longer duplicated per writer. What is left is genuinely import-only:

    * the creation stamps, when the existing profile carried no metadata at all;
    * the two parked lists, which the import REPLACES wholesale (#333 / E037
      PQ #4) with this round's items plus every still-open item parked earlier —
      the committer's own `extend` cannot express "and drop the ones that are
      now resolved", so the import computes the list and the committer appends
      nothing to it (the merge op raises no conflicts or confirmations of its
      own; they are already on `merged.metadata`).
    """
    parked_conflicts, parked_confirmations = _surviving_parked_items(
        existing_data, merge_result.conflicts, merge_result.pending_confirmations
    )
    if merged.metadata is None:
        merged.metadata = ProfileMetadata(
            created_via=created_via,
            created_at=created_at,
        )
    merged.metadata.pending_conflicts = parked_conflicts
    merged.metadata.pending_confirmations = parked_confirmations


# The #218 conflict write-back surgery (`_entry_for_conflict`,
# `_rewrite_bullet_list`, `_apply_resolution_to_list_section`) moved into
# `reconcile/apply.py` with #480 PR 5: it belongs to the `ResolveField` applier
# now, and living beside the ops it serves is what finally made it testable
# without a database and a service call.


# ── The text-ingest doors (#367) ──────────────────────────────────────────────
#
# Five public wrappers, one ingest. Each does exactly what a door may do (ADR-066
# cl. 1): turn its transport into text + the source bytes to keep, and hand both to
# `ingest_cv`. None of them evaluates the gate, chooses a merge, or decides whether
# an upload record is written — before #367 they did none of those things either,
# which was the defect.
#
# `storage` is a REQUIRED keyword argument on every one of them. It used to be
# absent, and that absence is precisely SF-DOOR.3: a door that can silently decline
# to persist the source document is a door that decides what the audit trail says.
# A caller that forgets it now fails loudly at the call site instead.


async def import_from_pdf(
    file_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    filename: str = "import.pdf",
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    raw_text = extract_pdf_text(file_bytes)
    if not raw_text:
        raise ValueError("Could not extract text from PDF")
    return await _import_from_text(
        raw_text,
        db,
        provider,
        created_via="cv_upload",
        embedding_provider=embedding_provider,
        storage=storage,
        source_bytes=file_bytes,
        filename=filename,
        content_type="application/pdf",
        user_id=user_id,
    )


async def import_from_text(
    raw_text: str,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    filename: str = "import.txt",
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    """Public wrapper to seed/merge a profile from already-extracted CV text.

    The text IS the source document: it is persisted like an uploaded file so an
    Art. 15 answer names it and the retention worker sweeps it (SF-DOOR.3).
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("text must not be empty")
    stripped = raw_text.strip()
    return await _import_from_text(
        stripped,
        db,
        provider,
        created_via="cv_paste",
        embedding_provider=embedding_provider,
        storage=storage,
        source_bytes=stripped.encode("utf-8"),
        filename=filename,
        content_type="text/plain",
        user_id=user_id,
    )


async def import_from_linkedin(
    linkedin_json: dict,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    filename: str = "linkedin-export.json",
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    raw_text = _linkedin_to_text(linkedin_json)
    return await _import_from_text(
        raw_text,
        db,
        provider,
        created_via="linkedin_import",
        embedding_provider=embedding_provider,
        storage=storage,
        source_bytes=json.dumps(linkedin_json, ensure_ascii=False).encode("utf-8"),
        filename=filename,
        content_type="application/json",
        user_id=user_id,
    )


async def import_from_linkedin_zip(
    zip_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    filename: str = "linkedin-export.zip",
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    raw_text = parse_linkedin_zip(zip_bytes)
    return await _import_from_text(
        raw_text,
        db,
        provider,
        created_via="linkedin_import",
        embedding_provider=embedding_provider,
        storage=storage,
        source_bytes=zip_bytes,
        filename=filename,
        content_type="application/zip",
        user_id=user_id,
    )


async def import_from_linkedin_pdf(
    pdf_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    filename: str = "linkedin-export.pdf",
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    raw_text = parse_linkedin_pdf(pdf_bytes)
    return await _import_from_text(
        raw_text,
        db,
        provider,
        created_via="linkedin_import",
        embedding_provider=embedding_provider,
        storage=storage,
        source_bytes=pdf_bytes,
        filename=filename,
        content_type="application/pdf",
        user_id=user_id,
    )


async def _import_from_text(
    raw_text: str,
    db: AsyncSession,
    provider: LLMProvider,
    created_via: str = "cv_upload",
    embedding_provider: EmbeddingProvider | None = None,
    *,
    storage,
    source_bytes: bytes,
    filename: str,
    content_type: str,
    user_id: uuid.UUID | None = None,
) -> "ProfileImportResponse | CVUploadResponse":
    """The text doors' adapter over :func:`ingest_cv` (#367).

    Everything this function used to do inline — extraction, the refinement review,
    `annotate_expected_fields`, `enrich_skills`, the merge through `commit_ops` —
    is the ingest's, and is now shared with `upload_cv` rather than duplicated
    beside it. What is left here is the two response shapes: a held merge is the
    same `CVUploadResponse` the browser door returns, a merged import is the
    `ProfileImportResponse` subclass #615 introduced.
    """
    outcome = await ingest_cv(
        db,
        provider,
        storage,
        raw_text=raw_text,
        source_bytes=source_bytes,
        filename=filename,
        content_type=content_type,
        created_via=created_via,
        recipe=PROFILE_TEXT_RECIPE,
        user_id=user_id,
        embedding_provider=embedding_provider,
    )
    if outcome.held:
        return gated_upload_response(outcome)

    # #615 (ADR-063 amended 2026-08-28) — a SEPARATE builder from `_to_response`,
    # which also serves GET /api/profile and PATCH /{section} (neither is a merge;
    # refuter B MAJOR 1). A first import has nothing to reconcile against, so
    # `_apply_merge` returns the honest "applied, []" defaults there.
    record = await _get_latest(db)
    return _to_import_response(
        record,
        merge_status=outcome.merge.merge_status,
        not_applied=outcome.merge.not_applied,
        matched=outcome.merge.matched,
    )


async def get_profile(db: AsyncSession) -> MasterProfileResponse | None:
    record = await _get_latest(db)
    if not record:
        return None
    return _to_response(record)


async def profile_exists(db: AsyncSession) -> dict:
    """Lightweight check: returns exists + completeness_score without full profile payload."""
    record = await _get_latest(db)
    if not record:
        return {"exists": False, "completeness_score": 0.0}
    profile_data = MasterProfileData.model_validate(record.profile_json)
    return {
        "exists": True,
        "completeness_score": profile_data.calculate_completeness(),
    }


async def patch_profile_section(
    section: str,
    value: object,
    db: AsyncSession,
    source: str = "manual_edit",
    source_session_id: str | None = None,
    provider: LLMProvider | None = None,
    basis_updated_at: datetime | None = None,
) -> MasterProfileResponse:
    """The manual section edit — the `FieldEdit` intake, on `commit_ops`.

    #480 PR 3 (ADR-063 clause 8(d)/(e)). This used to be its own little write
    path: validate, enrich, mint one blob-shaped `EnrichmentRecord`, assign
    `profile_json`. It is now an adapter plus a call — the shaping decisions
    (which section, object or list, how to decode the payload) stay here in a
    pure function, and every guarantee about the WRITE belongs to the committer.

    What the edit gains by moving, over what it did before:

    * a **per-entry receipt**, removals included, instead of one opaque
      "section updated, old → new" blob (§7.7 / ADR-063 amended clause 8);
    * the trail is minted **unconditionally**, by one implementation;
    * the completeness recompute and both clocks come from the same place as
      every other intake, so they cannot drift apart per writer;
    * the ADR-063 clause-6 write token, and the invariant-2 persisted-denial
      re-floor the moment PR 4 lands — a manual edit inherits it for free.

    Everything the two doors observe is unchanged: `_VALID_SECTIONS` refusals
    (`ValueError` → 422 / `invalid_input`), the #178 merge-patch semantics for
    object sections, wholesale replacement for lists, the response shape, and
    the STATUS a skill arrives with (the payload's own, or the schema default —
    the op round-trips the section through `MasterProfileData` exactly as this
    function always did, and nothing on the write path touches `status`).

    `grounding=None`: a manual edit is a DIRECT act. The committer never
    re-adjudicates the user (§7.4 ruling, ADR-061 clause 2) — requiring turn
    text here would put an LLM adjudication in front of every keystroke the
    candidate makes about their own history.

    The CV section editor reaches the vault through this function (#336), so it
    inherits all of the above transitively — which is the write half of FMEA
    SF-VAULT.4's laundering shape. The read half (the denial-release corpus)
    is PR 4.
    """
    # Pure adapter first: a mis-shaped payload is refused before anything
    # touches the database, exactly as it was.
    op = build_replace_section_op(section, value, basis_updated_at=basis_updated_at)

    record = await _get_latest(db)
    if not record:
        raise LookupError("No profile found")

    result = await commit_ops(
        db,
        [op],
        CommitProvenance(
            source=source,
            intake="field_edit",
            session_id=source_session_id,
            actor="candidate",
        ),
        record=record,
        # A direct act — see the docstring.
        grounding=None,
        # ADR-063 amendment (5) / #339: snapshot coverage stays with the import
        # writers. A manual edit captured none before and captures none now;
        # the omission is a parameter that says so, not a silent gap.
        snapshot=None,
        # #337 — the deterministic half runs regardless (the committer's
        # invariant 6); the LLM estimate is layered on only where this intake
        # ever ran it, so no door starts paying for an LLM call it did not make
        # before. Widening it to every section would be a cost decision nobody
        # has taken.
        llm_provider=provider if section in {"work_experience", "skills"} else None,
    )

    # TODO US179: edited/added roles here get the lean-floor expectation set until a provider is threaded in (fast-follow). Floor fallback is safe (under-asks).
    # Flush-not-commit (ADR-063 amended clause 6): the door owns its
    # transaction — dropping this line is a silent no-write.
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


async def get_enrichment_history(db: AsyncSession) -> list[EnrichmentRecord]:
    record = await _get_latest(db)
    if not record:
        return []
    profile_data = MasterProfileData.model_validate(record.profile_json)
    if not profile_data.metadata:
        return []
    return profile_data.metadata.enrichment_history


async def get_profile_changes(db: AsyncSession) -> ProfileChangesResponse:
    """US145 / ADR-040 — the combined "what changed & why" surface contract:
    the decision trail plus any pending conflicts, read from the Master Profile only.
    Never touches the source uploads (retention-independent — ADR-005)."""
    record = await _get_latest(db)
    if not record:
        return ProfileChangesResponse()
    profile_data = MasterProfileData.model_validate(record.profile_json)
    if not profile_data.metadata:
        return ProfileChangesResponse()
    return ProfileChangesResponse(
        enrichment_history=profile_data.metadata.enrichment_history,
        pending_conflicts=profile_data.metadata.pending_conflicts,
    )


async def get_profile_health(db: AsyncSession) -> ProfileHealthResponse:
    """US160 (E033 / ADR-041 amended) — deterministic Tier-2 health for the
    current profile: conflict + accuracy issues plus a completeness block.

    No LLM; reads only the durable Master Profile (never the 7-day upload —
    ADR-005). An absent profile is reported as empty health, not a 404, so the
    Health panel renders uniformly."""
    record = await _get_latest(db)
    if not record:
        return ProfileHealthResponse(completeness=CompletenessBlock(score=0.0))
    profile_data = MasterProfileData.model_validate(record.profile_json)
    na_fields = (record.profile_json.get("_meta") or {}).get("na_fields", [])
    return assess_health(profile_data, na_fields=na_fields)


async def resolve_conflict(
    conflict_id: str,
    resolution: str,
    value: object,
    db: AsyncSession,
) -> MasterProfileResponse:
    """Resolve a pending conflict by conflict_id — the `ResolveField` intake.

    resolution:
        "existing" — discard the incoming value, keep existing as-is
        "incoming" — accept the incoming value into the profile field
        "manual"   — write `value` into the profile field

    #480 PR 5 (ADR-063 clause 8(d)/(e)), and this is the writer #512 was filed
    about. It used to assign `profile_json` itself: no reconcile, no stance
    guard, no denial floor, no committer — while writing into
    `work_experience[].role`/`.company` and the bullet lists. PR 4 narrowed the
    denial-release corpus to attested entity labels, which deliberately INCLUDES
    role, company and technologies, so this writer's output became
    release-relevant while still travelling an unguarded path. Routing it here
    is the closure: a resolution is now an ordinary attested write — receipted,
    trailed, completeness-recomputed and **re-floor-guarded** (invariant 2),
    like every other door. No door writes those fields unguarded any more.

    What is deliberately unchanged: the `LookupError` for a missing profile or
    an unknown conflict (the interview's `_resolve_conflict_safely` relies on it
    to stay idempotent on resume), the `ValueError` for an unknown resolution
    (422 at the REST door), the #218 write-back semantics, and
    removal-on-resolve of the parked conflict.

    `grounding=None`: answering a dispute is a DIRECT act. The committer never
    re-adjudicates the candidate (§7.4 / ADR-061 clause 2) — the decision IS the
    testimony, and the open dispute is what authorises the overwrite.
    """
    record = await _get_latest(db)
    if not record:
        raise LookupError("No profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)

    if not profile_data.metadata or not profile_data.metadata.pending_conflicts:
        raise LookupError(f"Conflict '{conflict_id}' not found")

    conflict = next(
        (
            c
            for c in profile_data.metadata.pending_conflicts
            if c.conflict_id == conflict_id and not c.resolved
        ),
        None,
    )
    if conflict is None:
        raise LookupError(f"Conflict '{conflict_id}' not found")

    # Pure adapter first: an unknown resolution is refused before anything
    # touches the database, exactly as it was.
    op = build_resolve_field_op(conflict, resolution=resolution, value=value)

    result = await commit_ops(
        db,
        [op],
        CommitProvenance(
            source="manual_edit",
            intake="conflict_resolution",
            actor="candidate",
        ),
        record=record,
        grounding=None,
        # ADR-063 amendment (5) / #339 — snapshot coverage stays with the import
        # writers. A resolution captured none before and captures none now; the
        # omission is a parameter that says so, not a silent gap.
        snapshot=None,
    )

    # Flush-not-commit (ADR-063 amended clause 6): the CONFLICT-RESOLUTION door
    # owns its transaction — dropping this line is a silent no-write, and the
    # candidate is re-asked a dispute they already answered.
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


async def resolve_confirmation(
    confirmation_id: str,
    chosen_option: str,
    db: AsyncSession,
) -> MasterProfileResponse:
    """Resolve a pending import-time confirmation (E037 PQ #4).

    The reconciler already applied its best-effort merge at import time; a
    confirmation asks the user to confirm/steer an *identity* judgement (synonym
    role, project-vs-position, DE↔EN employer) it was unsure about. Recording the
    user's chosen option marks the confirmation resolved and removes it from the
    pending list. (Re-running the reconciler with the chosen option as context to
    physically re-shape the merge is a richer follow-up — see the seam noted on the
    epic; the minimum here surfaces a clean, answerable dialog instead of a garbled
    string and durably records the answer.)

    #480 PR 5 — the `ResolveConfirmation` intake (design §4.5). Two things
    change by routing it through `commit_ops`:

    * **a behavioural delta, not a refactor**: this writer moved
      `last_updated` and never recomputed `completeness_score`, so the stored
      score drifted from the vault every time a confirmation was answered. The
      committer's invariant 4 recomputes universally, so it comes for free
      (design §6 row 5, "+ missing recompute").
    * the park+clear lifecycle completes. With a durable CLEAR in the op
      vocabulary, `commit_ops` parks every intake's asks unconditionally and
      the interview's own in-session resolution (#187) clears the metadata park
      through this same act — so an answered ask can never resurface in a later
      session via `_open_confirmations`, and an ABANDONED one survives instead
      of dying with its session.

    The `LookupError` contract is unchanged: `_resolve_confirmation_safely`
    swallows exactly it to stay idempotent when an interview is resumed past a
    question that has already been answered.
    """
    record = await _get_latest(db)
    if not record:
        raise LookupError("No profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)
    if not profile_data.metadata or not profile_data.metadata.pending_confirmations:
        raise LookupError(f"Confirmation '{confirmation_id}' not found")

    confirmation = next(
        (
            c
            for c in profile_data.metadata.pending_confirmations
            if c.confirmation_id == confirmation_id and not c.resolved
        ),
        None,
    )
    if confirmation is None:
        raise LookupError(f"Confirmation '{confirmation_id}' not found")

    result = await commit_ops(
        db,
        [build_resolve_confirmation_op(confirmation, chosen_option)],
        CommitProvenance(
            source="manual_edit",
            intake="confirmation_resolution",
            actor="candidate",
        ),
        record=record,
        # A direct act — the candidate answering their own question.
        grounding=None,
        snapshot=None,
    )

    # Flush-not-commit (ADR-063 amended clause 6): the CONFIRMATION-RESOLUTION
    # door owns its transaction — dropping this line is a silent no-write, and
    # the parked ask comes back in the next session.
    await db.commit()
    await db.refresh(result.record)
    return _to_response(result.record)


# US154 (document-type) and US155 (name-mismatch) detection live in the canonical
# merge_gate module (US167 / ADR-041 amended) — re-exported here so the upload-time
# warning path and the existing tests keep importing them from this package.
from applire.services.profile.merge_gate import (  # noqa: E402
    evaluate_merge_gate,
    looks_like_cv as _looks_like_cv,
    names_clearly_differ as _names_clearly_differ,
)


class StagedExtractionNotFound(Exception):
    """No parked (gated) upload exists for the given id."""


class StagedExtractionAlreadyResolved(Exception):
    """The parked upload was already merged or discarded."""


# A parked gate is "open" until the user resolves it (US167 / ADR-041 amended).
_OPEN_GATES = {"not_a_cv", "name_divergence"}


async def list_open_gates(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> list[UploadRecord]:
    """Return parked uploads still holding an unresolved integrity gate (US167).

    These are the deferred Tier-1 gates US163 escalates into the JD interview —
    oldest first, so the longest-parked confirmation is asked first.

    #367 (adversarial): a hold `import_cv` raises before any `User` row exists
    is persisted with `user_id=NULL` (`mcp/server.py::_import_user_id` — "an
    empty `users` table is an ownerless import rather than a failure", a real
    state on the documented `python -m applire.mcp` standalone launch, which
    never runs `applire.main`'s lifespan). An exact `user_id == :uid` filter
    silently drops that row from the Health hub / `held_merges` the instant a
    `User` row later appears, so the human is never asked to adjudicate a CV
    the gate genuinely parked. Community is single-user (ADR-022 rejected), so
    an ownerless row is unambiguously "the" user's — the same shape
    `import_jobs.py::list_import_jobs` already uses for the sibling async-
    import door (`or_(CVImportJob.user_id == user_id, CVImportJob.user_id.is_(None))`).
    """
    query = (
        select(UploadRecord)
        .where(UploadRecord.gate_status.in_(tuple(_OPEN_GATES)))
        .order_by(UploadRecord.created_at.asc())
    )
    if user_id is not None:
        query = query.where(
            or_(UploadRecord.user_id == user_id, UploadRecord.user_id.is_(None))
        )
    return list((await db.execute(query)).scalars().all())


def _undated_positions(data: MasterProfileData) -> int:
    """Count work entries missing a start date (FMEA JF-M-2.7)."""
    return sum(1 for w in data.work_experience if not (w.start_date and w.start_date.strip()))


# Hard cap on the source text handed to the extraction LLM. Lives HERE, not in one
# door, because it is a property of the ingest: a LinkedIn PDF can be 30k+ chars of
# endorsements/courses, and before #367 only the browser door capped it. We cut at the
# last newline before the limit so the model always receives complete lines.
_MAX_CV_TEXT_CHARS = 25_000


@dataclass(frozen=True)
class ExtractionRecipe:
    """The ONE sanctioned difference between the CV-ingestion doors (ADR-066 cl. 3).

    #367 converged the doors on a single ingest function. They genuinely still run
    different extraction prompts and different refinement reviewers — the browser CV
    upload uses the `cv_extraction` pair, the agent/LinkedIn text doors the
    `profile_extraction` pair. ADR-066 clause 3 permits an intentional difference
    *expressed as a named parameter on the one implementation*, and forbids it as a
    second function body. This dataclass is that parameter: two values, enumerable,
    with exactly one seam for the eventual shared rule module (vault collector #674,
    `decide:` line) to act on.
    """

    system: str
    build_user_prompt: object          # Callable[[str], str]
    refinement_system: str
    build_retry_prompt: object         # Callable[..., str]
    reviewer_system: str
    build_review_prompt: object        # Callable[..., str]
    chain_id: str


#: The browser CV-upload door's recipe (`POST /api/profile/upload`, `/import-jobs`).
CV_UPLOAD_RECIPE = ExtractionRecipe(
    system=GENERIC_CV_EXTRACTION_PROMPT,
    build_user_prompt=build_generic_prompt,
    refinement_system=CV_EXTRACTION_REFINEMENT_PROMPT,
    build_retry_prompt=_build_cv_extraction_retry_prompt,
    reviewer_system=_CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
    build_review_prompt=_build_cv_extraction_review_prompt,
    chain_id="cv_extraction",
)

#: The text doors' recipe — MCP `import_cv` and the LinkedIn/XING structured export.
PROFILE_TEXT_RECIPE = ExtractionRecipe(
    system=SYSTEM_PROMPT,
    build_user_prompt=build_user_prompt,
    refinement_system=PROFILE_EXTRACTION_REFINEMENT_PROMPT,
    build_retry_prompt=_build_extraction_retry_prompt,
    reviewer_system=_EXTRACTION_REVIEW_SYSTEM_PROMPT,
    build_review_prompt=_build_extraction_review_prompt,
    chain_id="profile_extraction",
)


@dataclass
class IngestOutcome:
    """What one CV ingest did — the fact, not a transport shape.

    Every door adapts this into its own response (`CVUploadResponse`,
    `ProfileImportResponse`, the MCP summary dict); none of them re-derives it.
    """

    gate: str                                   # "none" | "not_a_cv" | "name_divergence"
    upload_record: UploadRecord                 # always written — SF-DOOR.3
    incoming: MasterProfileData
    looks_like_cv: bool
    undated_positions: int
    account_name: str | None = None
    cv_name: str | None = None
    merge: "ApplyMergeOutcome | None" = None    # None exactly when held

    @property
    def held(self) -> bool:
        return self.gate != "none"


async def _persist_upload_record(
    db: AsyncSession,
    *,
    source_bytes: bytes,
    filename: str,
    content_type: str,
    storage,
    user_id: uuid.UUID | None,
    provider: LLMProvider,
    gate_status: str | None = None,
    staged_extraction: dict | None = None,
) -> UploadRecord:
    """Persist the source document + its `UploadRecord` (SF-DOOR.3, GDPR Art. 15).

    One writer for both outcomes and all three doors: content hash, mime type, byte
    size, LLM provider and the ADR-005 expiry, plus the stored file the retention
    worker sweeps. A door supplies the bytes; it cannot decline to persist them
    (ADR-066 cl. 1 — a door may not hold state the core does not).
    """
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    file_path = await storage.save(source_bytes, filename)
    record = UploadRecord(
        user_id=user_id,
        original_filename=filename,
        content_hash=content_hash,
        mime_type=content_type,
        file_path=file_path,
        byte_size=len(source_bytes),
        llm_tokens_used=None,  # token tracking deferred — LLMProvider ABC not extended yet
        llm_provider=provider.__class__.__name__,
        gate_status=gate_status,
        staged_extraction=staged_extraction,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def gated_upload_response(outcome: IngestOutcome) -> CVUploadResponse:
    """The ONE representation of a held merge, whichever door raised it (US167).

    `ProfileImportView` reads exactly these field names (`status === "GATED"`,
    `gate`, `staged_id`, `account_name`, `cv_name`), and the parked row is resolved
    through the one `resolve_staged_extraction` regardless of origin.
    """
    return CVUploadResponse(
        profile_id=None,
        status="GATED",
        completeness_score=0.0,
        conflicts=[],
        enrichment_record_id=None,
        expires_at=outcome.upload_record.expires_at,
        looks_like_cv=(outcome.gate != "not_a_cv"),
        name_mismatch=(outcome.gate == "name_divergence"),
        undated_positions=outcome.undated_positions,
        gate=outcome.gate,
        account_name=outcome.account_name,
        cv_name=outcome.cv_name,
        staged_id=outcome.upload_record.id,
    )


async def ingest_cv(
    db: AsyncSession,
    provider: LLMProvider,
    storage,  # StorageProvider — imported inline to avoid circular imports
    *,
    raw_text: str,
    source_bytes: bytes,
    filename: str,
    content_type: str,
    created_via: str,
    recipe: ExtractionRecipe,
    user_id: uuid.UUID | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> IngestOutcome:
    """Ingest ONE CV into the Master Profile — the single implementation behind every
    CV-ingestion door (#367; ADR-066 cl. 1–3, ADR-041 amended 2026-09-13).

    extraction (by *recipe*) → refinement review (by *recipe*) → role-aware expected
    fields → skill enrichment → **the US167/ADR-041 pre-merge integrity gate** →
    park-or-merge → `UploadRecord` + stored source.

    The gate used to be called by `upload_cv` and by nothing else, so two of the three
    doors merged an arbitrary document unheld and left no audit trail (SF-DOOR.2/.3).
    It is called here, unconditionally, and a door cannot omit it without deleting it.
    Doors supply transport facts (the already-extracted text, the source bytes to keep,
    who is importing) and adapt the returned :class:`IngestOutcome`; they decide
    nothing about the merge.
    """
    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER

    if len(raw_text) > _MAX_CV_TEXT_CHARS:
        cut = raw_text.rfind("\n", 0, _MAX_CV_TEXT_CHARS)
        if cut == -1:
            cut = _MAX_CV_TEXT_CHARS
        logger.warning(
            "CV text truncated for LLM extraction: %d → %d chars (file: %s)",
            len(raw_text),
            cut,
            filename,
        )
        raw_text = raw_text[:cut]

    # Cap-safe extraction: single call on the fast path, segmented (outline-then-expand)
    # on truncation/timeout or a known-small cap (ADR-047 / US195) — so a dense CV is
    # never silently dropped behind an optimistic "complete" UI.
    data: dict = await extract_with_fallback(
        raw_text,
        provider,
        system=recipe.system,
        user_prompt=recipe.build_user_prompt(raw_text),
    )
    data = await review_and_refine(
        source=raw_text,
        draft=data,
        generator_prompt_fn=recipe.build_retry_prompt,
        generator_system=recipe.refinement_system,
        reviewer_prompt_fn=recipe.build_review_prompt,
        reviewer_system=recipe.reviewer_system,
        provider=provider,
        max_retries=LLM_REVIEW_MAX_RETRIES,
        generator_max_tokens=CV_EXTRACTION_MAX_TOKENS,
        chain_id=recipe.chain_id,
    )
    # US179 / ADR-041: annotate role-aware expected fields at write time so the stored
    # completeness score and the enrichment gaps derive from one source. Best-effort:
    # never raises (provider errors leave entries unannotated → lean-floor fallback).
    await annotate_expected_fields(data, provider)
    incoming = MasterProfileData.model_validate(data)
    incoming = await enrich_skills(incoming, provider)
    now = datetime.now(timezone.utc)

    # Pre-merge integrity gate (US167 / ADR-041 amended) — HOLD before commit.
    # not-a-CV and account-vs-CV name divergence are caught *before* the additive merge
    # can overwrite anything; safe default = don't merge. A held merge parks the staged
    # extraction for the user to resolve (merge / discard).
    existing = await _get_latest(db)
    account_name = (
        MasterProfileData.model_validate(existing.profile_json).personal_info.name
        if existing
        else None
    )
    gate = evaluate_merge_gate(account_name, incoming)

    if gate.gate != "none":
        record = await _persist_upload_record(
            db,
            source_bytes=source_bytes,
            filename=filename,
            content_type=content_type,
            storage=storage,
            user_id=user_id,
            provider=provider,
            gate_status=gate.gate,
            staged_extraction=incoming.model_dump(mode="json"),
        )
        return IngestOutcome(
            gate=gate.gate,
            upload_record=record,
            incoming=incoming,
            looks_like_cv=(gate.gate != "not_a_cv"),
            undated_positions=_undated_positions(incoming),
            account_name=gate.account_name,
            cv_name=gate.cv_name,
            merge=None,
        )

    merge_outcome = await _apply_merge(
        db, incoming, source=created_via, emb_provider=emb_provider, provider=provider, now=now
    )
    record = await _persist_upload_record(
        db,
        source_bytes=source_bytes,
        filename=filename,
        content_type=content_type,
        storage=storage,
        user_id=user_id,
        provider=provider,
    )
    return IngestOutcome(
        gate="none",
        upload_record=record,
        incoming=incoming,
        looks_like_cv=_looks_like_cv(incoming),
        undated_positions=_undated_positions(incoming),
        account_name=account_name,
        cv_name=gate.cv_name,
        merge=merge_outcome,
    )


async def upload_cv(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    db: AsyncSession,
    provider: LLMProvider,
    storage,  # StorageProvider — imported inline to avoid circular imports
    ocr_extractor,  # CVImageExtractor
    job_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> CVUploadResponse:
    """Parse an uploaded CV file and merge it into the Master Profile (ADR 014).

    Steps:
      1. Extract raw text (format-aware, OCR fallback for scanned PDFs/images)
      2. LLM extraction → MasterProfileData
      3. Merge with existing profile (or create first profile)
      4. Persist UploadRecord (file + cost metadata)
      5. Return CVUploadResponse with status, completeness, and conflicts

    *job_id* is accepted for REST API compatibility only (M5.1.3, 2026-09-11): every
    upload now builds the generic extraction prompt regardless of whether a job_id is
    supplied. It no longer selects a JD-aware extraction path — see the retired
    JD_AWARE_CV_EXTRACTION_PROMPT version-header note in prompts/cv_extraction.py.
    """
    from applire.services.cv_parser import extract_text

    # 1. Text extraction — each CV is analysed individually; never concatenated.
    #    Format-aware with an OCR fallback for scanned PDFs/images; this is the one
    #    step that stays door-local (the agent door's `pypdf` read cannot OCR).
    #    The 25k-char cap that used to live here is now part of the ingest, so all
    #    three doors get it (#367).
    raw_text = await extract_text(file_bytes, filename, content_type, ocr_extractor)

    # 2. job_id is accepted for REST API compatibility only (M5.1.3) — it no longer
    #    selects a different extraction path. Every upload builds the generic prompt.
    if job_id is not None:
        logger.info(
            "upload_cv: job_id=%s supplied but ignored — JD-aware extraction was "
            "retired M5.1.3 (2026-09-11); extraction is always generic.",
            job_id,
        )

    # 3. THE ingest (#367 / ADR-066 cl. 1–3): extraction → review → expected fields →
    #    skill enrichment → the US167 gate → park-or-merge → UploadRecord + source
    #    file. This door adds the OCR-capable text extraction above and the response
    #    shaping below; it decides nothing about the merge.
    outcome = await ingest_cv(
        db,
        provider,
        storage,
        raw_text=raw_text,
        source_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        created_via="cv_upload",
        recipe=CV_UPLOAD_RECIPE,
        user_id=user_id,
        embedding_provider=embedding_provider,
    )

    # 4. A held merge is the same object on every door (US167).
    if outcome.held:
        return gated_upload_response(outcome)

    # 5. Clean CV — the merge committed (or the first profile was created).
    merge_outcome = outcome.merge
    completeness = merge_outcome.completeness
    conflicts = merge_outcome.conflicts

    status = "DRAFT" if (completeness < 0.5 or bool(conflicts)) else "COMPLETE"
    conflict_summaries = [
        ConflictSummary(
            conflict_id=c.conflict_id,
            section=c.section,
            field=c.field,
            source=c.source,
        )
        for c in conflicts
    ]

    return CVUploadResponse(
        profile_id=merge_outcome.profile_id,
        status=status,
        completeness_score=completeness,
        conflicts=conflict_summaries,
        enrichment_record_id=merge_outcome.enrichment_id,
        expires_at=outcome.upload_record.expires_at,
        # Upload-time input-plausibility signals (Input Integrity sprint, issue #43):
        # document-type (US154/2.3) and per-CV completeness (US157/2.7).
        looks_like_cv=outcome.looks_like_cv,
        name_mismatch=False,  # a clean merge by definition had no name divergence
        undated_positions=outcome.undated_positions,
        gate="none",
        # #615 (ADR-063 amended 2026-08-28) — the SAME fact on every import
        # door; the async job's result inherits it via CVImportStatusResponse
        # wrapping this class unchanged (import_jobs.py — no adapter needed).
        merge_status=merge_outcome.merge_status,
        not_applied=merge_outcome.not_applied,
    )


@dataclass
class ApplyMergeOutcome:
    """#615 (ADR-063 amended 2026-08-28, refuter B MAJOR 1) — ``_apply_merge``
    returns the merge's FACT, not a positional tuple that silently discarded
    it. The old ``(profile_id, completeness, conflicts, enrichment_id)`` tuple
    is why ``not_applied``/``reconciliation`` never reached either of this
    function's two callers — both now read ``not_applied``/``merge_status``
    off this object instead of unpacking a wider tuple by position."""

    profile_id: uuid.UUID
    completeness: float
    conflicts: list
    enrichment_id: uuid.UUID
    not_applied: list[ImportNotApplied] = field(default_factory=list)
    merge_status: ImportMergeStatus = "applied"
    # #674 line 72 — the same drop, one field later: `matched` reached the
    # persisted `EnrichmentRecord` and stopped, because this outcome object did
    # not carry it to either caller.
    matched: list[MatchReceipt] = field(default_factory=list)


async def _apply_merge(
    db: AsyncSession,
    incoming: MasterProfileData,
    *,
    source: str,
    emb_provider: EmbeddingProvider,
    provider: LLMProvider,
    now: datetime | None = None,
) -> ApplyMergeOutcome:
    """Additively merge ``incoming`` into the latest profile (or create the first),
    commit, and return the outcome as an :class:`ApplyMergeOutcome`.

    The pre-merge gate (US167) is the caller's responsibility — by the time this
    runs the merge is authorised (clean upload, or a user-resolved staged merge).
    """
    now = now or datetime.now(timezone.utc)
    existing = await _get_latest(db)

    if existing:
        existing_data = MasterProfileData.model_validate(existing.profile_json)
        from applire.services.session import get_ui_language

        lang = await get_ui_language(db)
        merge_result = await reconcile_import(
            existing_data, incoming, source=source, provider=provider, lang=lang,
        )
        merged = merge_result.merged_profile
        enrichment = _enrichment_from_merge(merge_result, source=source)

        # #333 — dual-door rule: the browser /upload door preserves the same
        # still-open parked items as import_from_text, through the same helper.
        _apply_import_metadata(
            merged,
            existing_data,
            merge_result,
            created_via=source,
            created_at=existing.created_at,
        )

        # ADR-063 — the ONE write path; identical call to `_import_from_text`'s,
        # which is the point (ADR-058 clause 2: the same act may not behave
        # differently by door). The committer mints the merge's enrichment
        # record and keys the ADR-042 pre-merge snapshot to it, so the id this
        # function returns, the stored record and the snapshot still agree —
        # they just agree on the committer's id instead of a pre-generated one.
        committed = await commit_ops(
            db,
            [
                ApplyImportMerge(
                    merged=merged,
                    changes=enrichment.changes,
                    reconciliation=enrichment.reconciliation,
                    not_applied=enrichment.not_applied,
                    matched=enrichment.matched,  # #707
                )
            ],
            CommitProvenance(source=source, intake="import", actor="candidate"),
            record=existing,
            snapshot=SnapshotClass.MERGE,
            enrichment=EnrichPolicy.SKIP,
            embedding_provider=emb_provider,
        )
        await db.commit()
        await db.refresh(existing)
        return ApplyMergeOutcome(
            profile_id=existing.id,
            completeness=committed.completeness,
            conflicts=merge_result.conflicts,
            enrichment_id=uuid.UUID(committed.enrichment_record.id),
            not_applied=merge_result.not_applied,
            merge_status=("partial" if merge_result.not_applied else "applied"),
            matched=enrichment.matched,  # #674 line 72
        )

    # First upload — the committer creates the profile (#480 PR 8), through the
    # identical call `_import_from_text`'s creation branch makes: the same act
    # may not behave differently by door (ADR-058 clause 2), and that held for
    # the merge branch since PR 2 while the two creation branches were still
    # two hand-rolled copies of each other.
    enrichment = _make_enrichment_record(
        source=source, action="added", new_value="initial import"
    )
    incoming.metadata = ProfileMetadata(
        completeness_score=incoming.calculate_completeness(),
        created_via=source,
        created_at=now,
        last_updated=now,
    )
    committed = await commit_ops(
        db,
        [ApplyImportMerge(merged=incoming, changes=enrichment.changes)],
        CommitProvenance(source=source, intake="import", actor="candidate"),
        record=None,
        snapshot=None,
        enrichment=EnrichPolicy.SKIP,
        embedding_provider=emb_provider,
    )
    await db.commit()
    await db.refresh(committed.record)
    # The enrichment id this door returns now NAMES the record on disk — the
    # property the merge branch gained in PR 2. The creation branch used to
    # return a `uuid4()` minted at the top of this function that referred to
    # nothing, so `CVUploadResponse.enrichment_record_id` was unresolvable for
    # exactly the upload that created the profile.
    return ApplyMergeOutcome(
        profile_id=committed.record.id,
        completeness=committed.completeness,
        conflicts=[],
        enrichment_id=uuid.UUID(committed.enrichment_record.id),
        # #615 — a first import has nothing to reconcile against.
        not_applied=[],
        merge_status="applied",
    )


async def resolve_staged_extraction(
    db: AsyncSession,
    staged_id: uuid.UUID,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    provider: LLMProvider | None = None,
) -> StagedResolveResponse:
    """Resolve a parked (gated) upload (US167). ``action`` is ``"merge"`` (apply the
    staged extraction additively, re-using the original LLM result) or ``"discard"``
    (drop it, leaving the profile untouched). Idempotency is enforced: a second
    resolve raises ``StagedExtractionAlreadyResolved``.

    When ``user_id`` is given the lookup is scoped to that owner, so a foreign
    upload is indistinguishable from a missing one (IDOR guard) — a parked CV
    can only be resolved by the account that uploaded it. An ownerless row
    (``user_id IS NULL`` — #367 adversarial: `import_cv` ran before any `User`
    row existed) is included for any given ``user_id`` rather than excluded:
    Community is single-user (ADR-022 rejected), so it is unambiguously "the"
    user's, and an exact-equality filter made such a hold permanently
    unresolvable the moment a `User` row appeared (`list_open_gates` carries
    the same widening, and the same reasoning).
    """
    query = select(UploadRecord).where(UploadRecord.id == staged_id)
    if user_id is not None:
        query = query.where(
            or_(UploadRecord.user_id == user_id, UploadRecord.user_id.is_(None))
        )
    rec = (await db.execute(query)).scalar_one_or_none()
    if rec is None or rec.gate_status is None:
        raise StagedExtractionNotFound(str(staged_id))
    if rec.gate_status not in _OPEN_GATES:
        raise StagedExtractionAlreadyResolved(rec.gate_status)

    if action == "discard":
        rec.gate_status = "resolved_discarded"
        await db.commit()
        return StagedResolveResponse(staged_id=staged_id, action="discard")

    if action == "merge":
        emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER
        incoming = MasterProfileData.model_validate(rec.staged_extraction)
        # The staged path re-uses the original extraction (no re-extraction) so it
        # has no DI provider of its own; the reconcile merge still needs one.
        # Callers (the router via Depends, the interview gate via its session
        # provider) inject one; fall back to the configured factory otherwise so
        # tests can pass a controlled stub instead of hitting a real provider.
        if provider is None:
            from applire.providers import get_provider

            provider = get_provider()
        merge_outcome = await _apply_merge(
            db, incoming, source="cv_upload", emb_provider=emb_provider, provider=provider
        )
        rec.gate_status = "resolved_merged"
        await db.commit()
        return StagedResolveResponse(
            staged_id=staged_id,
            action="merge",
            profile_id=merge_outcome.profile_id,
            completeness_score=merge_outcome.completeness,
            conflicts=[
                ConflictSummary(
                    conflict_id=c.conflict_id,
                    section=c.section,
                    field=c.field,
                    source=c.source,
                )
                for c in merge_outcome.conflicts
            ],
            # #615 (ADR-063 amended 2026-08-28) — the same fact every import
            # door carries; a "discard" resolve above never reaches here, so
            # the "applied, []" defaults on that branch stay honest.
            merge_status=merge_outcome.merge_status,
            not_applied=merge_outcome.not_applied,
        )

    raise ValueError(f"unknown resolve action: {action!r}")
