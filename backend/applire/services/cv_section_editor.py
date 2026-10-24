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
- patch_cv_section: write override, re-render, optionally save to profile; reports
  which gap hints the edit covered (read-only — the gap analysis is never mutated, #117)
- apply_overrides_to_tailored: merge section_overrides on top of TailoredCVData (used by get_cv_html)
"""
import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.services.owner_resolution import owned_cv, resolve_owner
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
from applire.services.cv_gap_hints import build_gap_hints, resolved_gap_hints

if TYPE_CHECKING:  # pragma: no cover — annotation only; the runtime import is local
    from applire.schemas.profile import MasterProfileData


# ---------------------------------------------------------------------------
# L-3 = A (789:193, founder 2026-10-24): certification / education entries as
# editor sections. A red entry finding had no section to act on ("take out" and
# "edit myself" only reached introduction, positions and skills). Each entry is
# ONE line of positional fields joined by ``ENTRY_SEP``; the editor shows the
# fields as single-line inputs, never a free-text area. An empty override takes
# the entry off THIS CV (the vault keeps it).
# ---------------------------------------------------------------------------

ENTRY_SEP = " · "
ENTRY_FIELDS: dict[str, tuple[str, ...]] = {
    "certification": ("name", "issuing_organization", "date_obtained", "expiry_date"),
    "education": ("degree", "field", "institution", "start_date", "end_date"),
}
_ENTRY_KEYS = {"certification": "certifications", "education": "education"}


def entry_line(kind: str, entry: object, *, user_id: uuid.UUID | None = None) -> str:
    """One entry as its section text: the fields in ``ENTRY_FIELDS`` order,
    trailing empty fields dropped. (``user_id``: ADR-092 signature contract; pure.)"""
    get = entry.get if isinstance(entry, dict) else (lambda f, _d=None: getattr(entry, f, None))
    vals = [str(get(f) or "").strip() for f in ENTRY_FIELDS[kind]]
    while vals and not vals[-1]:
        vals.pop()
    return ENTRY_SEP.join(vals)


def parse_entry_line(kind: str, line: str, *, user_id: uuid.UUID | None = None) -> dict | None:
    """The fields of one entry line; ``None`` for an empty line (= take it out)."""
    text = (line or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(ENTRY_SEP.strip())]
    fields = ENTRY_FIELDS[kind]
    if len(parts) > len(fields):  # a separator typed inside the last field
        parts = parts[: len(fields) - 1] + [" · ".join(parts[len(fields) - 1:])]
    parts += [""] * (len(fields) - len(parts))
    return dict(zip(fields, parts))


def entry_section_kind(section_id: str, *, user_id: uuid.UUID | None = None) -> tuple[str, int] | None:
    """``("certification", 2)`` for ``certification::2``; ``None`` otherwise."""
    kind, sep, idx = section_id.partition("::")
    if not sep or kind not in ENTRY_FIELDS or not idx.isdigit():
        return None
    return kind, int(idx)


def snapshot_entry_lines(
    content_snapshot: dict | None, tailored_data: dict | None, *, user_id: uuid.UUID | None = None
) -> dict[str, list[str]]:
    """Per entry kind, the generation-time lines: the snapshot's own, or — for a
    snapshot stored before L-3 (``None``) — rebuilt lazily from tailored_data."""
    out: dict[str, list[str]] = {}
    for kind, key in _ENTRY_KEYS.items():
        stored = (content_snapshot or {}).get(key)
        if isinstance(stored, list):
            out[kind] = [str(x) for x in stored]
        else:
            out[kind] = [entry_line(kind, e) for e in ((tailored_data or {}).get(key) or [])]
    return out


# ---------------------------------------------------------------------------
# Snapshot extraction
# ---------------------------------------------------------------------------


def build_content_snapshot(tailored: TailoredCVData, *, user_id: uuid.UUID | None = None) -> dict:
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
                # #336 — carry the profile WorkEntry.id (structural on every
                # tailored entry since cv.assemble_tailored_cv, E049/ADR-067)
                # so the profile write-back can target an entity instead of
                # guessing by company name.
                work_id=entry.id or None,
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
        certifications=[entry_line("certification", c) for c in tailored.certifications],
        education=[entry_line("education", e) for e in tailored.education],
    )
    return snapshot.model_dump()


# ---------------------------------------------------------------------------
# Override application (used by get_cv_html)
# ---------------------------------------------------------------------------


def apply_overrides_to_tailored(
    tailored: TailoredCVData,
    content_snapshot: dict | None,
    section_overrides: dict | None,
    *,
    user_id: uuid.UUID | None = None,
) -> TailoredCVData:
    """Return a new TailoredCVData with section_overrides applied.

    If section_overrides is None or empty, returns tailored unchanged (byte-identical render).
    """
    if not section_overrides:
        return tailored

    # Deep-copy so we don't mutate the original
    data = tailored.model_dump()
    removed: dict[str, set[int]] = {}

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

        elif (ek := entry_section_kind(section_id)) is not None:
            kind, idx = ek
            entries = data.get(_ENTRY_KEYS[kind]) or []
            if 0 <= idx < len(entries):
                fields = parse_entry_line(kind, content)
                if fields is None:
                    removed.setdefault(kind, set()).add(idx)
                else:
                    if kind == "education" and not fields.get("end_date"):
                        fields["end_date"] = None
                    entries[idx].update(fields)

    # L-3: entries taken out leave after every edit landed (indices stay valid).
    for kind, idxs in removed.items():
        key = _ENTRY_KEYS[kind]
        data[key] = [e for i, e in enumerate(data.get(key) or []) if i not in idxs]

    return TailoredCVData.model_validate(data)


# ---------------------------------------------------------------------------
# GET /api/cv/{id}/sections
# ---------------------------------------------------------------------------


async def get_cv_sections(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> CVSectionsResponse:
    """Load sections + overrides + gap hints for a CV.

    Returns empty sections list when content_snapshot is NULL.
    Returns 404 if CV not found.
    """
    owner = resolve_owner(user_id, site="cv_section_editor.get_cv_sections")
    record = await _load_cv(cv_id, db, owner)

    # NULL snapshot — CV was generated before this sprint
    if record.content_snapshot is None:
        return CVSectionsResponse(sections=[], general_gaps=[])

    snapshot = ContentSnapshot.model_validate(record.content_snapshot)
    overrides: dict = record.section_overrides or {}

    entry_lines = snapshot_entry_lines(record.content_snapshot, record.tailored_data)
    section_contents = _section_contents(snapshot, overrides, entry_lines)

    # Gap hints = ledger entry × live document coverage (ADR-019 amended, #117).
    # Derived fresh per request; covered entries are hidden, nothing persisted.
    gap_map: dict[str, list[GapHintItem]] = {}
    general_gaps: list[GapHintItem] = []

    gap_analysis = await _load_gap_analysis(cv_id, db, owner)
    if gap_analysis:
        gap_map, general_gaps = build_gap_hints(
            ledger=gap_analysis.keyword_ledger,
            category_b=list(gap_analysis.category_b or []),
            category_c=list(gap_analysis.category_c or []),
            section_contents=section_contents,
            # #111: collapse near-duplicate concepts the clusters already group.
            gap_clusters=list(gap_analysis.gap_clusters or []),
        )

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
            gaps=gap_map.get("introduction", []),
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
                gaps=gap_map.get(sid, []),
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
            gaps=gap_map.get("skills", []),
        )
    )

    # L-3: one section per certification / education entry (label = its name).
    for kind, lines in entry_lines.items():
        for i, line in enumerate(lines):
            sid = f"{kind}::{i}"
            sections.append(
                SectionItem(
                    section_id=sid,
                    label=line.split(ENTRY_SEP)[0] or sid,
                    content=overrides.get(sid, line),
                    has_override=sid in overrides,
                    gaps=gap_map.get(sid, []),
                )
            )

    return CVSectionsResponse(
        sections=sections,
        general_gaps=general_gaps,
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
    background_tasks: BackgroundTasks | None = None,
    *,
    user_id: uuid.UUID | None = None,
) -> SectionPatchResponse:
    """Write a section override and re-render the CV HTML.

    Validates section_id against snapshot. Optionally saves to profile.
    Auto-resolves gaps whose keywords are now present in the new content.
    Returns updated HTML, list of all applied overrides, and resolved gap IDs.
    """
    from applire.services.cv import _jinja_env, _TEMPLATE_FILES

    owner = resolve_owner(user_id, site="cv_section_editor.patch_cv_section")
    record = await _load_cv(cv_id, db, owner)

    # Validate section_id
    valid_position_ids: set[str] = set()
    if record.content_snapshot:
        for pos in record.content_snapshot.get("positions", []):
            valid_position_ids.add(f"position::{pos['id']}")

    entry_lines = snapshot_entry_lines(record.content_snapshot, record.tailored_data)
    ek = entry_section_kind(section_id)
    valid_entry = ek is not None and record.content_snapshot is not None and ek[1] < len(entry_lines[ek[0]])
    if (
        section_id not in _VALID_STATIC_SECTION_IDS
        and section_id not in valid_position_ids
        and not valid_entry
    ):
        raise ValueError(f"Unknown section_id: {section_id!r}")

    # Snapshot the pre-edit section contents (for the resolved-hints diff below)
    snapshot_before = ContentSnapshot.model_validate(record.content_snapshot)
    contents_before = _section_contents(
        snapshot_before, dict(record.section_overrides or {}), entry_lines
    )

    # Write override
    overrides = dict(record.section_overrides or {})
    overrides[section_id] = content
    record.section_overrides = overrides
    await db.commit()
    await db.refresh(record)

    # Optional profile save
    if save_to_profile:
        await _save_section_to_profile(cv_id, section_id, content, record, db, owner)

    # Which hints did this edit just cover? Purely informational (#117): the UI
    # drops the chips; the gap analysis itself is NEVER mutated — the evidence
    # axis only moves via profile enrichment (ADR-048 two-axis model).
    contents_after = dict(contents_before)
    contents_after[section_id] = content
    resolved_gaps: list[str] = []
    gap_analysis = await _load_gap_analysis(cv_id, db, owner)
    if gap_analysis:
        resolved_gaps = resolved_gap_hints(
            ledger=gap_analysis.keyword_ledger,
            category_b=list(gap_analysis.category_b or []),
            category_c=list(gap_analysis.category_c or []),
            contents_before=contents_before,
            contents_after=contents_after,
        )

    # Jinja2 re-render with overrides applied
    from applire.services.color_detection import resolve_color_context
    tailored = TailoredCVData.model_validate(record.tailored_data)
    tailored_with_overrides = apply_overrides_to_tailored(
        tailored, record.content_snapshot, overrides
    )
    # #312: the live-preview render is the second (and only other) place a user's
    # CV reaches a template — the same orphan-heading guard applies here as in
    # cv.get_cv_html, from the same implementation (ADR-066).
    from applire.services.cv import strip_empty_projects
    tailored_with_overrides = strip_empty_projects(tailored_with_overrides)
    color_ctx = await resolve_color_context(record, db, user_id=owner)
    template_file = _TEMPLATE_FILES.get(record.template, "lebenslauf.html.j2")
    template = _jinja_env.get_template(template_file)
    # #4 (ADR-038): section headings follow the document's output language (mirrors
    # cv.get_cv_html). E054 clause 3b: the record's PINNED language wins; NULL
    # pin (pre-migration row) falls back to the seam.
    from applire.templates.labels import cv_labels

    lang = record.document_language
    if not lang:
        from applire.models.job import JobAnalysis
        from applire.services.application import get_application_for_job
        from applire.utils.language_detection import resolve_document_language

        job = await db.get(JobAnalysis, record.job_analysis_id)
        application = await get_application_for_job(
            record.job_analysis_id, owner, db
        )
        lang = resolve_document_language(application, job) if job else "de"
    # #759 (ruling E5-2): same render-context step as cv.get_cv_html.
    from applire.services.cv import localize_languages

    tailored_with_overrides = localize_languages(tailored_with_overrides, lang)
    html = template.render(
        cv=tailored_with_overrides, color=color_ctx, lang=lang, labels=cv_labels(lang)
    )

    if background_tasks is not None:
        # ADR-039: re-audit off-thread; the report must never describe a stale document
        from applire.services.cv import _update_ats_report_by_id
        # ADR-092 cl. 14: the task receives its user explicitly.
        background_tasks.add_task(_update_ats_report_by_id, record.id, user_id=owner)

    return SectionPatchResponse(
        html=html,
        overrides_applied=list(overrides.keys()),
        resolved_gaps=resolved_gaps,
    )


def build_section_field_edit(
    section_id: str,
    content: str,
    *,
    profile_data: "MasterProfileData",
    content_snapshot: dict | None,
    lang: str,
    user_id: uuid.UUID | None = None,
    tailored_data: dict | None = None,
) -> tuple[str, object] | None:
    """Intake adapter (ADR-063 clause 3): a CV-section edit → one ``FieldEdit``.

    Pure — no database, no LLM, no async. Returns the ``(section, value)`` pair
    the shared FieldEdit intake writes, or ``None`` when the edit targets
    nothing in the vault (an unknown position, a profile with no work history).

    introduction → ``None`` (founder ruling D2-2 = A, 2026-10-20, #783 line
    L23): an edited TAILORED introduction stays on the document. Until then it
    was merge-patched into ``professional_summary.{lang}``, and that slot is
    role-agnostic Oracle evidence (``matchers/vault.py``) with no provenance —
    so text written for one posting became evidence that "backs" the next
    document. The candidate's own summary keeps reaching the vault through CV
    import, the profile page and the interview's ``set_summary`` on an empty
    slot.
    skills → the ``skills`` section, additive, absent names appended as
    ``unconfirmed`` (ADR-061 clause 3)
    position::{uuid} → the ``work_experience`` section with ``responsibilities``
    replaced on the entry the position was tailored from (by ``work_id``;
    snapshots taken before that field existed fall back to a company match)
    """
    from applire.schemas.profile import Skill

    if section_id == "introduction":
        # D2-2 = A (#783 L23): document only — see the docstring. (#336's
        # language-slot fix is moot here now; the slot is never written.)
        return None

    if section_id == "skills":
        new_skills_raw = [s.strip() for s in content.split("\n") if s.strip()]
        existing = {s.name.lower() for s in (profile_data.skills or [])}
        skills = list(profile_data.skills or [])
        for skill_name in new_skills_raw:
            if skill_name.lower() not in existing:
                # #336 — was a bare ``Skill(name=…)``, which takes the schema
                # default ``status="confirmed"``: a skill typed into a tailored
                # CV became fully claimable vault evidence with no testimony
                # behind it, so a claim the Oracle had just rejected could be
                # laundered into ground truth. ``unconfirmed`` is ADR-061
                # clause 3's third state — visible, candidate-confirmable, and
                # never claimable — which is exactly this write's standing.
                skills.append(Skill(name=skill_name, status="unconfirmed", source="transcribed"))
                existing.add(skill_name.lower())
        return "skills", [s.model_dump(mode="json") for s in skills]

    if section_id.startswith("position::") and content_snapshot:
        position_uuid = section_id[len("position::"):]
        snapshot_positions = content_snapshot.get("positions", [])
        snap_pos = next(
            (p for p in snapshot_positions if p.get("id") == position_uuid), None
        )
        if not snap_pos or not profile_data.work_experience:
            return None
        new_bullets = [b.strip() for b in content.split("\n") if b.strip()]
        # #336 — prefer the entity id carried from the tailored entry. The old
        # code matched the FIRST entry whose lowercased company matched, so two
        # roles at the same employer (a promotion, a re-hire) wrote the edited
        # bullets onto whichever came first. ``work_id`` is absent on snapshots
        # taken before it existed; those keep the legacy behaviour rather than
        # silently doing nothing.
        work_id = snap_pos.get("work_id")
        target = None
        if work_id:
            target = next(
                (e for e in profile_data.work_experience if str(e.id) == str(work_id)),
                None,
            )
        if target is None:
            company = snap_pos.get("company", "").lower()
            target = next(
                (e for e in profile_data.work_experience if e.company.lower() == company),
                None,
            )
        if target is None:
            return None
        entries = []
        for entry in profile_data.work_experience:
            dumped = entry.model_dump(mode="json")
            if entry is target:
                dumped["responsibilities"] = new_bullets
            entries.append(dumped)
        return "work_experience", entries

    ek = entry_section_kind(section_id)
    if ek is not None:
        # L-3 = A: an edited certification / education entry writes its fields
        # onto the vault entry it was rendered from — matched by its generation-
        # time line (certifications are a verbatim passthrough), else by its
        # normalised name. Taking an entry off the CV (empty) never deletes it
        # from the vault: the document is not the profile.
        kind, idx = ek
        fields = parse_entry_line(kind, content)
        if fields is None:
            return None
        lines = snapshot_entry_lines(content_snapshot, tailored_data)[kind]
        if idx >= len(lines):
            return None
        original = lines[idx]
        key = _ENTRY_KEYS[kind]
        vault = list(getattr(profile_data, key, None) or [])
        name_field = ENTRY_FIELDS[kind][0]

        def _n(x: object) -> str:
            return " ".join(str(x or "").casefold().split())

        target = next((e for e in vault if entry_line(kind, e) == original), None)
        if target is None:
            orig_name = _n(original.split(ENTRY_SEP)[0])
            target = next((e for e in vault if _n(getattr(e, name_field, "")) == orig_name), None)
        if target is None:
            return None
        out = []
        for e in vault:
            dumped = e.model_dump(mode="json")
            if e is target:
                for f, v in fields.items():
                    if f in dumped:
                        dumped[f] = v or None if f in ("end_date", "start_date", "expiry_date", "date_obtained") else v
            out.append(dumped)
        return key, out

    return None


async def _save_section_to_profile(
    cv_id: uuid.UUID,
    section_id: str,
    content: str,
    record: GeneratedCV,
    db: AsyncSession,
    owner: uuid.UUID,
) -> None:
    """Save the edited section into the Master Profile through the shared
    ``FieldEdit`` intake (ADR-063 clauses 2–3, ADR-066).

    This used to assign ``master_profiles.profile_json`` itself, which made the
    section editor a **second implementation** of an operation the profile page
    and the MCP door already share — and the copy carried none of the intake's
    invariants: no ``EnrichmentRecord`` (ADR-063 Context finding 2, one of the
    four trail-less writers), no completeness recompute, no ``last_updated``, no
    skill enrichment. The edit is now shaped by the pure adapter above and
    written by ``patch_profile_section``; ``source_session_id`` carries the CV so
    the receipt says where the edit came from.

    **The write IS guarded now** (#480 PRs 3-5; this docstring said otherwise
    until PR 9 and was wrong). ``patch_profile_section`` emits a
    ``ReplaceSection`` op — the wholesale-list-replace type ADR-063's 2026-07-29
    amendment (2) listed as missing, built in PR 3 — and hands it to
    ``commit_ops``, which does exist. So a section edit inherits the committer's
    whole invariant set transitively: ops applied only through ``apply_ops``;
    the **persisted-denial re-floor** (invariant 2, PR 4), so an edit that
    re-introduces a skill the candidate retracted is taken back at the seam; an
    unconditional per-entry trail; the universal completeness recompute; both
    clocks; deterministic skill enrichment; receipt separation; the
    ``_ensure_loadable`` round-trip; and the clause-6 write token. That closes
    FMEA SF-VAULT.4's laundering shape — the write half here, the read half
    (the denial-release corpus) in PR 4.

    Two things remain absent, both deliberately and neither specific to this
    door:

    * the **ADR-042 pre-merge snapshot** — ``patch_profile_section`` passes
      ``snapshot=None``. Widening snapshot coverage beyond the two import
      writers is BLOCKED by decision (ADR-063 amendment (5) / #339): with
      ``SNAPSHOT_MAX_PER_PROFILE`` pruned by recency, snapshotting every write
      would let one editing session evict the import snapshot. It is a parameter
      that says so, not a silent gap;
    * **releasing** a denial — the ADR-059 un-denial act is unbuilt
      committer-wide (``UN_DENIAL_INTAKE``, #506), so a denial floored here is
      permanent.

    What is NOT missing, despite the older reading: ``reconcile``, the ADR-046
    stance guard and ``enforce_attribution`` are type-1 controls over TURN TEXT,
    and they run inside ``reconcile()`` before ops land. A section edit is a
    DIRECT act by the candidate about their own history — ``grounding=None`` →
    ``confirmed`` — and the committer never re-adjudicates direct user input
    (§7.4 ruling / ADR-061 clause 2, amended 2026-08-08). Requiring turn text
    here would put an LLM adjudication in front of every keystroke.
    """
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile import patch_profile_section

    # The adapter is shaped against the CV's own profile; the intake resolves
    # the profile it writes itself (``_get_latest``). Those are the same row
    # under ADR-022 (1 User → 1 Profile, canonical in every edition) — both
    # ``MasterProfile(...)`` constructors sit on a "first import" branch.
    profile = await db.get(MasterProfile, record.profile_id)
    if profile is None:
        return

    profile_data = MasterProfileData.model_validate(profile.profile_json)

    # D2-2 = A: the introduction (the only language-slotted section) is
    # document-only, so no document-language resolution is needed here.
    lang = "de"

    field_edit = build_section_field_edit(
        section_id,
        content,
        profile_data=profile_data,
        content_snapshot=record.content_snapshot,
        lang=lang,
        tailored_data=record.tailored_data,
    )
    if field_edit is None:
        return

    section, value = field_edit
    await patch_profile_section(
        section,
        value,
        db,
        source="manual_edit",
        source_session_id=str(cv_id),
        user_id=owner,
    )


def _section_contents(
    snapshot: ContentSnapshot, overrides: dict, entry_lines: dict[str, list[str]] | None = None
) -> dict[str, str]:
    """Current per-section text: snapshot merged with overrides (override wins)."""
    contents: dict[str, str] = {
        "introduction": overrides.get("introduction", snapshot.introduction),
        "skills": overrides.get("skills", "\n".join(snapshot.skills)),
    }
    for pos in snapshot.positions:
        sid = f"position::{pos.id}"
        contents[sid] = overrides.get(sid, "\n".join(pos.bullets))
    for kind, lines in (entry_lines or {}).items():
        for i, line in enumerate(lines):
            sid = f"{kind}::{i}"
            contents[sid] = overrides.get(sid, line)
    return contents


async def _load_gap_analysis(
    cv_id: uuid.UUID, db: AsyncSession, owner: uuid.UUID
) -> GapAnalysis | None:
    """The gap analysis linked to this CV via the owner's FlowSession, or None
    (ADR-092 cl. 3 table: flow lookups ``+ user_id``)."""
    flow_result = await db.execute(
        select(FlowSession)
        .where(
            FlowSession.generated_cv_id == cv_id,
            FlowSession.user_id == owner,
            FlowSession.deleted_at.is_(None),
        )
        .limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    if not flow or not flow.gap_analysis_id:
        return None
    gap = (
        await db.execute(
            select(GapAnalysis).where(
                GapAnalysis.id == flow.gap_analysis_id, GapAnalysis.user_id == owner
            )
        )
    ).scalar_one_or_none()
    return gap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_cv(cv_id: uuid.UUID, db: AsyncSession, owner: uuid.UUID) -> GeneratedCV:
    """The owner's live CV — a foreign id reads like a missing one (S-10)."""
    return await owned_cv(db, cv_id, owner)
