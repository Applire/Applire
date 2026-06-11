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

# backend/applire/services/cv_section_editor.py
"""CV Section Editor service (Sprint 9, ADR-019).

Responsibilities:
- build_content_snapshot: extract structured snapshot from TailoredCVData at generation time
- get_cv_sections: return merged snapshot+overrides+gap hints for GET /api/cv/{id}/sections
- patch_cv_section: write override, re-render, optionally save to profile and auto-resolve gaps
- apply_overrides_to_tailored: merge section_overrides on top of TailoredCVData (used by get_cv_html)
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.profile import MasterProfile
from applire.schemas.cv import TailoredCVData
from applire.schemas.cv_sections import (
    ContentSnapshot,
    CVSectionsResponse,
    GapHintItem,
    SectionItem,
    SectionPatchResponse,
    SnapshotPosition,
)
from applire.services.cv_gap_mapper import map_gaps_to_sections


# ---------------------------------------------------------------------------
# Snapshot extraction
# ---------------------------------------------------------------------------


def build_content_snapshot(tailored: TailoredCVData) -> dict:
    """Extract a structured snapshot dict from TailoredCVData.

    Called once at generation time. ~5ms, no LLM.
    Returns a plain dict (stored as JSONB).
    """
    positions = []
    for idx, entry in enumerate(tailored.work_history):
        period = entry.start_date
        if entry.end_date:
            period = f"{entry.start_date} – {entry.end_date}"
        positions.append(
            SnapshotPosition(
                id=str(uuid.uuid4()),
                index=idx,
                title=entry.role,
                company=entry.company,
                period=period,
                bullets=list(entry.bullets),
            ).model_dump()
        )

    snapshot = ContentSnapshot(
        introduction=tailored.summary,
        positions=positions,
        skills=list(tailored.skills),
    )
    return snapshot.model_dump()


# ---------------------------------------------------------------------------
# Override application (used by get_cv_html)
# ---------------------------------------------------------------------------


def apply_overrides_to_tailored(
    tailored: TailoredCVData,
    content_snapshot: dict | None,
    section_overrides: dict | None,
) -> TailoredCVData:
    """Return a new TailoredCVData with section_overrides applied.

    If section_overrides is None or empty, returns tailored unchanged (byte-identical render).
    """
    if not section_overrides:
        return tailored

    # Deep-copy so we don't mutate the original
    data = tailored.model_dump()

    for section_id, content in section_overrides.items():
        if section_id == "introduction":
            data["summary"] = content

        elif section_id == "skills":
            data["skills"] = [s.strip() for s in content.split("\n") if s.strip()]

        elif section_id.startswith("position::") and content_snapshot:
            position_uuid = section_id[len("position::"):]
            # Find the position's work_history index from the snapshot
            snapshot_positions = content_snapshot.get("positions", [])
            for snap_pos in snapshot_positions:
                if snap_pos.get("id") == position_uuid:
                    idx = snap_pos.get("index", -1)
                    if 0 <= idx < len(data.get("work_history", [])):
                        data["work_history"][idx]["bullets"] = [
                            b.strip() for b in content.split("\n") if b.strip()
                        ]
                    break

    return TailoredCVData.model_validate(data)


# ---------------------------------------------------------------------------
# GET /api/cv/{id}/sections
# ---------------------------------------------------------------------------


async def get_cv_sections(cv_id: uuid.UUID, db: AsyncSession) -> CVSectionsResponse:
    """Load sections + overrides + gap hints for a CV.

    Returns empty sections list when content_snapshot is NULL.
    Returns 404 if CV not found.
    """
    record = await _load_cv(cv_id, db)

    # NULL snapshot — CV was generated before this sprint
    if record.content_snapshot is None:
        return CVSectionsResponse(sections=[], general_gaps=[])

    snapshot = ContentSnapshot.model_validate(record.content_snapshot)
    overrides: dict = record.section_overrides or {}

    # Build section content map for gap mapping
    section_contents: dict[str, str] = {
        "introduction": overrides.get("introduction", snapshot.introduction),
        "skills": overrides.get("skills", "\n".join(snapshot.skills)),
    }
    for pos in snapshot.positions:
        sid = f"position::{pos.id}"
        section_contents[sid] = overrides.get(sid, "\n".join(pos.bullets))

    # Load gap analysis via FlowSession
    gap_map: dict[str, list[str]] = {}
    general_gaps: list[str] = []

    flow_result = await db.execute(
        select(FlowSession)
        .where(
            FlowSession.generated_cv_id == cv_id,
            FlowSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()

    if flow and flow.gap_analysis_id:
        gap_analysis = await db.get(GapAnalysis, flow.gap_analysis_id)
        if gap_analysis:
            all_gaps: list[str] = (
                list(gap_analysis.category_b) + list(gap_analysis.category_c)
            )
            raw_map = map_gaps_to_sections(all_gaps, section_contents)
            gap_map = {k: v for k, v in raw_map.items() if k != "__general__"}
            general_gaps = raw_map.get("__general__", [])

    # Build section items
    sections: list[SectionItem] = []

    # Introduction
    intro_content = overrides.get("introduction", snapshot.introduction)
    sections.append(
        SectionItem(
            section_id="introduction",
            label="Introduction",
            content=intro_content,
            has_override="introduction" in overrides,
            gaps=[
                GapHintItem(id=g, label=g)
                for g in gap_map.get("introduction", [])
            ],
        )
    )

    # Positions
    for pos in snapshot.positions:
        sid = f"position::{pos.id}"
        pos_content = overrides.get(sid, "\n".join(pos.bullets))
        label = f"{pos.title} — {pos.company}"
        sections.append(
            SectionItem(
                section_id=sid,
                label=label,
                content=pos_content,
                has_override=sid in overrides,
                gaps=[GapHintItem(id=g, label=g) for g in gap_map.get(sid, [])],
            )
        )

    # Skills
    skills_content = overrides.get("skills", "\n".join(snapshot.skills))
    sections.append(
        SectionItem(
            section_id="skills",
            label="Skills",
            content=skills_content,
            has_override="skills" in overrides,
            gaps=[GapHintItem(id=g, label=g) for g in gap_map.get("skills", [])],
        )
    )

    return CVSectionsResponse(
        sections=sections,
        general_gaps=[GapHintItem(id=g, label=g) for g in general_gaps],
    )


# ---------------------------------------------------------------------------
# PATCH /api/cv/{id}/sections/{section_id}
# ---------------------------------------------------------------------------

_VALID_STATIC_SECTION_IDS = {"introduction", "skills"}


async def patch_cv_section(
    cv_id: uuid.UUID,
    section_id: str,
    content: str,
    save_to_profile: bool,
    db: AsyncSession,
) -> SectionPatchResponse:
    """Write a section override and re-render the CV HTML.

    Validates section_id against snapshot. Optionally saves to profile.
    Auto-resolves gaps whose keywords are now present in the new content.
    Returns updated HTML, list of all applied overrides, and resolved gap IDs.
    """
    from applire.services.cv import _jinja_env, _TEMPLATE_FILES

    record = await _load_cv(cv_id, db)

    # Validate section_id
    valid_position_ids: set[str] = set()
    if record.content_snapshot:
        for pos in record.content_snapshot.get("positions", []):
            valid_position_ids.add(f"position::{pos['id']}")

    if section_id not in _VALID_STATIC_SECTION_IDS and section_id not in valid_position_ids:
        raise ValueError(f"Unknown section_id: {section_id!r}")

    # Write override
    overrides = dict(record.section_overrides or {})
    overrides[section_id] = content
    record.section_overrides = overrides
    await db.commit()
    await db.refresh(record)

    # Optional profile save
    if save_to_profile:
        await _save_section_to_profile(cv_id, section_id, content, record, db)

    # Gap auto-resolve: check which gaps now have keyword overlap with the new content
    resolved_gaps = await _resolve_gaps(cv_id, section_id, content, db)

    # Jinja2 re-render with overrides applied
    from applire.services.color_detection import resolve_color_context
    tailored = TailoredCVData.model_validate(record.tailored_data)
    tailored_with_overrides = apply_overrides_to_tailored(
        tailored, record.content_snapshot, overrides
    )
    color_ctx = await resolve_color_context(record, db)
    template_file = _TEMPLATE_FILES.get(record.template, "lebenslauf.html.j2")
    template = _jinja_env.get_template(template_file)
    html = template.render(cv=tailored_with_overrides, color=color_ctx)

    # ADR-039: recompute ATS report after section edit (function-level import to avoid circular)
    from applire.services.cv import _update_ats_report
    await _update_ats_report(record, db)

    return SectionPatchResponse(
        html=html,
        overrides_applied=list(overrides.keys()),
        resolved_gaps=resolved_gaps,
    )


async def _save_section_to_profile(
    cv_id: uuid.UUID,
    section_id: str,
    content: str,
    record: GeneratedCV,
    db: AsyncSession,
) -> None:
    """Merge the edited section content into the Master Profile (ADR-013).

    introduction: replaces professional_summary.de (user intent is replacement here)
    skills: additive — only appends skills not already present
    position::{uuid}: replaces responsibilities on the first matching work_experience entry
    """
    from applire.schemas.profile import MasterProfileData

    profile = await db.get(MasterProfile, record.profile_id)
    if profile is None:
        return

    profile_data = MasterProfileData.model_validate(profile.profile_json)

    if section_id == "introduction":
        profile_data.professional_summary.de = content

    elif section_id == "skills":
        new_skills_raw = [s.strip() for s in content.split("\n") if s.strip()]
        existing = {s.name.lower() for s in (profile_data.skills or [])}
        from applire.schemas.profile import Skill
        for skill_name in new_skills_raw:
            if skill_name.lower() not in existing:
                profile_data.skills = list(profile_data.skills or []) + [
                    Skill(name=skill_name)
                ]

    elif section_id.startswith("position::") and record.content_snapshot:
        position_uuid = section_id[len("position::"):]
        snapshot_positions = record.content_snapshot.get("positions", [])
        snap_pos = next(
            (p for p in snapshot_positions if p.get("id") == position_uuid), None
        )
        if snap_pos and profile_data.work_experience:
            new_bullets = [b.strip() for b in content.split("\n") if b.strip()]
            for entry in profile_data.work_experience:
                if entry.company.lower() == snap_pos.get("company", "").lower():
                    entry.responsibilities = new_bullets
                    break

    profile.profile_json = profile_data.model_dump()
    await db.commit()


async def _resolve_gaps(
    cv_id: uuid.UUID,
    section_id: str,
    new_content: str,
    db: AsyncSession,
) -> list[str]:
    """Return gap IDs whose keywords are now present in new_content.

    Also removes resolved gaps from gap_analysis.category_b / category_c.
    Returns empty list if no gap_analysis linked to this CV.
    """
    flow_result = await db.execute(
        select(FlowSession)
        .where(
            FlowSession.generated_cv_id == cv_id,
            FlowSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    if not flow or not flow.gap_analysis_id:
        return []

    gap_analysis = await db.get(GapAnalysis, flow.gap_analysis_id)
    if not gap_analysis:
        return []

    all_gaps: list[str] = list(gap_analysis.category_b) + list(gap_analysis.category_c)
    if not all_gaps:
        return []

    # Check which gaps have keyword overlap with the new content
    mapping = map_gaps_to_sections(all_gaps, {section_id: new_content})
    resolved: list[str] = mapping.get(section_id, [])

    if resolved:
        resolved_set = set(resolved)
        gap_analysis.category_b = [g for g in gap_analysis.category_b if g not in resolved_set]
        gap_analysis.category_c = [g for g in gap_analysis.category_c if g not in resolved_set]
        await db.commit()

    return resolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_cv(cv_id: uuid.UUID, db: AsyncSession) -> GeneratedCV:
    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == cv_id,
            GeneratedCV.deleted_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Generated CV {cv_id} not found")
    return record
