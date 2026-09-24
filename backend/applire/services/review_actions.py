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


class TakeOutStemOnly(Exception):
    """RULING B-1: every matched form of the finding hit only through the
    token-stem fallback (the document carries another word form), so a removal
    rewrite has no literal wording to take out — Lead B's replay broke the
    sentence in 4 of 4 rounds on this shape. Refused (→ 409); the user edits."""


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


def _section_holds_figures(text: str, figures: list[str]) -> bool:
    """RULING E-1: a figure finding selects sections by the figure's canonical
    value as a whole token (WP-B's ``figure_present``) — a substring test would
    pick a section saying "380" for a "38" finding."""
    from applire.services.review_rewrite import figure_present

    return any(figure_present(f, text or "") for f in figures)


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
        vault_changed = testimony.status in ("applied", "partial")
        if vault_changed:
            state = rs.with_decision(rs.load_state(record.review_state), key, finding.label, "added")
            await _save_state(record, state, db)
        still = rs.find_listed(findings_of(record), key) is not None
    # RULING E-2: the vault changed, so the SIBLING document of the same
    # application is re-audited too — AFTER the pressed document's lock is
    # released (holding both would let a concurrent add-evidence on the sibling
    # take them in the opposite order and deadlock). Never fails the request.
    if vault_changed:
        await _reaudit_sibling(kind, record, key, finding.label, db)
    return ActionOutcome(record=record, testimony=testimony, still_listed=still)


async def sibling_document_id(kind: Kind, record, db: AsyncSession) -> tuple[Kind, uuid.UUID] | None:
    """The other document of the same application: the job's flow session
    (one per user+job) records the CURRENT CV and cover letter. Only when the
    pressed document IS that flow's current one — an older regeneration has no
    defined sibling."""
    from applire.models.flow import FlowSession

    flows = (
        await db.execute(
            select(FlowSession).where(
                FlowSession.job_id == record.job_analysis_id,
                FlowSession.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for flow in flows:
        if kind == "cv" and flow.generated_cv_id == record.id and flow.generated_cover_letter_id:
            return "cover_letter", flow.generated_cover_letter_id
        if kind == "cover_letter" and flow.generated_cover_letter_id == record.id and flow.generated_cv_id:
            return "cv", flow.generated_cv_id
    return None


async def _reaudit_sibling(kind: Kind, record, key: str, label: str, db: AsyncSession) -> None:
    try:
        sib = await sibling_document_id(kind, record, db)
        if sib is None:
            return
        sib_kind, sib_id = sib
        async with rs.document_lock(sib_kind, sib_id):
            sibling = await load_document(sib_kind, sib_id, db)
            listed_before = rs.find_listed(findings_of(sibling), key)
            await reaudit(sib_kind, sibling, db)
            if listed_before is not None and rs.find_listed(findings_of(sibling), key) is None:
                state = rs.with_decision(
                    rs.load_state(sibling.review_state), key, listed_before.label or label, "added",
                )
                await _save_state(sibling, state, db)
    except Exception:
        logger.exception("sibling re-audit after add-evidence failed (document %s)", record.id)
        try:
            await db.rollback()  # the pressed document is already committed
        except Exception:  # pragma: no cover
            pass


async def take_out(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, provider) -> ActionOutcome:
    rewrite_for_removal = _rewriter()
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        finding = _listed_or_raise(record, key)
        if finding.matches and all(m.get("stem") for m in finding.matches):
            raise TakeOutStemOnly(
                f"finding {key!r} matched only through another word form; edit it yourself"
            )
        wording = finding.wording()
        # RULING E-1 part 2: an Oracle finding whose verdict names figures
        # removes ONLY those figures (the rest of the claim is the candidate's
        # true prose); without the field it takes the whole-claim path.
        figures_only = finding.producer == "oracle" and bool(finding.claim_figures)
        if figures_only:
            wording = list(finding.claim_figures)
        language = await _document_language(kind, record, db)
        changes: list[dict] = []
        for section_id, section_text in await patchable_sections(kind, record, db):
            holds = _section_holds_figures if figures_only else _section_holds
            if not holds(section_text, wording):
                continue
            result = await rewrite_for_removal(
                kind, record, section_id, section_text, wording, provider,
                language=language, figures_only=figures_only,
            )
            if not result.changed or result.after == section_text:
                continue
            changes.append({"section_id": section_id, "before": section_text, "after": result.after})
        # Adversarial finding 2 (2026-09-24): all-or-nothing ACROSS sections.
        # Every rewrite runs first; nothing is written unless all of them
        # returned, so a failing second section can no longer leave the first
        # one silently saved with no decision and no re-audit.
        for change in changes:
            await write_section(kind, record, change["section_id"], change["after"], db)
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
        prior = rs.get_decision(rs.load_state(record.review_state), key)
        # Adversarial finding 1 (2026-09-24): only a finding this document
        # actually had may become a decision. The section save may already have
        # re-audited in the background, so "listed right now" is too strict;
        # "listed now, decided before, or a term the report still knows" is not.
        known = before is not None or prior is not None or _term_known_to_report(record, key)
        await reaudit(kind, record, db)
        still = rs.find_listed(findings_of(record), key) is not None
        if not still and known:
            if prior is not None and before is None:
                label = prior.get("label") or label
            state = rs.with_decision(rs.load_state(record.review_state), key, label, "edited")
            await _save_state(record, state, db)
        return ActionOutcome(record=record, still_listed=still)


def _term_known_to_report(record, key: str) -> bool:
    """An ``ats:`` key whose term the document's report still carries in any
    keyword list: a flagged keyword that cleared moves between lists, it does
    not vanish. An ``oracle:`` claim that cleared leaves no trace, so it counts
    only when it was listed before the re-audit or already decided."""
    producer, norm = rs.split_key(key)
    if producer != "ats":
        return False
    keywords = (getattr(record, "ats_report", None) or {}).get("keywords") or {}
    for value in keywords.values():
        if isinstance(value, list) and any(
            isinstance(term, str) and rs.norm_quote(term) == norm for term in value
        ):
            return True
    return False


async def walked(kind: Kind, doc_id: uuid.UUID, db: AsyncSession) -> ActionOutcome:
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db)
        await _save_state(record, rs.with_walked(rs.load_state(record.review_state)), db)
        return ActionOutcome(record=record)
