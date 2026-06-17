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

from applire.constants import LLM_REVIEW_MAX_RETRIES
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
from applire.services.profile.merge import merge_profiles
from applire.services.reviewer import review_and_refine
from applire.services.skill_enrichment import enrich_skills
from applire.schemas.profile import (
    ConflictSummary,
    CVUploadResponse,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    MasterProfileResponse,
    ProfileChangesResponse,
    ProfileMetadata,
)

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


_VALID_SECTIONS = {
    "personal_info",
    "professional_summary",
    "work_experience",
    "education",
    "certifications",
    "skills",
    "languages",
    "publications",
    "volunteer_activities",
}


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
    data: dict = await provider.aparse_json(
        build_user_prompt(raw_text),
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=8192,
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
        generator_max_tokens=8192,
        chain_id="profile_extraction",
    )
    incoming = MasterProfileData.model_validate(data)
    incoming = await enrich_skills(incoming, provider)
    now = datetime.now(timezone.utc)

    existing = await _get_latest(db)
    if existing:
        existing_data = MasterProfileData.model_validate(existing.profile_json)
        merge_result = merge_profiles(existing_data, incoming, source=created_via)

        merged = merge_result.merged_profile
        enrichment = _enrichment_from_merge(merge_result, source=created_via)

        if merged.metadata is None:
            merged.metadata = ProfileMetadata(
                completeness_score=merged.calculate_completeness(),
                created_via=created_via,
                created_at=existing.created_at,
                last_updated=now,
                enrichment_history=[enrichment],
                pending_conflicts=merge_result.conflicts,
            )
        else:
            merged.metadata.completeness_score = merged.calculate_completeness()
            merged.metadata.last_updated = now
            merged.metadata.enrichment_history.append(enrichment)
            # Replace pending conflicts with latest round (user resolves via endpoint)
            merged.metadata.pending_conflicts = merge_result.conflicts

        merged_json = merged.model_dump(mode="json")
        existing.profile_json = merged_json
        existing.embedding = await _compute_embedding(merged_json, emb_provider)
        existing.updated_at = now
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
    if section not in _VALID_SECTIONS:
        raise ValueError(f"Invalid section '{section}'. Valid: {sorted(_VALID_SECTIONS)}")

    record = await _get_latest(db)
    if not record:
        raise LookupError("No profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)
    old_value = profile_data.model_dump(mode="json").get(section)

    # Apply section update
    # Decode JSON strings — guards against frontend sending double-encoded data
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    updated_dict = profile_data.model_dump(mode="json")
    updated_dict[section] = value
    validated = MasterProfileData.model_validate(updated_dict)

    # Re-run enrichment when skills or work_experience are patched (keeps years fresh)
    if section in {"work_experience", "skills"} and provider is not None:
        validated = await enrich_skills(validated, provider)

    # Build enrichment record
    action = "updated" if old_value else "added"
    enrichment = _make_enrichment_record(
        source=source,
        section=section,
        action=action,
        old_value=old_value,
        new_value=value,
        session_id=source_session_id,
    )

    now = datetime.now(timezone.utc)
    if validated.metadata is None:
        validated.metadata = ProfileMetadata(
            completeness_score=validated.calculate_completeness(),
            created_via="manual",
            created_at=record.created_at,
            last_updated=now,
            enrichment_history=[enrichment],
        )
    else:
        validated.metadata.completeness_score = validated.calculate_completeness()
        validated.metadata.last_updated = now
        validated.metadata.enrichment_history.append(enrichment)

    record.profile_json = validated.model_dump(mode="json")
    record.updated_at = now
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


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

    # Apply the chosen value to the profile (only for non-list sections with a single field)
    section = conflict.section
    field_name = conflict.field
    profile_dict = profile_data.model_dump(mode="json")

    section_data = profile_dict.get(section)
    if isinstance(section_data, dict):
        section_data[field_name] = chosen
        profile_dict[section] = section_data
    # For list-type sections (work_experience etc.) date conflicts reference a specific entry;
    # we update the first matching entry. Caller may PATCH the section directly for complex edits.
    elif isinstance(section_data, list) and section == "work_experience":
        # Best effort: update the first entry that still has the old value on that field
        for entry in section_data:
            if entry.get(field_name) == conflict.existing_value:
                entry[field_name] = chosen
                break
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


# US154 (document-type) and US155 (name-mismatch) detection live in the canonical
# merge_gate module (US167 / ADR-041 amended) — re-exported here so the upload-time
# warning path and the existing tests keep importing them from this package.
from applire.services.profile.merge_gate import (  # noqa: E402
    looks_like_cv as _looks_like_cv,
    names_clearly_differ as _names_clearly_differ,
)


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

    data: dict = await provider.aparse_json(prompt, system=system, temperature=0.1, max_tokens=8192)
    data = await review_and_refine(
        source=raw_text,
        draft=data,
        generator_prompt_fn=_build_cv_extraction_retry_prompt,
        generator_system=CV_EXTRACTION_REFINEMENT_PROMPT,
        reviewer_prompt_fn=_build_cv_extraction_review_prompt,
        reviewer_system=_CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=LLM_REVIEW_MAX_RETRIES,
        generator_max_tokens=8192,
        chain_id="cv_extraction",
    )
    incoming = MasterProfileData.model_validate(data)
    incoming = await enrich_skills(incoming, provider)
    now = datetime.now(timezone.utc)
    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER

    # Upload-time input-plausibility signals (Input Integrity sprint, issue #43):
    # document-type (US154/2.3) and per-CV completeness (US157/2.7) come from the
    # just-extracted CV; the name-mismatch check (US155/2.4) needs an existing name.
    looks_like_cv = _looks_like_cv(incoming)
    undated_positions = _undated_positions(incoming)
    name_mismatch = False

    # 4. Merge with existing profile (or create first profile)
    enrichment_id = uuid.uuid4()
    existing = await _get_latest(db)

    if existing:
        existing_data = MasterProfileData.model_validate(existing.profile_json)
        name_mismatch = _names_clearly_differ(
            existing_data.personal_info.name, incoming.personal_info.name
        )
        merge_result = merge_profiles(existing_data, incoming, source="cv_upload")
        merged = merge_result.merged_profile

        enrichment = _enrichment_from_merge(merge_result, source="cv_upload")
        # Patch enrichment_id onto the record for traceability
        enrichment_id = uuid.uuid4()

        if merged.metadata is None:
            merged.metadata = ProfileMetadata(
                completeness_score=merged.calculate_completeness(),
                created_via="cv_upload",
                created_at=existing.created_at,
                last_updated=now,
                enrichment_history=[enrichment],
                pending_conflicts=merge_result.conflicts,
            )
        else:
            merged.metadata.completeness_score = merged.calculate_completeness()
            merged.metadata.last_updated = now
            merged.metadata.enrichment_history.append(enrichment)
            merged.metadata.pending_conflicts = merge_result.conflicts

        merged_json = merged.model_dump(mode="json")
        existing.profile_json = merged_json
        existing.embedding = await _compute_embedding(merged_json, emb_provider)
        existing.updated_at = now
        await db.commit()
        await db.refresh(existing)

        profile_id = existing.id
        completeness = merged.calculate_completeness()
        conflicts = merge_result.conflicts
    else:
        # First upload — create the profile
        enrichment = _make_enrichment_record(
            source="cv_upload",
            action="added",
            new_value="initial import",
        )
        incoming.metadata = ProfileMetadata(
            completeness_score=incoming.calculate_completeness(),
            created_via="cv_upload",
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

        profile_id = record.id
        completeness = incoming.calculate_completeness()
        conflicts = []

    # 5. Persist file + cost metadata
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

    # 6. Build response
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
        name_mismatch=name_mismatch,
        undated_positions=undated_positions,
    )
