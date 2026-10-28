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
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.services import review_state as rs

logger = logging.getLogger(__name__)

Kind = Literal["cv", "cover_letter"]


# 786:232 — a section save from a finding ran the full re-audit TWICE: the section
# PATCH schedules the background re-audit (``_update_ats_report_by_id``, under
# ``document_lock``), and ``edited`` then waited for that lock and re-audited the
# same, unchanged content again (ux-audit-2 log: outcome-critic pairs 49 s and 31 s
# apart for one save). Each completed audit notes the content fingerprint it audited;
# ``edited`` skips its own re-audit when the report already describes the current
# content. In-process only: a miss (restart, another worker) re-audits — the
# pre-786:232 behaviour, never a stale report.
_AUDITED: dict[tuple[str, str], str] = {}


def content_fingerprint(kind: Kind, record, *, user_id: uuid.UUID | None = None) -> str:
    """Hash of every column an audit reads content from (CV: tailored_data,
    content_snapshot, section_overrides; letter: letter_data, section_overrides)."""
    import hashlib
    import json

    cols = (
        ("tailored_data", "content_snapshot", "section_overrides")
        if kind == "cv"
        else ("letter_data", "section_overrides")
    )
    payload = json.dumps(
        [getattr(record, c, None) for c in cols], sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def note_audited(kind: Kind, record, *, user_id: uuid.UUID | None = None) -> None:
    """Record that ``record``'s current content has a report computed from it."""
    if isinstance(getattr(record, "ats_report", None), dict):
        _AUDITED[(kind, str(record.id))] = content_fingerprint(kind, record)
    else:
        _AUDITED.pop((kind, str(record.id)), None)


def audited_current(kind: Kind, record, *, user_id: uuid.UUID | None = None) -> bool:
    """True when the persisted report was computed from exactly this content."""
    if not isinstance(getattr(record, "ats_report", None), dict):
        return False
    return _AUDITED.get((kind, str(record.id))) == content_fingerprint(kind, record)


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


class TakeOutProtectedName(Exception):
    """ADR-090 amended 2026-09-26 (WP-R, ruling R-1 belt): a section the removal
    would rewrite holds a job title or employer name — the target posting's, the
    letter's recipient, or one from the candidate's own work history — that
    contains the finding's wording. The removal prompt's rule 1 (remove every
    occurrence) and rule 4 (keep job titles and employers) cannot both hold on
    that passage, and the all-or-nothing check (the form must be gone) can only
    pass by changing the name. The #736 delivery run's ``Payments platform`` row
    was exactly this: every take-out would have rewritten the target title.
    Refused before any model call (→ 409); the user edits."""


class FindingDenied(Exception):
    """Build-2 delivery F2 (SF-REVIEW.18): one of the candidate's own recorded
    denials covers the finding's term (``GroupOneFinding.denials``), so writing
    it into the profile as testimony would record the claim they disclaimed.
    Refused before any model call (→ 409 ``finding_denied``, with the denials);
    the user takes it out or edits it. A changed position goes through the
    profile's own denial correction (ADR-059 am. 2026-07-26 cl. 4)."""

    def __init__(self, key: str, denials: list[str]):
        super().__init__(f"finding {key!r} is covered by a recorded denial")
        self.denials = list(denials)


class RewriteUnavailable(Exception):
    """The removal rewrite service is not installed (pre-integration, → 503)."""


@dataclass
class ActionOutcome:
    record: Any
    testimony: Any = None
    changes: list[dict] = field(default_factory=list)
    still_listed: bool = False


# ── per-kind plumbing ────────────────────────────────────────────────────────


async def load_document(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None):
    """The caller's live document — a foreign id reads like a missing one
    (ADR-092 cl. 6, S-10; both kinds, both through an owner-keyed read)."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.load_document")
    if kind == "cv":
        from applire.services.cv import _load_cv

        return await _load_cv(doc_id, db, user_id=owner)
    from applire.models.cover_letter import GeneratedCoverLetter

    cl = (
        await db.execute(
            select(GeneratedCoverLetter).where(
                GeneratedCoverLetter.id == doc_id,
                GeneratedCoverLetter.user_id == owner,
                GeneratedCoverLetter.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {doc_id} not found")
    return cl


async def reaudit(kind: Kind, record, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None:
    """The document re-audit, AWAITED (ADR-090 cl. 3/4) — the exact function the
    section editor's background task runs. It commits."""
    if kind == "cv":
        from applire.services.cv import _update_ats_report

        await _update_ats_report(record, db)
    else:
        from applire.services.cover_letter import _update_ats_report_letter

        await _update_ats_report_letter(record, db)
    await db.refresh(record)
    note_audited(kind, record)


def findings_of(record, *, user_id: uuid.UUID | None = None) -> list[rs.GroupOneFinding]:
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
    from applire.utils.language_detection import resolve_document_language

    job = await db.get(JobAnalysis, record.job_analysis_id)
    # ADR-092: the document's owner's link (the record was loaded owner-keyed).
    application = await get_application_for_job(record.job_analysis_id, record.user_id, db)
    return resolve_document_language(application, job) if job else "de"


async def patchable_sections(kind: Kind, record, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> list[tuple[str, str]]:
    """(section_id, current text) for every section the user's editor can write —
    CV: what ``PATCH /api/cv/{id}/sections/{section_id}`` takes; letter: ``body``."""
    if kind == "cv":
        from applire.services.cv_section_editor import get_cv_sections

        resp = await get_cv_sections(record.id, db, user_id=record.user_id)
        return [(s.section_id, s.content or "") for s in resp.sections]
    from applire.services.cover_letter import _apply_section_overrides

    data = _apply_section_overrides(record.letter_data or {}, record.section_overrides or {})
    paragraphs = ((data.get("body") or {}).get("paragraphs")) or []
    return [("body", "\n\n".join(p for p in paragraphs if isinstance(p, str)))]


async def write_section(kind: Kind, record, section_id: str, content: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None:
    """The section-editor write, WITHOUT its background re-audit (the caller
    awaits one)."""
    if kind == "cv":
        from applire.services.cv_section_editor import patch_cv_section

        await patch_cv_section(
            record.id, section_id, content, False, db, None, user_id=record.user_id
        )
    else:
        from applire.services.cover_letter import patch_cover_letter_section

        await patch_cover_letter_section(
            record.id, section_id, content, db, None, user_id=record.user_id
        )
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


async def protected_names(kind: Kind, record, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> list[str]:
    """The names a take-out may never rewrite, normalised (``ats_audit._norm``):
    the posting's title and employer (``non_claim_names_for_job``, the same names
    the audit masks), the letter's ``recipient.company``, and every employer, job
    title and recorded alternate title in the candidate's vault work history.
    Facts about the input (ADR-062 cl. 1), read fresh per request."""
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.services.ats_audit import NonClaimNames, _norm, non_claim_names_for_job

    job = await db.get(JobAnalysis, record.job_analysis_id) if record.job_analysis_id else None
    application = None
    if job is not None:
        from applire.services.application import get_application_for_job

        # ADR-092 cl. 5(f): the owner's own labels for the posting are names too.
        application = await get_application_for_job(job.id, record.user_id, db)
    names = non_claim_names_for_job(job, application) or NonClaimNames()
    if kind == "cover_letter":
        recipient = ((record.letter_data or {}).get("recipient")) or {}
        names = names.plus_employers(recipient.get("company"))
    out: list[str] = list(names.all_names())
    profile = await db.get(MasterProfile, record.profile_id) if getattr(record, "profile_id", None) else None
    work = ((getattr(profile, "profile_json", None) or {}).get("work_experience")) or []
    vault_employers: list[str] = []
    for w in work:
        if not isinstance(w, dict):
            continue
        vault_employers.append(w.get("company") or "")
        for t in [w.get("role"), w.get("title"), *(w.get("role_aliases") or [])]:
            n = _norm(t) if isinstance(t, str) else ""
            if n and n not in out:
                out.append(n)
    for e in NonClaimNames().plus_employers(*vault_employers).employers:
        if e not in out:
            out.append(e)
    return out


def protected_name_hit(section_text: str, wording: list[str], names: list[str], *, user_id: uuid.UUID | None = None) -> str | None:
    """The first protected name that stands in ``section_text`` AND contains one
    of ``wording``'s forms (the audit's own ``surface_present``), else ``None``."""
    from applire.services.ats_audit import _norm, surface_present

    t = _norm(section_text or "")
    for n in sorted(names, key=len, reverse=True):
        # the NAME must stand in the section as whole words (adversarial finding
        # 3: a vault title "IT" is a substring of "mit"/"seit"); the FORM may sit
        # inside the name as a substring, because the rewrite removes it from
        # inside a compound or longer name too (removal prompt rule 1)
        if not n or not re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t):
            continue
        if any(surface_present(w, n) for w in wording if w):
            return n
    return None


def _listed_or_raise(record, key: str) -> rs.GroupOneFinding:
    rs.split_key(key)  # ValueError → 422
    f = rs.find_listed(findings_of(record), key)
    if f is None:
        raise FindingNotListed(f"finding {key!r} is not listed on the current report")
    return f


# ── the actions ──────────────────────────────────────────────────────────────


async def add_evidence(kind: Kind, doc_id: uuid.UUID, key: str, text: str, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.add_evidence")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        finding = _listed_or_raise(record, key)
        if finding.denials:
            raise FindingDenied(key, finding.denials)
        testimony = await submit_testimony(text, db, provider, user_id=owner)
        await reaudit(kind, record, db)
        vault_changed = testimony.status in ("applied", "partial")
        still = rs.find_listed(findings_of(record), key) is not None
        if vault_changed:
            state = rs.with_decision(rs.load_state(record.review_state), key, finding.label, "added")
            await _save_state(record, state, db)
        elif still and testimony.status != "error":
            # ADR-099 cl. 6 ("Stimmt das?"): the answer changed nothing in the
            # vault, so the finding stays — the card now offers only
            # "Entfernen". Persisted so a reload shows the same end state.
            state = rs.with_decision(rs.load_state(record.review_state), key, finding.label, "no_change")
            await _save_state(record, state, db)
    # RULING E-2: the vault changed, so the SIBLING document of the same
    # application is re-audited too — AFTER the pressed document's lock is
    # released (holding both would let a concurrent add-evidence on the sibling
    # take them in the opposite order and deadlock). Never fails the request.
    if vault_changed:
        await _reaudit_sibling(kind, record, key, finding.label, db)
    return ActionOutcome(record=record, testimony=testimony, still_listed=still)


class NotSupportedByProfile(LookupError):
    """D2-5: a wording of the finding is not in the candidate's profile, so the
    one-click close is refused (→ 409) and the surface asks for testimony."""


def _profile_for_support(profile_json: dict) -> tuple[dict, list[str]]:
    """The WHOLE profile minus what can never back a claim: ``denied`` skills,
    languages and certifications (ADR-061 am. 2026-08-08). ``unconfirmed`` ones
    stay in — the candidate's click is the confirmation ADR-061 cl. 3 asks for.
    Also returns the persisted denied concepts."""
    import copy as _copy

    out = _copy.deepcopy(profile_json or {})
    for section in ("skills", "languages", "certifications"):
        items = out.get(section)
        if isinstance(items, list):
            out[section] = [
                e for e in items if not (isinstance(e, dict) and e.get("status") == "denied")
            ]
    denied = [
        d.get("concept") for d in ((out.get("metadata") or {}).get("denied_concepts") or [])
        if isinstance(d, dict) and d.get("concept")
    ]
    return out, denied


def support_in_profile(
    wordings: list[str], profile_json: dict, *, user_id: uuid.UUID | None = None,
) -> tuple[list[dict], list[str]] | None:
    """Bug #797 / founder ruling D2-5 = A — does the candidate's WHOLE profile
    hold every wording this finding stands on?

    ``(receipt, unconfirmed_skill_names)`` when every wording grounds through
    THE skill instrument (``ground_skill_claim``: whole token, near-dupe, slash
    enumerations) over the profile minus denied entries, and none is a denied
    concept; ``None`` otherwise. ``receipt`` names the backing profile lines (at
    most three, de-duplicated); ``unconfirmed_skill_names`` the skills with
    status ``unconfirmed`` that back a wording — the caller confirms them. Pure: no database, no model call.
    ``user_id`` is accepted for the service-signature contract (ADR-092) and
    unused: the caller passes the owner's own profile.
    """
    from applire.services.oracle.matchers import build_vault_index, ground_skill_claim
    from applire.services.profile.reconcile.stance import is_denied_concept

    wordings = [w for w in wordings if w and w.strip()]
    if not wordings:
        return None
    usable, denied = _profile_for_support(profile_json)
    if denied and any(is_denied_concept(w, denied, None) for w in wordings):
        return None
    try:
        index = build_vault_index(usable)
    except Exception:  # pragma: no cover — an unreadable profile supports nothing
        return None
    units = []
    for w in wordings:
        unit = ground_skill_claim(w, index)
        if unit is None:
            return None
        units.append(unit)
    receipt: list[dict] = []
    seen: set[str] = set()
    for u in units:
        if u.path not in seen:
            seen.add(u.path)
            receipt.append({"path": u.path, "text": u.text})
    skills = usable.get("skills") or []
    unconfirmed: list[str] = []
    for u in units:
        m = re.fullmatch(r"skills\[(\d+)\]", u.path)
        if m:
            i = int(m.group(1))
            entry = skills[i] if i < len(skills) else None
            if isinstance(entry, dict) and entry.get("status") == "unconfirmed":
                name = entry.get("name") or ""
                if name and name not in unconfirmed:
                    unconfirmed.append(name)
    return receipt[:3], unconfirmed


async def confirm_supported(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """Bug #797 / founder ruling D2-5 = A (ADR-090 cl. 6 amended 2026-10-21).

    *Stimmt, in mein Profil aufnehmen* on a finding the profile already holds:
    no testimony is asked for. When :func:`support_in_profile` finds every
    wording of the finding in the candidate's whole profile, a matching
    UNCONFIRMED skill is confirmed through the shared intake (ADR-061 cl. 3 —
    the click is the candidate's confirmation), the document is re-audited, and
    a ``supported`` decision with the receipt closes the row even if the
    keyword check still lists it. Otherwise :class:`NotSupportedByProfile`
    (→ 409) and nothing is written — the surface falls back to the ask."""
    from applire.services.owner_resolution import resolve_owner
    from applire.services.profile import get_profile_for_user, patch_profile_section

    owner = resolve_owner(user_id, site="review_actions.confirm_supported")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        finding = _listed_or_raise(record, key)
        if finding.denials:
            raise FindingDenied(key, finding.denials)
        profile = await get_profile_for_user(db, owner)
        profile_json = (profile.profile_json if profile else None) or {}
        found = support_in_profile(finding.wording(), profile_json, user_id=owner)
        if found is None:
            raise NotSupportedByProfile(
                f"a wording of {key!r} is not in the profile — testimony needed"
            )
        receipt, unconfirmed = found
        if unconfirmed:
            skills = [dict(s) for s in (profile_json.get("skills") or [])]
            names = {n.strip().casefold() for n in unconfirmed}
            for s in skills:
                if (s.get("name") or "").strip().casefold() in names and s.get("status") == "unconfirmed":
                    s["status"] = "confirmed"
            await patch_profile_section(
                "skills", skills, db, source="manual_edit",
                source_session_id=str(doc_id), user_id=owner,
            )
        await reaudit(kind, record, db)
        state = rs.with_decision(
            rs.load_state(record.review_state), key, finding.label, "supported", receipt=receipt,
        )
        await _save_state(record, state, db)
        still = rs.find_listed(findings_of(record), key) is not None
    if unconfirmed:
        await _reaudit_sibling(kind, record, key, finding.label, db)
    return ActionOutcome(record=record, still_listed=still)


async def sibling_document_id(kind: Kind, record, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> tuple[Kind, uuid.UUID] | None:
    """The other document of the same application: the job's flow session
    (one per user+job) records the CURRENT CV and cover letter. Only when the
    pressed document IS that flow's current one — an older regeneration has no
    defined sibling."""
    from applire.models.flow import FlowSession

    flows = (
        await db.execute(
            select(FlowSession).where(
                FlowSession.job_id == record.job_analysis_id,
                # ADR-092: the posting is shared; the flow is the owner's.
                FlowSession.user_id == record.user_id,
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
            sibling = await load_document(sib_kind, sib_id, db, user_id=record.user_id)
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


async def take_out(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.take_out")
    # ADR-092: the ownership check precedes everything else, so a foreign id is
    # a 404 even where the rewriter is not installed.
    await load_document(kind, doc_id, db, user_id=owner)
    rewrite_for_removal = _rewriter()
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        if rs.split_key(key)[0] == "critic":
            # #702 RULING R-2 = A (ADR-090 cl. 3 amended 2026-10-07): a
            # cross-document item is taken out of the LETTER by the same removal
            # rewrite — its literal concepts, or the whole sentence.
            from applire.services.review_signals import cross_document_target

            label, wording = cross_document_target(record, key)
            finding = rs.GroupOneFinding(key=key, producer="critic", norm=rs.split_key(key)[1], label=label)
            critic_sentence = label
        else:
            critic_sentence = None
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
        holds = _section_holds_figures if figures_only else _section_holds
        # (section_id, text the rewriter sees, full section text, start, end)
        targets: list[tuple[str, str, str, int, int]] = []
        if critic_sentence is not None:
            # Adv-review finding 7 (2026-10-07): a cross-document item is ONE
            # sentence ("Applire schreibt den Satz … neu"). Only that sentence goes
            # to the removal rewrite; the result is spliced back into its section,
            # so another sentence naming the same fact is never touched.
            from applire.services.review_signals import locate_sentence

            hit = locate_sentence(await patchable_sections(kind, record, db), critic_sentence)
            if hit is not None:
                sid, full, start, end = hit
                if holds(full[start:end], wording):
                    targets.append((sid, full[start:end], full, start, end))
        else:
            targets = [
                (sid, text, text, 0, len(text))
                for sid, text in await patchable_sections(kind, record, db)
                if holds(text, wording)
            ]
        # L-3 = A (789:193): a certification / education entry holding the wording
        # leaves THIS CV whole (an empty entry override) — no model rewrite of a
        # name line, and no protected-name refusal (nothing inside a name is
        # rewritten). Undo writes the entry line back.
        from applire.services.cv_section_editor import entry_section_kind

        entry_targets = [t for t in targets if kind == "cv" and entry_section_kind(t[0]) is not None]
        targets = [t for t in targets if t not in entry_targets]
        sections = [(sid, span) for sid, span, _f, _s, _e in targets]
        # WP-R belt (ruling R-1): refuse BEFORE any model call when a section to
        # be rewritten holds a job title / employer name containing the wording.
        if sections:
            names = await protected_names(kind, record, db)
            for sid, text in sections:
                hit = protected_name_hit(text, wording, names)
                if hit is not None:
                    raise TakeOutProtectedName(
                        f"finding {key!r} stands inside the name {hit!r} in section {sid!r}; "
                        "edit it yourself"
                    )
        language = await _document_language(kind, record, db)
        changes: list[dict] = [
            {"section_id": sid, "before": full, "after": ""} for sid, _span, full, _s, _e in entry_targets
        ]
        for section_id, section_text, full, start, end in targets:
            result = await rewrite_for_removal(
                kind, record, section_id, section_text, wording, provider,
                language=language, figures_only=figures_only,
            )
            if not result.changed or result.after == section_text:
                continue
            after = full[:start] + result.after + full[end:]
            changes.append({"section_id": section_id, "before": full, "after": after})
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
        still = await _still_listed(kind, record, key, wording, db, sentence=critic_sentence)
        return ActionOutcome(record=record, changes=changes, still_listed=still)


async def _still_listed(
    kind: Kind, record, key: str, wording: list[str], db: AsyncSession, *, sentence: str | None = None
) -> bool:
    """Group 1: the live report still lists the finding. A cross-document item
    (#702): the critic does not re-run on a re-audit, so "still there" is read off
    the document itself — does the item's SENTENCE still stand and hold the
    wording (adv-review finding 7: another sentence naming the fact is not this
    item)."""
    if rs.split_key(key)[0] == "send":
        return _language_check_failed(record)
    if rs.split_key(key)[0] == "page":
        return _letter_page_check(record) is not None
    if rs.split_key(key)[0] == "critic":
        from applire.services.review_signals import locate_sentence

        if not sentence or not wording:
            return False
        hit = locate_sentence(await patchable_sections(kind, record, db), sentence)
        return hit is not None and _section_holds(hit[1][hit[2]:hit[3]], wording)
    return rs.find_listed(findings_of(record), key) is not None


async def undo(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.undo")
    rs.split_key(key)
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
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
        wording: list[str] = []
        sentence: str | None = None
        if rs.split_key(key)[0] == "critic":
            from applire.services.review_signals import cross_document_target

            try:
                sentence, wording = cross_document_target(record, key)
            except FindingNotListed:
                wording = []
        still = await _still_listed(kind, record, key, wording, db, sentence=sentence)
        return ActionOutcome(record=record, still_listed=still)


async def edited(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """Called after a section save that was opened from a finding (cl. 5). The
    save itself went through the editor unchanged; this awaits the re-audit and
    records ``edited`` only if the finding cleared. No undo text: the editor's
    own history is the user's."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.edited")
    rs.split_key(key)
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
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
        if not audited_current(kind, record):
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


async def walked(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.walked")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        await _save_state(record, rs.with_walked(rs.load_state(record.review_state)), db)
        return ActionOutcome(record=record)


# ── ADR-099 resolvers (UAT-2 fix round, WP-C) ────────────────────────────────
#
# One action per class-A/U row (ADR-099 cl. 2/6/9). The corrector edits are
# WP-B's ``services/delivery_actions.py`` (model edit of ONE sentence + the full
# re-audit); this module holds the document lock around them and records the
# decision with the undo text, exactly like ``take_out``. A finding whose answer
# did not clear it stays OPEN — every count derives from the live report.

#: The send check "Diese Stellen übersetzen" resolves (ADR-099 cl. 9).
SEND_LANGUAGE_KEY = "send:document-language"


class ResolverUnavailable(Exception):
    """WP-B's corrector action is not installed yet (stub raises
    ``NotImplementedError``) → 503; nothing was written."""


class ResolverNotApplicable(LookupError):
    """``delivery_actions.ActionNotApplicable``: the finding / station / span is
    not (or no longer) there, or there is nothing to change → 409."""


class ResolverRewriteFailed(Exception):
    """``delivery_actions.RewriteFailed``: the model edit did not pass its fact
    check; nothing was written → 502."""


def _delivery_actions():
    from applire.services import delivery_actions

    return delivery_actions


async def _call_delivery(coro_fn, *args, **kwargs):
    """Run one ``delivery_actions`` call and map its contract errors."""
    da = _delivery_actions()
    try:
        return await coro_fn(*args, **kwargs)
    except NotImplementedError as exc:
        raise ResolverUnavailable(f"{coro_fn.__name__} is not installed yet") from exc
    except da.ActionNotApplicable as exc:
        raise ResolverNotApplicable(str(exc)) from exc
    except da.RewriteFailed as exc:
        raise ResolverRewriteFailed(str(exc)) from exc


@dataclass
class StationsOutcome:
    stations: list[dict]


async def stations(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> StationsOutcome:
    """The answers of "Wohin gehört das?" (resolver ``ask_station``): the stations
    that own the evidence the Oracle found (WP-B ``owning_stations``). ``[]`` =
    the only end state is "Entfernen". Read-only."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.stations")
    record = await load_document(kind, doc_id, db, user_id=owner)
    _listed_or_raise(record, key)
    da = _delivery_actions()
    options = await _call_delivery(da.owning_stations, kind, doc_id, key, db, user_id=owner)
    return StationsOutcome(stations=[
        {
            "station_id": o.station_id,
            "experience_id": o.experience_id,
            "title": o.title,
            "organization": o.organization,
        }
        for o in options
    ])


async def _resolve_with_edit(
    kind: Kind,
    doc_id: uuid.UUID,
    key: str,
    action: str,
    call,
    db: AsyncSession,
    *,
    owner: uuid.UUID,
    listed_required: bool = True,
) -> ActionOutcome:
    """Shared body of the corrector-backed resolvers: lock → listed check → the
    WP-B call → decision with the undo sections (only when something changed)."""
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        label = key.partition(":")[2]
        if listed_required:
            finding = _listed_or_raise(record, key)
            label = finding.label
        result = await call()
        record = result.record if result.record is not None else record
        changes = [
            {"section_id": c["section_id"], "before": c["before"], "after": c["after"]}
            for c in (result.changes or [])
        ]
        if changes:
            state = rs.with_decision(
                rs.load_state(record.review_state), key, label, action,
                undo_sections=[{"section_id": c["section_id"], "before": c["before"]} for c in changes],
            )
            await _save_state(record, state, db)
        return ActionOutcome(record=record, changes=changes, still_listed=bool(result.still_listed))


async def move(kind: Kind, doc_id: uuid.UUID, key: str, station_id: str, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """"Wohin gehört das?" → a station: WP-B's ``move_sentence`` (corrector edit of
    that sentence + re-audit); decision ``moved`` with undo."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.move")
    da = _delivery_actions()

    async def call():
        return await _call_delivery(
            da.move_sentence, kind, doc_id, key, station_id, db, provider, user_id=owner,
        )

    return await _resolve_with_edit(kind, doc_id, key, "moved", call, db, owner=owner)


async def as_goal(kind: Kind, doc_id: uuid.UUID, key: str, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """"Ist das erreicht?" → Nein: WP-B's ``narrow_or_remove(mode="as_goal")``;
    decision ``as_goal`` with undo."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.as_goal")
    da = _delivery_actions()

    async def call():
        return await _call_delivery(
            da.narrow_or_remove, kind, doc_id, key, db, provider, mode="as_goal", user_id=owner,
        )

    return await _resolve_with_edit(kind, doc_id, key, "as_goal", call, db, owner=owner)


def _language_check_failed(record) -> bool:
    for c in ((record.ats_report or {}) if isinstance(record.ats_report, dict) else {}).get("checks") or []:
        cid = str((c or {}).get("id") or "")
        if cid.split("#", 1)[0] == "document-language" and c.get("status") == "fail":
            return True
    return False


async def translate(kind: Kind, doc_id: uuid.UUID, spans: list[str] | None, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """"Diese Stellen übersetzen" (resolver ``translate``, ADR-099 cl. 9): WP-B's
    ``translate_spans`` rewrites ONLY the sentences the failed
    ``document-language`` check names, then re-audits; decision under
    ``send:document-language`` with undo. ``still_listed`` = the check still
    fails on the new report."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.translate")
    da = _delivery_actions()
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        if not _language_check_failed(record):
            raise ResolverNotApplicable("the document-language check does not fail on this version")
        result = await _call_delivery(da.translate_spans, kind, doc_id, db, provider, spans=spans, user_id=owner)
        record = result.record if result.record is not None else record
        changes = [
            {"section_id": c["section_id"], "before": c["before"], "after": c["after"]}
            for c in (result.changes or [])
        ]
        if changes:
            state = rs.with_decision(
                rs.load_state(record.review_state), SEND_LANGUAGE_KEY, "document-language", "translated",
                undo_sections=[{"section_id": c["section_id"], "before": c["before"]} for c in changes],
            )
            await _save_state(record, state, db)
        return ActionOutcome(record=record, changes=changes, still_listed=_language_check_failed(record))


async def recheck(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """"Jetzt prüfen" (resolver ``recheck``, ADR-099 cl. 9 / UAT-3h): the full
    document re-audit on THIS version — no new version, no content change. A
    producer that still cannot run stays unknown (the surface says so)."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.recheck")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        await reaudit(kind, record, db)
        return ActionOutcome(record=record)


#: ADR-099 cl. 7 (INTERFACE B-2): the decision key of the letter's page-length card.
PAGE_LENGTH_KEY = "page:page-overflow"


def _letter_page_check(record) -> dict | None:
    """The letter's failing ``page-length`` check (B-2: details_key
    ``page-length-letter``, params ``{pages, letterPages}``) — the card's data."""
    report = record.ats_report if isinstance(record.ats_report, dict) else {}
    for c in report.get("checks") or []:
        cid = str((c or {}).get("id") or "").split("#", 1)[0]
        if cid.startswith("page-length") and cid != "page-length-kept" and c.get("status") == "fail":
            return c
    return None


async def shorten_letter(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """Page card → "Auf {n} Seite kürzen" (resolver ``page_length``, letter):
    WP-B's ``shorten_letter`` (one corrector call + re-audit); decision
    ``shortened`` under ``page:page-overflow`` with undo. ``still_listed`` =
    still over target → the card comes back with the new count."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.shorten_letter")
    da = _delivery_actions()
    async with rs.document_lock(kind, doc_id):
        # Ownership first (another user's id is a 404, never a 409), then the kind.
        record = await load_document(kind, doc_id, db, user_id=owner)
        if kind != "cover_letter":
            raise ResolverNotApplicable("the CV's page card decides through /page-overflow")
        if _letter_page_check(record) is None:
            raise ResolverNotApplicable("the letter is not over its page target")
        shorten = getattr(da, "shorten_letter", None)
        if shorten is None:  # WP-B's action not installed yet
            raise ResolverUnavailable("delivery action shorten_letter is not available")
        result = await _call_delivery(shorten, kind, doc_id, db, provider, user_id=owner)
        record = result.record if result.record is not None else record
        changes = [
            {"section_id": c["section_id"], "before": c["before"], "after": c["after"]}
            for c in (result.changes or [])
        ]
        if changes:
            state = rs.with_decision(
                rs.load_state(record.review_state), PAGE_LENGTH_KEY, "page-length", "shortened",
                undo_sections=[{"section_id": c["section_id"], "before": c["before"]} for c in changes],
            )
            await _save_state(record, state, db)
        return ActionOutcome(record=record, changes=changes, still_listed=_letter_page_check(record) is not None)


async def keep_length(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """Page card → "{n} Seiten behalten" (letter): decision ``kept`` under
    ``page:page-overflow``; the re-audit lets WP-B's letter audit decide the
    row (→ class S), so the card leaves in this request."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_actions.keep_length")
    async with rs.document_lock(kind, doc_id):
        # Ownership first (another user's id is a 404, never a 409), then the kind.
        record = await load_document(kind, doc_id, db, user_id=owner)
        if kind != "cover_letter":
            raise ResolverNotApplicable("the CV's page card decides through /page-overflow")
        if _letter_page_check(record) is None:
            raise ResolverNotApplicable("the letter is not over its page target")
        state = rs.with_decision(rs.load_state(record.review_state), PAGE_LENGTH_KEY, "page-length", "kept")
        await _save_state(record, state, db)
        await reaudit(kind, record, db)
        return ActionOutcome(record=record, still_listed=_letter_page_check(record) is not None)


def removal_fold(text: str, *, user_id: uuid.UUID | None = None) -> str:
    """The key of one removal-line item: the fold of its removed wording."""
    return rs.norm_quote(text)


def _listed_removal(record, fold: str) -> dict | None:
    report = record.ats_report if isinstance(record.ats_report, dict) else {}
    for r in ((report.get("delivery") or {}).get("removals") or []):
        if isinstance(r, dict) and removal_fold(str(r.get("text") or "")) == fold:
            return r
    return None


async def removal_evidence(kind: Kind, doc_id: uuid.UUID, removal_text: str, text: str, db: AsyncSession, provider, *, user_id: uuid.UUID | None = None) -> ActionOutcome:
    """"Doch, stimmt – wo?" on one item of the removal line (ADR-099 cl. 5 /
    UAT-3i): testimony through the reconcile door (ADR-061, the same
    ``submit_testimony`` as ``add_evidence``); the outcome is recorded per item.
    The document is not changed — the NEXT version may use what the vault now
    holds."""
    from applire.services.owner_resolution import resolve_owner
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    owner = resolve_owner(user_id, site="review_actions.removal_evidence")
    fold = removal_fold(removal_text)
    if not fold:
        raise ValueError("removal_text is empty")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        if _listed_removal(record, fold) is None:
            raise FindingNotListed(f"no removal {removal_text!r} on the current report")
        row = _listed_removal(record, fold) or {}
        testimony = await submit_testimony(text, db, provider, user_id=owner)
        # D-9: the reconcile judges by meaning ("das steht schon in deinem
        # Profil"), the removal's producer by the skill's own words. Ask the
        # PRODUCER again, on the profile as it now stands, so the answer's
        # outcome is the one the next version will meet — never a dead end.
        backed = None
        if str(row.get("kind") or "") == SKILL_REMOVAL_KIND and testimony.status != "error":
            backed = await skill_backed_now(record, str(row.get("text") or removal_text), db, user_id=owner)
        state = rs.with_removal_answer(rs.load_state(record.review_state), fold, testimony.status, backed=backed)
        await _save_state(record, state, db)
        return ActionOutcome(record=record, testimony=testimony)


#: D-9: the removal kind whose producer is the Oracle's skill grounding.
SKILL_REMOVAL_KIND = "oracle:skillNoEvidence"


async def skill_backed_now(record, skill: str, db: AsyncSession, *, user_id: uuid.UUID) -> bool:
    """Would the Oracle ground ``skill`` on the profile as it stands NOW? The
    producer's own deterministic arm (``ground_skill_claim`` over the claimable
    vault + the job's claimable ledger forms, as ``audit.py`` reads them). The
    cross-language equivalence judgement (ADR-068 Seam A) is a model call and is
    not re-run here — a miss it would have rescued reads as not backed. Never
    raises: a failure is "not backed" (the removal stands, the copy says so)."""
    try:
        from applire.services.gap import _latest_gap_analysis
        from applire.services.oracle.audit import _claimable_index
        from applire.services.oracle.matchers.grounding import ground_skill_claim
        from applire.services.profile import _get_latest

        profile = await _get_latest(db, user_id)
        if profile is None or not skill.strip():
            return False
        forms = None
        gap = await _latest_gap_analysis(record.job_analysis_id, db, user_id=user_id)
        if gap is not None and gap.keyword_ledger:
            from applire.services.keyword_ledger import claimable_surface_form_groups

            forms = claimable_surface_form_groups(gap.keyword_ledger, exclude_keyword_only=True) or None
        return ground_skill_claim(skill, _claimable_index(profile.profile_json), forms) is not None
    except Exception:  # noqa: BLE001
        logger.exception("removal_evidence: skill re-grounding failed — read as not backed")
        return False


def remove_only(record, key: str, *, user_id: uuid.UUID | None = None) -> bool:
    """ADR-099 cl. 6: after a "Stimmt das?" answer whose testimony changed
    nothing (``no_change``) — or changed the vault without clearing it
    (``added`` while still listed) — the finding offers only "Entfernen": True
    when the latest decision on ``key`` is one of those and it is listed."""
    if rs.find_listed(findings_of(record), key) is None:
        return False
    d = rs.get_decision(rs.load_state(record.review_state), key)
    return bool(d and d.get("action") in ("no_change", "added"))
