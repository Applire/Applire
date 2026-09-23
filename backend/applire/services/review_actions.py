# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 clauses 3–6 — the review surface's group-1 actions, one implementation
for both document kinds.

Every action runs under the document's :func:`review_state.document_lock`, and
**awaits** the document re-audit (the same ``_update_ats_report`` /
``_update_ats_report_letter`` the section editor schedules in the background,
called directly) before it records a decision or answers. The answer therefore
always carries the report computed from the content it just wrote.

* ``add_evidence`` (cl. 4) — the same ``submit_testimony`` service
  ``POST /api/profile/testimony`` and the MCP tool call (ADR-058 door parity),
  then the re-audit. A testimony that changed the vault records ``added``.
* ``take_out`` (cl. 3) — each patchable section holding the finding's wording is
  rewritten by WP-B's ``rewrite_for_removal`` (a model rewrite, user-triggered;
  not ADR-082's deterministic deletion), saved through the section-editor write,
  re-audited, recorded as ``taken_out`` with the pre-rewrite text for Undo.
* ``undo`` — writes the stored ``before`` text back the same way, re-audits and
  drops the decision. An ``added`` decision has no undo: testimony writes take no
  ADR-042 snapshot, so ``undo-last-merge`` would roll back a different merge
  (NOTE A-2) — refused with :class:`UndoUnavailable`.
* ``edited`` (cl. 5) — after a section save opened from a finding: re-audit, and
  record ``edited`` only when the finding cleared.
* ``walked`` — stamps ``walked_at``.

Nothing here decides what counts as open: that is :func:`review_state.derive_review`
over the live report (SF-REVIEW.9).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.services import review_state as rs

logger = logging.getLogger(__name__)

Kind = Literal["cv", "cover_letter"]


class FindingNotListed(LookupError):
    """The finding_key names no finding the current report lists (→ 409)."""


class NoDecision(LookupError):
    """``undo`` on a finding with no recorded decision (→ 409)."""


class UndoUnavailable(Exception):
    """``undo`` on an ``added`` decision (→ 409, NOTE A-2)."""


class RewriteUnavailable(Exception):
    """The removal rewrite service is not installed (pre-integration, → 503)."""


@dataclass
class ActionOutcome:
    record: Any
    testimony: Any = None
    changes: list[dict] = field(default_factory=list)
    still_listed: bool = False


# ── per-kind plumbing ────────────────────────────────────────────────────────


async def load_document(kind: Kind, doc_id: uuid.UUID, db: AsyncSession):
    if kind == "cv":
        from applire.services.cv import _load_cv

        return await _load_cv(doc_id, db)
    from applire.models.cover_letter import GeneratedCoverLetter

    cl = (
        await db.execute(
            select(GeneratedCoverLetter).where(
                GeneratedCoverLetter.id == doc_id,
                GeneratedCoverLetter.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {doc_id} not found")
    return cl


async def reaudit(kind: Kind, record, db: AsyncSession) -> None:
    """The document re-audit, AWAITED (ADR-090 cl. 3/4) — the exact function the
    section editor's background task runs. It commits."""
    if kind == "cv":
        from applire.services.cv import _update_ats_report

        await _update_ats_report(record, db)
    else:
        from applire.services.cover_letter import _update_ats_report_letter

        await _update_ats_report_letter(record, db)
    await db.refresh(record)


def findings_of(record) -> list[rs.GroupOneFinding]:
    ats = record.ats_report if isinstance(record.ats_report, dict) else None
    truth = record.truthfulness_report if isinstance(record.truthfulness_report, dict) else None
    return rs.group_one_findings(ats, truth)


async def _save_state(record, state: dict, db: AsyncSession) -> None:
    record.review_state = state
    await db.commit()
    await db.refresh(record)


async def _document_language(kind: Kind, record, db: AsyncSession) -> str:
    if record.document_language:
        return record.document_language
    from applire.models.job import JobAnalysis
    from applire.services.application import get_application_for_job
    from applire.services.color_detection import _CE_STUB_USER_ID
    from applire.utils.language_detection import resolve_document_language

    job = await db.get(JobAnalysis, record.job_analysis_id)
    application = await get_application_for_job(record.job_analysis_id, _CE_STUB_USER_ID, db)
    return resolve_document_language(application, job) if job else "de"


async def patchable_sections(kind: Kind, record, db: AsyncSession) -> list[tuple[str, str]]:
    """(section_id, current text) for every section the user's editor can write —
    CV: what ``PATCH /api/cv/{id}/sections/{section_id}`` takes; letter: ``body``."""
    if kind == "cv":
        from applire.services.cv_section_editor import get_cv_sections

        resp = await get_cv_sections(record.id, db)
        return [(s.section_id, s.content or "") for s in resp.sections]
    from applire.services.cover_letter import _apply_section_overrides

    data = _apply_section_overrides(record.letter_data or {}, record.section_overrides or {})
    paragraphs = ((data.get("body") or {}).get("paragraphs")) or []
    return [("body", "\n\n".join(p for p in paragraphs if isinstance(p, str)))]


async def write_section(kind: Kind, record, section_id: str, content: str, db: AsyncSession) -> None:
    """The section-editor write, WITHOUT its background re-audit (the caller
    awaits one)."""
    if kind == "cv":
        from applire.services.cv_section_editor import patch_cv_section

        await patch_cv_section(record.id, section_id, content, False, db, None)
    else:
        from applire.services.cover_letter import patch_cover_letter_section

        await patch_cover_letter_section(record.id, section_id, content, db, None)
    await db.refresh(record)


def _section_holds(text: str, wording: list[str]) -> bool:
    from applire.services.ats_audit import _norm, surface_present

    t = _norm(text or "")
    return any(surface_present(w, t) for w in wording)


def _rewriter():
    """WP-B's Contract 1 (``services/review_rewrite.rewrite_for_removal``)."""
    try:
        from applire.services.review_rewrite import rewrite_for_removal  # type: ignore
    except ImportError as exc:  # pre-integration: WP-B not merged yet
        raise RewriteUnavailable("the removal rewrite is not installed") from exc
    return rewrite_for_removal


def _listed_or_raise(record, key: str) -> rs.GroupOneFinding:
    rs.split_key(key)  # ValueError → 422
    f = rs.find_listed(findings_of(record), key)
    if f is None:
        raise FindingNotListed(f"finding {key!r} is not listed on the current report")
    return f


# ── the actions ──────────────────────────────────────────────────────────────


async def add_evidence(kind: Kind, doc_id: uuid.UUID, key: str, text: str, db: AsyncSession, provider) -> ActionOutcome:
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        finding = _listed_or_raise(record, key)
        testimony = await submit_testimony(text, db, provider)
        await reaudit(kind, record, db)
        if testimony.status in ("applied", "partial"):
            state = rs.with_decision(rs.load_state(record.review_state), key, finding.label, "added")
            await _save_state(record, state, db)
        still = rs.find_listed(findings_of(record), key) is not None
        return ActionOutcome(record=record, testimony=testimony, still_listed=still)


async def take_out(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, provider) -> ActionOutcome:
    rewrite_for_removal = _rewriter()
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        finding = _listed_or_raise(record, key)
        wording = finding.wording()
        language = await _document_language(kind, record, db)
        changes: list[dict] = []
        for section_id, section_text in await patchable_sections(kind, record, db):
            if not _section_holds(section_text, wording):
                continue
            result = await rewrite_for_removal(
                kind, record, section_id, section_text, wording, provider, language=language,
            )
            if not result.changed or result.after == section_text:
                continue
            await write_section(kind, record, section_id, result.after, db)
            changes.append({"section_id": section_id, "before": section_text, "after": result.after})
        if changes:
            await reaudit(kind, record, db)
            state = rs.with_decision(
                rs.load_state(record.review_state), key, finding.label, "taken_out",
                undo_sections=[{"section_id": c["section_id"], "before": c["before"]} for c in changes],
            )
            await _save_state(record, state, db)
        still = rs.find_listed(findings_of(record), key) is not None
        return ActionOutcome(record=record, changes=changes, still_listed=still)


async def undo(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession) -> ActionOutcome:
    rs.split_key(key)
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        state = rs.load_state(record.review_state)
        decision = rs.get_decision(state, key)
        if decision is None:
            raise NoDecision(f"no decision recorded for {key!r}")
        if decision["action"] == "added":
            raise UndoUnavailable(
                "a profile addition cannot be undone from the review surface "
                "(testimony writes take no merge snapshot)"
            )
        sections = ((decision.get("undo") or {}).get("sections")) or []
        for s in sections:
            await write_section(kind, record, s["section_id"], s["before"], db)
        if sections:
            await reaudit(kind, record, db)
        await _save_state(record, rs.without_decision(state, key), db)
        still = rs.find_listed(findings_of(record), key) is not None
        return ActionOutcome(record=record, still_listed=still)


async def edited(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession) -> ActionOutcome:
    """Called after a section save that was opened from a finding (cl. 5). The
    save itself went through the editor unchanged; this awaits the re-audit and
    records ``edited`` only if the finding cleared. No undo text: the editor's
    own history is the user's."""
    rs.split_key(key)
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        label = key.partition(":")[2]
        before = rs.find_listed(findings_of(record), key)
        if before is not None:
            label = before.label
        await reaudit(kind, record, db)
        still = rs.find_listed(findings_of(record), key) is not None
        if not still:
            prior = rs.get_decision(rs.load_state(record.review_state), key)
            if prior is not None and before is None:
                label = prior.get("label") or label
            state = rs.with_decision(rs.load_state(record.review_state), key, label, "edited")
            await _save_state(record, state, db)
        return ActionOutcome(record=record, still_listed=still)


async def walked(kind: Kind, doc_id: uuid.UUID, db: AsyncSession) -> ActionOutcome:
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        await _save_state(record, rs.with_walked(rs.load_state(record.review_state)), db)
        return ActionOutcome(record=record)
