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
from datetime import datetime, timezone
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import CV_EXTRACTION_MAX_TOKENS, LLM_REVIEW_MAX_RETRIES
from applire.models.profile import MasterProfile
from applire.models.uploads import UploadRecord
from applire.prompts.cv_extraction import (
    CV_EXTRACTION_REFINEMENT_PROMPT,
    GENERIC_CV_EXTRACTION_PROMPT,
    JD_AWARE_CV_EXTRACTION_PROMPT,
    build_generic_prompt,
    build_jd_aware_prompt,
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
from applire.services.reviewer import review_and_refine
from applire.services.skill_enrichment import enrich_skills, enrich_skills_deterministic
from applire.schemas.profile import (
    CompletenessBlock,
    Conflict,
    ConflictSummary,
    CVUploadResponse,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    MasterProfileResponse,
    PendingConfirmation,
    ProfileChangesResponse,
    ProfileHealthResponse,
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


# ── #218 — writing a resolved conflict back to the value it disputes ──────────
# A conflict is only worth surfacing if answering it changes the vault. Before
# this, the resolution path could address a dict section (`personal_info`,
# `professional_summary`) and — for `work_experience` only — the FIRST entry
# whose scalar field still equalled the old value. Neither reaches one string
# inside a role's `responsibilities` / `achievements` list, so a bullet-level
# dispute resolved into nothing and the rejected variant stayed in the vault.
#
# All three helpers are fact-level: they locate a named entity and rewrite a
# named string. No similarity matching, no judgement about whether two bullets
# say the same thing — that verdict is the reconciler model's (ADR-062 clause 1)
# and arrives on the Conflict record.


def _conflict_norm(value: object) -> str:
    """The applier's bullet-comparison normalisation (``reconcile.apply._norm``)."""
    return (str(value) if value is not None else "").strip().casefold()


def _entry_for_conflict(entries: list[dict], conflict: Conflict) -> dict | None:
    """The list entry a conflict belongs to: by ``entity_id``, else by value."""
    if conflict.entity_id:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == conflict.entity_id:
                return entry
        return None
    # Conflicts parked before `entity_id` existed carry no identity — fall back
    # to the pre-#218 behaviour: the first entry still holding the old value.
    old = _conflict_norm(conflict.existing_value)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        current = entry.get(conflict.field)
        if isinstance(current, list):
            if any(_conflict_norm(item) == old for item in current):
                return entry
        elif current == conflict.existing_value:
            return entry
    return None


def _rewrite_bullet_list(
    bullets: list, existing: object, incoming: object, chosen: object
) -> list:
    """Leave the chosen wording in place of the contested pair, order preserved.

    Both disputed variants may be stored (the reconciler's bullet dedup is exact-
    string, so two wordings of one fact both survive an import — #453). Resolving
    the dispute must therefore also remove the variant the candidate rejected;
    otherwise the vault keeps serving the figure they just ruled out.
    """
    contested = {_conflict_norm(existing), _conflict_norm(incoming)} - {""}
    chosen_key = _conflict_norm(chosen)
    result: list = []
    placed = False
    for bullet in bullets:
        key = _conflict_norm(bullet)
        if key in contested:
            if chosen_key and not placed:
                result.append(chosen)
                placed = True
            continue  # the losing variant is dropped, not left alongside
        if chosen_key and key == chosen_key and placed:
            continue  # never leave the chosen wording twice
        result.append(bullet)
    if chosen_key and not placed and chosen_key not in {_conflict_norm(b) for b in result}:
        # The disputed wording is no longer stored (edited away, or the incoming
        # variant was never merged). The candidate's choice still has to land.
        result.append(chosen)
    return result


def _apply_resolution_to_list_section(
    entries: list, conflict: Conflict, chosen: object
) -> None:
    """Write ``chosen`` onto the entry the conflict names, in place."""
    entry = _entry_for_conflict(entries, conflict)
    if entry is None:
        return
    # A `field` the schema has no slot for needs no guard here: the write lands
    # on the dumped dict and `MasterProfileData.model_validate` drops it (pydantic
    # `extra="ignore"`), so the resolution is a quiet no-op either way. Pinned by
    # test_resolve_conflict_on_an_unknown_field_is_a_no_op.
    current = entry.get(conflict.field)
    if isinstance(current, list):
        entry[conflict.field] = _rewrite_bullet_list(
            current, conflict.existing_value, conflict.incoming_value, chosen
        )
    else:
        entry[conflict.field] = chosen


async def import_from_pdf(
    file_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    raw_text = extract_pdf_text(file_bytes)
    if not raw_text:
        raise ValueError("Could not extract text from PDF")
    return await _import_from_text(raw_text, db, provider, created_via="cv_upload", embedding_provider=embedding_provider)


async def import_from_text(
    raw_text: str,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    """Public wrapper to seed/merge a profile from already-extracted CV text."""
    if not raw_text or not raw_text.strip():
        raise ValueError("text must not be empty")
    return await _import_from_text(
        raw_text.strip(), db, provider, created_via="cv_paste", embedding_provider=embedding_provider
    )


async def import_from_linkedin(
    linkedin_json: dict,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    raw_text = _linkedin_to_text(linkedin_json)
    return await _import_from_text(raw_text, db, provider, created_via="linkedin_import", embedding_provider=embedding_provider)


async def import_from_linkedin_zip(
    zip_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    raw_text = parse_linkedin_zip(zip_bytes)
    return await _import_from_text(raw_text, db, provider, created_via="linkedin_import", embedding_provider=embedding_provider)


async def import_from_linkedin_pdf(
    pdf_bytes: bytes,
    db: AsyncSession,
    provider: LLMProvider,
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    raw_text = parse_linkedin_pdf(pdf_bytes)
    return await _import_from_text(raw_text, db, provider, created_via="linkedin_import", embedding_provider=embedding_provider)


async def _import_from_text(
    raw_text: str,
    db: AsyncSession,
    provider: LLMProvider,
    created_via: str = "cv_upload",
    embedding_provider: EmbeddingProvider | None = None,
) -> MasterProfileResponse:
    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER
    # Cap-safe extraction: single call on the fast path, segmented (outline-then-expand)
    # on truncation/timeout or a known-small cap (ADR-047 / US195) — so a dense CV is
    # never silently dropped behind an optimistic "complete" UI.
    data: dict = await extract_with_fallback(
        raw_text, provider, system=SYSTEM_PROMPT, user_prompt=build_user_prompt(raw_text),
    )
    data = await review_and_refine(
        source=raw_text,
        draft=data,
        generator_prompt_fn=_build_extraction_retry_prompt,
        generator_system=PROFILE_EXTRACTION_REFINEMENT_PROMPT,
        reviewer_prompt_fn=_build_extraction_review_prompt,
        reviewer_system=_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=LLM_REVIEW_MAX_RETRIES,
        generator_max_tokens=CV_EXTRACTION_MAX_TOKENS,
        chain_id="profile_extraction",
    )
    # US179 / ADR-041: annotate role-aware expected fields at write time so the
    # stored completeness score and the enrichment gaps derive from one source.
    # Best-effort: annotate_expected_fields never raises (provider errors leave
    # entries unannotated → scorer's lean floor fallback).
    await annotate_expected_fields(data, provider)
    incoming = MasterProfileData.model_validate(data)
    incoming = await enrich_skills(incoming, provider)
    now = datetime.now(timezone.utc)

    existing = await _get_latest(db)
    if existing:
        existing_data = MasterProfileData.model_validate(existing.profile_json)
        # Lazy import: session.py imports this package, so a module-top import
        # would create a circular import (import applire.services.session fails).
        from applire.services.session import get_ui_language

        lang = await get_ui_language(db)
        merge_result = await reconcile_import(
            existing_data, incoming, source=created_via, provider=provider, lang=lang,
        )

        merged = merge_result.merged_profile
        enrichment = _enrichment_from_merge(merge_result, source=created_via)

        _apply_import_metadata(
            merged,
            existing_data,
            merge_result,
            created_via=created_via,
            created_at=existing.created_at,
        )

        # ADR-063 — the ONE write path. The merge itself is the op (#480 PR 2 /
        # ADR-063 amended 2026-08-09 second entry: no reconciler-op sequence can
        # reproduce an import), and the committer owns the tail this function
        # used to hand-roll: the trail, the completeness recompute, both clocks,
        # the write token — and the ADR-042 pre-merge snapshot, which is now a
        # named parameter instead of an inline call only two writers remembered.
        await commit_ops(
            db,
            [
                ApplyImportMerge(
                    merged=merged,
                    changes=enrichment.changes,
                    reconciliation=enrichment.reconciliation,
                )
            ],
            CommitProvenance(source=created_via, intake="import", actor="candidate"),
            record=existing,
            snapshot=SnapshotClass.MERGE,
            # The import already ran `enrich_skills` WITH a provider on
            # `incoming` and `enrich_skills_deterministic` on the merged result
            # (inside `reconcile_import`); re-running the deterministic half
            # here would be a second pass over the same profile.
            enrichment=EnrichPolicy.SKIP,
            embedding_provider=emb_provider,
        )
        # Flush-not-commit (ADR-063 amended clause 6): the door still owns its
        # transaction — dropping this line is a silent no-write.
        await db.commit()
        await db.refresh(existing)
        return _to_response(existing)

    # First import — create profile
    enrichment = _make_enrichment_record(source=created_via, action="added", new_value="initial import")
    incoming.metadata = ProfileMetadata(
        completeness_score=incoming.calculate_completeness(),
        created_via=created_via,
        created_at=now,
        last_updated=now,
        enrichment_history=[enrichment],
    )

    profile_json = incoming.model_dump(mode="json")
    embedding = await _compute_embedding(profile_json, emb_provider)
    record = MasterProfile(profile_json=profile_json, embedding=embedding)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


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
    op = build_replace_section_op(section, value)

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
    """
    Resolve a pending conflict by conflict_id.

    resolution:
        "existing" — discard the incoming value, keep existing as-is
        "incoming" — accept the incoming value into the profile field
        "manual"   — write `value` into the profile field

    The conflict is marked resolved=True and removed from pending_conflicts.
    An enrichment record is appended.
    """
    record = await _get_latest(db)
    if not record:
        raise LookupError("No profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)

    if not profile_data.metadata or not profile_data.metadata.pending_conflicts:
        raise LookupError(f"Conflict '{conflict_id}' not found")

    conflict = next(
        (c for c in profile_data.metadata.pending_conflicts if c.conflict_id == conflict_id),
        None,
    )
    if conflict is None:
        raise LookupError(f"Conflict '{conflict_id}' not found")

    # Determine the winning value
    if resolution == "existing":
        chosen = conflict.existing_value
    elif resolution == "incoming":
        chosen = conflict.incoming_value
    elif resolution == "manual":
        chosen = value
    else:
        raise ValueError(f"Invalid resolution '{resolution}'. Must be existing, incoming, or manual.")

    # Apply the chosen value to the profile.
    section = conflict.section
    field_name = conflict.field
    profile_dict = profile_data.model_dump(mode="json")

    section_data = profile_dict.get(section)
    if isinstance(section_data, dict):
        section_data[field_name] = chosen
        profile_dict[section] = section_data
    # #218 — every entity section (work_experience, projects, volunteer_activities)
    # resolves the same way: find the entry the conflict names, then write the
    # chosen value onto its field — replacing the contested string in place when
    # that field is a bullet list.
    elif isinstance(section_data, list):
        _apply_resolution_to_list_section(section_data, conflict, chosen)
        profile_dict[section] = section_data

    updated = MasterProfileData.model_validate(profile_dict)

    # Mark conflict resolved and rebuild pending list
    resolved_ids = {conflict_id}
    updated.metadata = profile_data.metadata.model_copy(deep=True)
    updated.metadata.pending_conflicts = [
        c.model_copy(update={"resolved": True}) if c.conflict_id in resolved_ids else c
        for c in updated.metadata.pending_conflicts
        if c.conflict_id not in resolved_ids  # remove resolved conflicts from pending
    ]

    now = datetime.now(timezone.utc)
    enrichment = _make_enrichment_record(
        source="manual_edit",
        section=section,
        action="updated",
        old_value=conflict.existing_value,
        new_value=chosen,
    )
    updated.metadata.enrichment_history.append(enrichment)
    updated.metadata.last_updated = now
    updated.metadata.completeness_score = updated.calculate_completeness()

    record.profile_json = updated.model_dump(mode="json")
    record.updated_at = now
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


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
    string and durably records the answer.)"""
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
            if c.confirmation_id == confirmation_id
        ),
        None,
    )
    if confirmation is None:
        raise LookupError(f"Confirmation '{confirmation_id}' not found")

    updated = profile_data.model_copy(deep=True)
    updated.metadata.pending_confirmations = [
        c
        for c in updated.metadata.pending_confirmations
        if c.confirmation_id != confirmation_id
    ]

    now = datetime.now(timezone.utc)
    enrichment = _make_enrichment_record(
        source="manual_edit",
        section="metadata",
        action="updated",
        old_value=confirmation.question,
        new_value=chosen_option,
    )
    updated.metadata.enrichment_history.append(enrichment)
    updated.metadata.last_updated = now

    record.profile_json = updated.model_dump(mode="json")
    record.updated_at = now
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


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
    """
    query = (
        select(UploadRecord)
        .where(UploadRecord.gate_status.in_(tuple(_OPEN_GATES)))
        .order_by(UploadRecord.created_at.asc())
    )
    if user_id is not None:
        query = query.where(UploadRecord.user_id == user_id)
    return list((await db.execute(query)).scalars().all())


def _undated_positions(data: MasterProfileData) -> int:
    """Count work entries missing a start date (FMEA JF-M-2.7)."""
    return sum(1 for w in data.work_experience if not (w.start_date and w.start_date.strip()))


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
      2. Optionally fetch JobAnalysis context for JD-aware extraction
      3. LLM extraction → MasterProfileData
      4. Merge with existing profile (or create first profile)
      5. Persist UploadRecord (file + cost metadata)
      6. Return CVUploadResponse with status, completeness, and conflicts
    """
    from applire.models.job import JobAnalysis
    from applire.services.cv_parser import extract_text

    # 1. Text extraction — each CV is analysed individually; never concatenated.
    #    Hard cap prevents token overflow on verbose files (LinkedIn PDFs can be
    #    30K+ chars due to endorsements/courses/recommendations).  We cut at the
    #    last newline before the limit so the LLM always receives complete lines.
    _MAX_CV_TEXT_CHARS = 25_000
    raw_text = await extract_text(file_bytes, filename, content_type, ocr_extractor)
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

    # 2. JD context (optional)
    job_analysis_dict: dict | None = None
    if job_id is not None:
        result = await db.execute(
            select(JobAnalysis).where(
                JobAnalysis.id == job_id,
                JobAnalysis.deleted_at.is_(None),
            )
        )
        job_record = result.scalar_one_or_none()
        if job_record:
            job_analysis_dict = {
                "role_title": job_record.role_title,
                "required_skills": job_record.required_skills,
                "nice_to_have_skills": job_record.nice_to_have_skills,
                "keywords": job_record.keywords,
                "seniority_level": job_record.seniority_level,
                "language_requirement": job_record.language_requirement,
            }

    # 3. LLM extraction + review layer + skill enrichment
    if job_analysis_dict:
        prompt = build_jd_aware_prompt(raw_text, job_analysis_dict)
        system = JD_AWARE_CV_EXTRACTION_PROMPT
    else:
        prompt = build_generic_prompt(raw_text)
        system = GENERIC_CV_EXTRACTION_PROMPT

    # Cap-safe extraction (ADR-047 / US195): segmented fallback when the single call would
    # truncate, so the /upload path never silently drops a dense CV either.
    data: dict = await extract_with_fallback(
        raw_text, provider, system=system, user_prompt=prompt,
    )
    data = await review_and_refine(
        source=raw_text,
        draft=data,
        generator_prompt_fn=_build_cv_extraction_retry_prompt,
        generator_system=CV_EXTRACTION_REFINEMENT_PROMPT,
        reviewer_prompt_fn=_build_cv_extraction_review_prompt,
        reviewer_system=_CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=LLM_REVIEW_MAX_RETRIES,
        generator_max_tokens=CV_EXTRACTION_MAX_TOKENS,
        chain_id="cv_extraction",
    )
    # US179 / ADR-041: annotate role-aware expected fields at write time (same as
    # _import_from_text). The primary /upload path must annotate too, or expected_fields
    # stays null and the completeness model can't be role-aware (#66 PQ finding).
    await annotate_expected_fields(data, provider)
    incoming = MasterProfileData.model_validate(data)
    incoming = await enrich_skills(incoming, provider)
    now = datetime.now(timezone.utc)
    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER

    # Upload-time input-plausibility signals (Input Integrity sprint, issue #43):
    # document-type (US154/2.3) and per-CV completeness (US157/2.7) come from the
    # just-extracted CV.
    looks_like_cv = _looks_like_cv(incoming)
    undated_positions = _undated_positions(incoming)

    # 4. Pre-merge integrity gate (US167 / ADR-041 amended) — HOLD before commit.
    #    not-a-CV and account-vs-CV name divergence are caught *before* the additive
    #    merge can overwrite anything; safe default = don't merge. A held merge parks
    #    the staged extraction for the user to resolve (merge / discard).
    existing = await _get_latest(db)
    account_name = (
        MasterProfileData.model_validate(existing.profile_json).personal_info.name
        if existing
        else None
    )
    gate = evaluate_merge_gate(account_name, incoming)
    if gate.gate != "none":
        return await _park_gated_upload(
            db,
            gate,
            incoming,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            storage=storage,
            user_id=user_id,
            provider=provider,
        )

    # 5. Clean CV — additive merge commits (or first profile is created).
    profile_id, completeness, conflicts, enrichment_id = await _apply_merge(
        db, incoming, source="cv_upload", emb_provider=emb_provider, provider=provider, now=now
    )

    # 6. Persist file + cost metadata
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    file_path = await storage.save(file_bytes, filename)

    upload_record = UploadRecord(
        user_id=user_id,
        original_filename=filename,
        content_hash=content_hash,
        mime_type=content_type,
        file_path=file_path,
        byte_size=len(file_bytes),
        llm_tokens_used=None,  # token tracking deferred — LLMProvider ABC not extended yet
        llm_provider=provider.__class__.__name__,
    )
    db.add(upload_record)
    await db.commit()

    # 7. Build response
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
        profile_id=profile_id,
        status=status,
        completeness_score=completeness,
        conflicts=conflict_summaries,
        enrichment_record_id=enrichment_id,
        expires_at=upload_record.expires_at,
        looks_like_cv=looks_like_cv,
        name_mismatch=False,  # a clean merge by definition had no name divergence
        undated_positions=undated_positions,
        gate="none",
    )


async def _apply_merge(
    db: AsyncSession,
    incoming: MasterProfileData,
    *,
    source: str,
    emb_provider: EmbeddingProvider,
    provider: LLMProvider,
    now: datetime | None = None,
) -> tuple[uuid.UUID, float, list, uuid.UUID]:
    """Additively merge ``incoming`` into the latest profile (or create the first),
    commit, and return ``(profile_id, completeness, conflicts, enrichment_id)``.

    The pre-merge gate (US167) is the caller's responsibility — by the time this
    runs the merge is authorised (clean upload, or a user-resolved staged merge).
    """
    now = now or datetime.now(timezone.utc)
    enrichment_id = uuid.uuid4()
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
        return (
            existing.id,
            committed.completeness,
            merge_result.conflicts,
            uuid.UUID(committed.enrichment_record.id),
        )

    # First upload — create the profile
    enrichment = _make_enrichment_record(
        source=source, action="added", new_value="initial import"
    )
    incoming.metadata = ProfileMetadata(
        completeness_score=incoming.calculate_completeness(),
        created_via=source,
        created_at=now,
        last_updated=now,
        enrichment_history=[enrichment],
    )
    profile_json = incoming.model_dump(mode="json")
    embedding = await _compute_embedding(profile_json, emb_provider)
    record = MasterProfile(profile_json=profile_json, embedding=embedding)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record.id, incoming.calculate_completeness(), [], enrichment_id


async def _park_gated_upload(
    db: AsyncSession,
    gate,
    incoming: MasterProfileData,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    storage,
    user_id: uuid.UUID | None,
    provider: LLMProvider,
) -> CVUploadResponse:
    """HOLD the merge (US167): persist the source file, park the already-extracted
    profile JSON on the upload row, and return a GATED response. Nothing is merged
    — the user resolves it via ``resolve_staged_extraction``."""
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    file_path = await storage.save(file_bytes, filename)

    upload_record = UploadRecord(
        user_id=user_id,
        original_filename=filename,
        content_hash=content_hash,
        mime_type=content_type,
        file_path=file_path,
        byte_size=len(file_bytes),
        llm_provider=provider.__class__.__name__,
        gate_status=gate.gate,
        staged_extraction=incoming.model_dump(mode="json"),
    )
    db.add(upload_record)
    await db.commit()
    await db.refresh(upload_record)

    return CVUploadResponse(
        profile_id=None,
        status="GATED",
        completeness_score=0.0,
        conflicts=[],
        enrichment_record_id=None,
        expires_at=upload_record.expires_at,
        looks_like_cv=(gate.gate != "not_a_cv"),
        name_mismatch=(gate.gate == "name_divergence"),
        undated_positions=_undated_positions(incoming),
        gate=gate.gate,
        account_name=gate.account_name,
        cv_name=gate.cv_name,
        staged_id=upload_record.id,
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
    can only be resolved by the account that uploaded it.
    """
    query = select(UploadRecord).where(UploadRecord.id == staged_id)
    if user_id is not None:
        query = query.where(UploadRecord.user_id == user_id)
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
        profile_id, completeness, conflicts, _ = await _apply_merge(
            db, incoming, source="cv_upload", emb_provider=emb_provider, provider=provider
        )
        rec.gate_status = "resolved_merged"
        await db.commit()
        return StagedResolveResponse(
            staged_id=staged_id,
            action="merge",
            profile_id=profile_id,
            completeness_score=completeness,
            conflicts=[
                ConflictSummary(
                    conflict_id=c.conflict_id,
                    section=c.section,
                    field=c.field,
                    source=c.source,
                )
                for c in conflicts
            ],
        )

    raise ValueError(f"unknown resolve action: {action!r}")
