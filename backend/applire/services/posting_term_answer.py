# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — the person's answer to the posting-terms tick list.

ADR-046 amended 2026-11-17 (rulings Q10-4, Q10-6). Two doors, one service:

* the web card before the CV (``via="person"``): every term shown is answered —
  a ticked term becomes a profile skill, an unticked one is recorded on THIS
  application (``applications.posting_terms.declined``) and not asked again;
* the agent (``via="agent"``): ``confirmed`` terms become profile skills with the
  agent origin recorded; ``leave_open`` lets the generation proceed WITHOUT
  recording an answer — the open terms are never printed as skills and the web
  card still asks.

A ticked term is a NEW skill (Q10-1 recommendation A, measured: the near-dupe
reading never fires for a candidate by construction — a near-dupe of a vault
skill already counts as the person's word). The write is the manual section
edit (``patch_profile_section``), so the committer's trail, the denial floor
and the completeness recompute are the ones every edit gets.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.services.posting_terms import offered_posting_term_rows, same_language_pair, their_words

logger = logging.getLogger(__name__)

Via = Literal["person", "agent"]


@dataclass
class PostingTermsState:
    #: the terms still to answer: [{"term", "their_words"}], posting order
    terms: list[dict[str, Any]] = field(default_factory=list)
    #: the terms the person left unticked for this application
    declined: list[str] = field(default_factory=list)
    #: the terms the agent left open (generation proceeds; nothing answered)
    left_open: list[str] = field(default_factory=list)
    #: the terms the last answer added to the profile (the receipt)
    confirmed: list[str] = field(default_factory=list)
    answered_at: str | None = None
    language: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "terms": self.terms, "declined": self.declined, "left_open": self.left_open,
            "confirmed": self.confirmed, "answered_at": self.answered_at,
            "language": self.language,
        }

    @property
    def agent_must_ask(self) -> list[dict[str, Any]]:
        """The terms that hold an agent's ``generate_cv`` back: open ones it has
        not left open."""
        left = {_key(t) for t in self.left_open}
        return [t for t in self.terms if _key(t["term"]) not in left]


def _key(term: str) -> str:
    return " ".join(str(term).split()).casefold()


def _record(application: Any) -> dict[str, Any]:
    raw = getattr(application, "posting_terms", None)
    return dict(raw) if isinstance(raw, dict) else {}


async def _load(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID):
    from applire.models.gap import GapAnalysis
    from applire.services.job import caller_link, get_job_for_user
    from applire.services.profile import get_profile_for_user

    job = await get_job_for_user(db, job_id, user_id)
    application = await caller_link(db, job.id, user_id)
    profile = await get_profile_for_user(db, user_id)
    gap = (
        await db.execute(
            select(GapAnalysis)
            .where(
                GapAnalysis.job_analysis_id == job.id,
                GapAnalysis.user_id == user_id,
                GapAnalysis.deleted_at.is_(None),
            )
            .order_by(GapAnalysis.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return job, application, profile, gap


def compute_state(
    ledger: list[dict[str, Any]] | None,
    profile_json: dict[str, Any] | None,
    document_language: str | None,
    record: dict[str, Any] | None,
    *,
    job_id: Any = None,
) -> PostingTermsState:
    """Pure: the list the card shows, from the LAST full classification read
    against the CURRENT vault, minus what this application already declined.
    Empty across languages (the literal reading cannot see a translation)."""
    record = record or {}
    declined = [t for t in record.get("declined") or [] if isinstance(t, str)]
    left_open = [t for t in record.get("left_open") or [] if isinstance(t, str)]
    state = PostingTermsState(
        declined=declined, left_open=left_open,
        confirmed=[t for t in record.get("confirmed") or [] if isinstance(t, str)],
        answered_at=record.get("answered_at"), language=document_language,
    )
    if not same_language_pair(profile_json, document_language, seam="posting terms", job_id=job_id):
        return state
    gone = {_key(t) for t in declined}
    for row in offered_posting_term_rows(ledger, profile_json):
        term = row["concept"].strip()
        if _key(term) in gone or any(_key(t["term"]) == _key(term) for t in state.terms):
            continue
        state.terms.append({"term": term, "their_words": their_words(row, profile_json)})
    return state


async def posting_terms_state(
    db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID
) -> PostingTermsState:
    """The read: never persists (the CV seam owns the ledger write)."""
    from applire.services.keyword_ledger import refresh_ledger_against_vault
    from applire.utils.language_detection import resolve_document_language

    job, application, profile, gap = await _load(db, job_id, user_id)
    profile_json = profile.profile_json if profile is not None else None
    ledger = []
    if gap is not None:
        ledger, _ = refresh_ledger_against_vault(
            gap.keyword_ledger, profile_json, seam="posting terms"
        )
    return compute_state(
        ledger, profile_json, resolve_document_language(application, job), _record(application),
        job_id=job_id,
    )


def _posting_label(job: Any, application: Any) -> str | None:
    """The posting as the person's own card names it — the company (mock T3:
    "Wortlaut aus der Stellenanzeige NovaPay"), the role when no company is known."""
    for value in (
        getattr(application, "company_name", None), getattr(job, "company_name", None),
        getattr(application, "role_title", None), getattr(job, "role_title", None),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def add_confirmed_skills(
    skills: list[dict[str, Any]],
    terms: list[str],
    *,
    via: Via,
    posting: str | None,
    job_id: str,
    today: date,
    shown: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure: the skills section with each confirmed term as a NEW confirmed skill
    carrying its confirmation. A term already a skill name (case-blind) gets the
    confirmation only — never a second entry. A term the existing near-duplicate
    check calls close to a held skill becomes that skill's second name (ruling
    Q10-1 = A: "close" is ``ats_audit.skills_near_dupe``, unchanged). In practice
    this branch is rare: a near-dupe of a held skill is already the person's word
    and is not asked, so it fires only when the skills changed in between.

    Ruling Q10-8: *shown* maps a term to the words the card showed beside it; the
    confirmation records which of them are EXACTLY a profile skill name
    (``shown_skills``, minus the skill the confirmation lands on) — that posting's
    CV prints the term in place of them."""
    from applire.services.ats_audit import skills_near_dupe
    from applire.services.posting_terms import card_skill_names

    out = [dict(s) if isinstance(s, dict) else s for s in skills]
    added: list[str] = []
    for term in terms:
        conf = {"name": term, "via": via, "on": today.isoformat(), "posting": posting, "job_id": job_id}
        existing = next(
            (s for s in out if isinstance(s, dict) and _key(s.get("name") or "") == _key(term)), None
        )
        close = None if existing is not None else next(
            (s for s in out if isinstance(s, dict) and s.get("name")
             and skills_near_dupe(term, str(s["name"]))), None
        )
        holder = (existing or close or {}).get("name") or term
        card = card_skill_names((shown or {}).get(term), skills, holder=holder)
        if card:
            conf["shown_skills"] = card
        if close is not None:
            aliases = list(close.get("aliases") or [])
            if _key(term) not in {_key(a) for a in aliases}:
                aliases.append(term)
            close["aliases"] = aliases
            close["confirmations"] = [*(close.get("confirmations") or []), conf]
        elif existing is not None:
            existing["confirmations"] = [*(existing.get("confirmations") or []), conf]
            if existing.get("status") != "confirmed":
                existing["status"] = "confirmed"
        else:
            out.append({"name": term, "category": "technical", "status": "confirmed",
                        "confirmations": [conf]})
        added.append(term)
    return out, added


class UnknownTerms(ValueError):
    """A term the caller answered is not on the list (the card's or the agent's)."""

    def __init__(self, terms: list[str]) -> None:
        super().__init__(f"not on the list: {', '.join(terms)}")
        self.terms = terms


async def answer_posting_terms(
    db: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    confirmed: list[str],
    via: Via,
    leave_open: bool = False,
) -> PostingTermsState:
    """THE write for both doors.

    ``via="person"`` (the card): ``confirmed`` = the ticked terms; every other
    term on the list is declined for this application. Nothing ticked is a valid
    answer. ``via="agent"``: ``confirmed`` = what the person told the agent;
    ``leave_open=True`` records the rest as left open by the agent (generation
    may proceed, the card still asks) — and records NO answer."""
    from applire.services.profile import patch_profile_section

    job, application, profile, _gap = await _load(db, job_id, user_id)
    state = await posting_terms_state(db, job_id, user_id)
    by_key = {_key(t["term"]): t["term"] for t in state.terms}
    unknown = [t for t in confirmed if _key(t) not in by_key]
    if unknown:
        raise UnknownTerms(unknown)
    chosen = list(dict.fromkeys(by_key[_key(t)] for t in confirmed))

    added: list[str] = []
    if chosen:
        if profile is None:
            raise LookupError("No profile found")
        skills = list((profile.profile_json or {}).get("skills") or [])
        new_skills, added = add_confirmed_skills(
            skills, chosen, via=via, posting=_posting_label(job, application),
            job_id=str(job.id), today=datetime.now(timezone.utc).date(),
            # Ruling Q10-8: the words THIS answer's list showed beside each term.
            shown={t["term"]: list(t.get("their_words") or []) for t in state.terms},
        )
        await patch_profile_section(
            "skills", new_skills, db,
            source="agent_interview" if via == "agent" else "manual_edit",
            user_id=user_id,
        )

    if application is not None:
        record = _record(application)
        rest = [t["term"] for t in state.terms if t["term"] not in chosen]
        if via == "person":
            record["declined"] = list(dict.fromkeys([*(record.get("declined") or []), *rest]))
            record["left_open"] = []
            record["answered_at"] = datetime.now(timezone.utc).isoformat()
            record["via"] = via
        elif leave_open:
            record["left_open"] = list(dict.fromkeys([*(record.get("left_open") or []), *rest]))
        record["confirmed"] = added
        application.posting_terms = record
        await db.commit()
    logger.info(
        "POSTING_TERMS_ANSWER job=%s via=%s confirmed=%s leave_open=%s",
        job_id, via, added, leave_open,
    )
    return await posting_terms_state(db, job_id, user_id)


# ── the confirmation record on the agent door (ADR-084, Sorbet WP-Q10) ─────


def posting_label_paths(payload: Any) -> list[str]:
    """The field paths at which *payload* carries a confirmation's ``posting``
    label — the job posting's company or role name, i.e. untrusted posting text
    (ADR-084). Paths use the ``untrusted_content`` notation (``a.b[].c``), so any
    agent-door payload that echoes a skill (``get_profile``, ``update_profile``,
    a testimony change's old/new value, ``profile://current``) can be marked
    without each door knowing where the skill sits."""
    found: list[str] = []

    def walk(node: Any, path: str, under_confirmations: bool) -> None:
        if isinstance(node, dict):
            if under_confirmations and node.get("posting"):
                p = f"{path}.posting" if path else "posting"
                if p not in found:
                    found.append(p)
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, f"{path}.{k}" if path else str(k), k == "confirmations")
        elif isinstance(node, list):
            for v in node:
                walk(v, f"{path}[]", under_confirmations)

    walk(payload, "", False)
    return found


def keep_stored_confirmations(incoming: Any, stored_skills: Any) -> Any:
    """The agent door's ``update_profile('skills', …)``: the confirmation record
    is not agent-writable. Every incoming skill carries exactly the confirmations
    stored for a skill of the same name (none for a new name) — an agent can
    neither forge "confirmed by you" nor erase a confirmation by round-tripping a
    read that dropped it. ``confirm_posting_terms`` is the agent's only way in."""
    if not isinstance(incoming, list):
        return incoming
    stored: dict[str, list] = {}
    for s in stored_skills or []:
        if isinstance(s, dict) and s.get("confirmations"):
            stored.setdefault(_key(str(s.get("name") or "")), []).extend(s["confirmations"])
    out = []
    for s in incoming:
        if isinstance(s, str) and stored.get(_key(s)):
            s = {"name": s}  # the bare-name shape the schema coerces anyway
        if not isinstance(s, dict):
            out.append(s)
            continue
        kept = {k: v for k, v in s.items() if k != "confirmations"}
        held = stored.get(_key(str(s.get("name") or "")))
        if held:
            kept["confirmations"] = list(held)
        out.append(kept)
    return out
