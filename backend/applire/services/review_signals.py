# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#702 (ADR-060 amended 2026-10-07; ADR-090 cl. 6) — the *So lassen* / *Keep it as
is* decision on a cross-document item.

A cross-document item (``OutcomeCriticReport.cross_document``) is keyed
``critic:<_norm_quote(letter sentence)>``. Keeping it records the decision ``kept``
in the document's ``review_state`` — server-side, per generated document, readable by
every door (ADR-090 cl. 7: losing it would change what the user is shown to do next).
Nothing in the document changes; no re-audit is needed.

Only an item the document's LIVE critic report lists can be kept (a foreign or stale
key is ``FindingNotListed`` → 409), mirroring ADR-090's "only a finding this document
actually had may become a decision". Un-keeping removes the decision again.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.services import review_state as rs
from applire.services.review_actions import (
    ActionOutcome,
    FindingNotListed,
    Kind,
    load_document,
)


def cross_document_items(record) -> list:
    """The live cross-document items of a document's persisted critic report —
    derived through the schema (never read from the stored blob). Empty for a CV
    (Pass A has no letter), a NULL or malformed report."""
    from applire.schemas.outcome_critic import OutcomeCriticReport

    raw = getattr(record, "critic_report", None)
    if not isinstance(raw, dict):
        return []
    try:
        return OutcomeCriticReport.model_validate(raw).cross_document
    except Exception:
        return []


#: R-2b: the one non-``critic`` key *keep* accepts. Producer ``send`` (NOT ``ats``:
#: ``ats`` is a group-1 producer and would inflate "k of n decided").
TERMINAL_REVIEW_KEY = "send:terminal-review"
_UNVERIFIED_KEYS = ("terminal-review-unverified", "terminal-review-unverified-clean")
#: WP-C (GARH): the reviewed final draft with open points — the person decides.
_OPEN_KEYS = ("terminal-review-open",)
#: MD-G9 (adv/holds H-1/H-2): no verdict read the delivered draft.
_INCOMPLETE_KEYS = ("terminal-review-incomplete",)
#: MD-G9: every terminal-review state that holds the download until the person
#: decides. NOT held, by design: clean / observations / clean-code-open (a verdict
#: read the delivered draft and found nothing blocking), unnamed (a rejection that
#: named nothing: nothing to decide), not-run (no terminal review configured or
#: recorded — a legacy row, or LLM_REVIEW_MAX_RETRIES / CV_TERMINAL_REVIEW_MAX_RETRIES
#: = 0 by the operator). See the state table in the WP-C report.
_HELD_KEYS = _OPEN_KEYS + _INCOMPLETE_KEYS + _UNVERIFIED_KEYS


def unverified_terminal_check(record) -> dict | None:
    """The document's persisted ``terminal-review`` check when it reads *changed
    after the last check* (an unverified state), else None."""
    return _terminal_check(record, _UNVERIFIED_KEYS)


def decidable_terminal_check(record) -> dict | None:
    """The persisted ``terminal-review`` check when the person can decide on it:
    an unverified state (R-2b), open points on the reviewed final draft (WP-C) or a
    review that read no delivered draft (MD-G9)."""
    return _terminal_check(record, _HELD_KEYS)


class DeliveryHeld(Exception):
    """WP-C (C-1, recommendation B): the document's download is held until the person
    has decided on the open points of its reviewed final draft. The REST door maps it
    to 409 ``open_points_undecided``."""

    code = "open_points_undecided"

    def __init__(self, count: int, open_points_hash: str | None):
        super().__init__(f"{count} open point(s) on the reviewed final draft are undecided")
        self.count = count
        self.open_points_hash = open_points_hash


def held_detail(exc: DeliveryHeld) -> dict:
    """The 409 body every download door answers with."""
    return {"code": exc.code, "count": exc.count}


def link_or_bearer(request) -> bool:
    """MD-G9 (adv/holds D-1): the request reached a document GET with a signed link
    or a bearer — a download door (print-to-PDF of the html is the PDF). Only the
    person's own session (the in-app preview, the review surface) reads a held
    document's html."""
    from applire.auth.tokens import bearer_from_request

    return bool(getattr(request.state, "signed_link", False)) or bearer_from_request(request) is not None


class AuditPending(DeliveryHeld):
    """MD-G9 (adv/holds D-4): the saved content's re-audit has not landed yet — the
    reports still describe the previous text. The REST door maps it to 409
    ``audit_pending``; the client asks again in a moment. A :class:`DeliveryHeld`, so
    no door that holds can forget it."""

    code = "audit_pending"

    def __init__(self) -> None:
        super().__init__(0, None)
        self.args = ("the saved version is still being checked",)


class OpenPointsChanged(Exception):
    """MD-G9 (adv/holds D-9): a "Passt so" named a set of open points that is not the
    current one (a re-audit landed after the person read the card). → 409
    ``open_points_changed``."""

    def __init__(self, current: str | None):
        super().__init__("the open points changed since they were read")
        self.current = current


def decision_basis(check: dict) -> str | None:
    """What a "Passt so" on this check binds to: ``unreviewed_changes_hash`` for an
    unverified state (R-2b, ruling P-1), else ``open_points_hash`` — and, for a held
    row persisted before it carried one, a hash of the row's state and notes."""
    if check.get("details_key") in _UNVERIFIED_KEYS:
        return check.get("unreviewed_changes_hash") or None
    h = check.get("open_points_hash")
    if h:
        return h
    import hashlib
    import json

    payload = json.dumps(
        {"state": check.get("details_key"), "notes": check.get("notes") or []},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _decided(record, check: dict) -> bool:
    decision = rs.get_decision(rs.load_state(getattr(record, "review_state", None)), TERMINAL_REVIEW_KEY)
    return (
        decision is not None
        and decision.get("action") == "kept"
        and (decision.get("basis") or None) == decision_basis(check)
    )


def open_points_pending(record) -> dict | None:
    """The document's open-points check while UNDECIDED, else None — THE predicate of
    the C-1 / L-1 / MD-G9 hold (download, Word export, signed html, the agent door).

    Decided = a ``kept`` decision on :data:`TERMINAL_REVIEW_KEY` whose ``basis`` is the
    check's :func:`decision_basis`: a different set of open points (a new version, a
    re-audit that found more) voids it, exactly as the review surface's card reads it."""
    check = open_points_check(record)
    if check is None or _decided(record, check):
        return None
    return check


def open_points_count(check: dict) -> int:
    """How many open points the check names (``details_params.count``, else its notes)."""
    raw = (check.get("details_params") or {}).get("count")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else max(1, len(check.get("notes") or []))


def hold_if_open_points_pending(record) -> None:
    """Raise :class:`AuditPending` while the saved content's re-audit is running, and
    :class:`DeliveryHeld` while the record's open points are undecided."""
    from applire.services import audit_state

    if _held_document(record) and audit_state.pending(record):
        raise AuditPending()
    check = open_points_pending(record)
    if check is not None:
        raise DeliveryHeld(open_points_count(check), decision_basis(check))


#: How long a download door waits for a running re-audit before answering 409
#: ``audit_pending`` (the edit door's re-audit takes ~10 s on a real provider).
AUDIT_WAIT_SECONDS = 25.0


async def hold_for_delivery(kind: str, record, db) -> None:
    """The download doors' hold (async): while the saved content's re-audit is
    pending, wait for it (bounded, :data:`AUDIT_WAIT_SECONDS`) and read the row again;
    then :func:`hold_if_open_points_pending`. MD-G9: pending never releases."""
    import asyncio
    import time

    from applire.services import audit_state

    deadline = time.monotonic() + AUDIT_WAIT_SECONDS
    while _held_document(record) and audit_state.pending(record) and time.monotonic() < deadline:
        # The background re-audit holds the document lock while it runs.
        async with rs.document_lock(kind, record.id):
            pass
        await db.refresh(record)
        if audit_state.pending(record):
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
            await db.refresh(record)
    hold_if_open_points_pending(record)


async def hold_document_download(kind: str, doc_id, db, *, user_id) -> None:
    """The hold for a door that reads only an id (the html door on a signed link or a
    bearer, MD-G9 D-1): load the owner's document, then :func:`hold_for_delivery`."""
    from applire.services.review_actions import load_document

    record = await load_document(kind, doc_id, db, user_id=user_id)
    await hold_for_delivery(kind, record, db)


# ── WP-L (RULING L-1, founder 2026-11-23; MD-G4): the letter joins the same hold ──
#
# A generated letter whose DELIVERED audit still lists counted group-1 rows
# (``stop_and_fix`` — the ATS ``present_unsupported`` terms and the flagged Oracle
# claims, exactly what the review panel counts) after the GARH round (LETTER_HELD) is
# held like a CV with open points: ONE card, ONE decision ("Passt so" on
# ``send:terminal-review``), ONE predicate. The points are read from the LIVE reports,
# never frozen: a point fixed through the existing review actions (an edit, "Entfernen",
# a station, evidence — each re-audits) leaves the list, and the hold ends when none is
# left. The decision binds to the hash of the points it saw, so a different set voids it.
# An agent-authored document (ADR-054, ``origin == "agent"``) ran no loop and is never
# held (adv/holds D-2 — an agent re-rendering a held document's content — is with the
# founder).
#
# MD-G9 (D-8): "one pattern" — the CV is held on its delivered truth report exactly
# as the letter is.

#: The card's line for a held statement (``ats.terminalReview.code.<key with - for .>``).
_TRUTH_POINT_KEYS = {
    "misattributed": "oracle.misattributed",
    "inflated": "oracle.inflated",
    "unbacked": "oracle.unbacked",
}
_TRUTH_POINT_POSTING_TERM = "oracle.posting_term"
#: MD-G9 card lines for a held state that is not a finding (``code.<key>``).
_STATE_NOTE_KEYS = {
    "terminal-review-incomplete": "review.incomplete",
    "terminal-review-unverified": "review.unverified",
    "terminal-review-unverified-clean": "review.unverified",
}
_NOT_RECHECKED_KEY = "audit.not_rechecked"


def _is_letter(record) -> bool:
    return hasattr(record, "letter_data")


def _held_document(record) -> bool:
    """ADR-054: an agent-authored document is never held (D-2 is the founder's)."""
    return getattr(record, "origin", None) != "agent"


def truth_points(record) -> list[dict]:
    """The document's open statements as card notes (``kind: code``), one per counted
    group-1 row of the LIVE reports — CV and letter alike (MD-G9, D-8); ``[]`` for an
    agent-authored document, an unavailable truthfulness report (an audit that did not
    run holds through :mod:`audit_state`, not here) or a document whose check passes."""
    if not _held_document(record):
        return []
    from applire.services.truthfulness_summary import summarize

    truth = getattr(record, "truthfulness_report", None)
    ats = getattr(record, "ats_report", None)
    if summarize(truth, ats).stop_and_fix is not True:
        return []
    verdicts: dict[str, str] = {}
    for c in (truth or {}).get("claims") or []:
        claim = (c or {}).get("claim") or {}
        v = ((c or {}).get("verdict") or {}).get("verdict")
        if isinstance(v, str):
            verdicts.setdefault(rs.norm_quote(claim.get("text")), v)
    points: list[dict] = []
    for f in rs.group_one_findings(ats if isinstance(ats, dict) else None, truth):
        if f.employer_sentence:  # listed, never counted (#803) — never held either
            continue
        if f.claim_text:
            key = _TRUTH_POINT_KEYS.get(verdicts.get(rs.norm_quote(f.claim_text)) or "", "oracle.unbacked")
            subject = f.claim_text
        else:
            key, subject = _TRUTH_POINT_POSTING_TERM, f.label
        points.append({
            "kind": "code", "key": key, "subjects": [subject[:400]],
            "text": subject[:400], "finding_key": f.key,
        })
    return points


def letter_truth_points(record) -> list[dict]:
    """WP-L's name for :func:`truth_points` on a letter (``[]`` for a CV)."""
    return truth_points(record) if _is_letter(record) else []


def _points_hash(base_hash: str | None, finding_keys: list[str]) -> str:
    import hashlib
    import json

    payload = json.dumps({"terminal": base_hash, "truth": sorted(finding_keys)}, sort_keys=True,
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _audit_points(record) -> list[dict]:
    """MD-G9 (D-3/D-4/D-5): no audit describes the shipped content and none is coming
    — the hold that stood before carries over, plus one line that says so."""
    from applire.services import audit_state

    if not _held_document(record):
        return []
    st = audit_state.unchecked(record)
    if st is None:
        return []
    carried = [dict(n) for n in (st.get("carried") or []) if isinstance(n, dict)]
    reason = str(st.get("reason") or "audit_failed")
    line = {
        "kind": "code", "key": _NOT_RECHECKED_KEY, "subjects": [reason],
        "text": (
            "Applire could not check this version of the document "
            f"({reason}); what it said before stays open."
        ),
        "finding_key": f"audit:{str(st.get('basis') or '')[:16]}:{st.get('carried_basis') or ''}",
    }
    for n in carried:
        n["finding_key"] = f"carried:{n.get('key') or n.get('kind')}:{rs.norm_quote(n.get('text'))}"
    return [line] + carried


def open_points_check(record) -> dict | None:
    """THE open-points check of a document — None when nothing holds it.

    * the persisted ``terminal-review`` row in a held state (:data:`_HELD_KEYS`), as
      persisted when nothing else is open (a CV's or letter's hash unchanged);
    * joined with the open statements of the delivered truth report (CV and letter,
      :func:`truth_points`) and the audit-state lines (:func:`_audit_points`) into ONE
      ``terminal-review-open`` check whose ``open_points_hash`` covers all of them."""
    if not _held_document(record):
        return None
    base = _terminal_check(record, _HELD_KEYS)
    extra = _audit_points(record) + truth_points(record)
    if not extra:
        if base is None:
            return None
        served = dict(base)
        if base.get("details_key") not in _UNVERIFIED_KEYS and not base.get("open_points_hash"):
            served["open_points_hash"] = decision_basis(base)
        return served
    base_key = base.get("details_key") if base else None
    base_notes = [n for n in (base.get("notes") or []) if isinstance(n, dict)] if base else []
    state_note = []
    if base_key in _STATE_NOTE_KEYS:
        state_note = [{
            "kind": "code", "key": _STATE_NOTE_KEYS[base_key], "subjects": [],
            "text": (
                "No review read the delivered version." if base_key in _INCOMPLETE_KEYS
                else "The document changed after its last review."
            ),
        }]
    # one line per statement: a carried note the live reports still list is not doubled
    seen: set[tuple] = set()
    notes: list[dict] = []
    finding_keys: list[str] = []
    for n in state_note + base_notes + extra:
        sig = (n.get("kind"), n.get("key"), rs.norm_quote(n.get("text")))
        fk = n.pop("finding_key", None) if "finding_key" in n else None
        if sig in seen and str(fk or "").startswith("carried:"):
            continue
        seen.add(sig)
        notes.append(n)
        if fk:
            finding_keys.append(fk)
    base_count = open_points_count(base) if base_key in _OPEN_KEYS else (1 if base_key else 0)
    count = base_count + len(finding_keys)
    statements = "; ".join(f'"{n["text"]}"' for n in notes if n.get("kind") == "code")
    doc = "letter" if _is_letter(record) else "CV"
    return {
        "id": "terminal-review",
        "status": "fail",
        "details": (
            f"The final check still finds {count} open point(s) on the delivered {doc} that "
            f"Applire could not resolve: {statements}. The PDF and Word download stay held "
            f"until the person keeps them as they are or changes the {doc}."
        ),
        "details_key": "terminal-review-open",
        "details_params": {"count": count},
        "notes": notes,
        "delivery_class": "A",
        "resolver": "decide_open",
        "open_points_hash": _points_hash(decision_basis(base) if base else None, finding_keys),
    }


def with_open_points_check(report: dict | None, record) -> dict | None:
    """The persisted ATS report as the doors serve it: its ``terminal-review`` row is
    :func:`open_points_check` whenever the document is held (the persisted row is
    never rewritten — the points stay live). A NULL report of a held document is
    served as that one row (adv/holds N-3: the card stays reachable). Every other
    report is returned unchanged."""
    check = open_points_check(record)
    if check is None:
        return report
    if not isinstance(report, dict):
        return None
    checks = list(report.get("checks") or [])
    served = dict(report)
    idx = next(
        (i for i, c in enumerate(checks) if isinstance(c, dict) and c.get("id") == "terminal-review"),
        None,
    )
    old_status = checks[idx].get("status") if idx is not None else None
    if idx is None:
        checks.append(check)
    else:
        checks[idx] = check
    # the convenience counts follow the row they count (never a score, ADR-039)
    if old_status != "fail" and check.get("status") == "fail":
        if old_status == "pass" and isinstance(served.get("passed"), int):
            served["passed"] = max(0, served["passed"] - 1)
        elif old_status == "not_applicable" and isinstance(served.get("not_applicable"), int):
            served["not_applicable"] = max(0, served["not_applicable"] - 1)
        if isinstance(served.get("failed"), int):
            served["failed"] = served["failed"] + 1
    served["checks"] = checks
    return served


def _terminal_check(record, keys: tuple[str, ...]) -> dict | None:
    report = getattr(record, "ats_report", None)
    checks = report.get("checks") if isinstance(report, dict) else None
    if not isinstance(report, dict):
        # MD-G9 (D-5): an ATS engine failure left the report NULL — the row about the
        # review is kept beside it (audit_state.terminal), never erased.
        from applire.services import audit_state

        row = audit_state.terminal_row(record)
        checks = [row] if row else []
    for c in checks or []:
        if isinstance(c, dict) and c.get("id") == "terminal-review":
            return c if c.get("details_key") in keys else None
    return None


async def keep(
    kind: Kind,
    doc_id: uuid.UUID,
    key: str,
    db: AsyncSession,
    *,
    keep: bool = True,
    user_id: uuid.UUID | None = None,
    basis: str | None = None,
    check_basis: bool = False,
) -> ActionOutcome:
    """Record (``keep=True``) or withdraw (``keep=False``) the ``kept`` decision on a
    cross-document item, or on the terminal-review row (``send:terminal-review``,
    R-2b "Passt so"). ValueError (→ 422) on any other key;
    FindingNotListed (→ 409) when the live report does not list the item.

    MD-G9 (adv/holds D-9): with ``check_basis`` (the REST and agent doors), a "Passt
    so" on the terminal review names the ``basis`` the client read; a different
    current basis is :class:`OpenPointsChanged` (→ 409 ``open_points_changed``) —
    a click on a card rendered before a re-audit landed never keeps points the person
    did not see."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_signals.keep")
    # ADR-092: the ownership check precedes everything else, so a foreign id is a
    # 404 whatever the key looks like.
    await load_document(kind, doc_id, db, user_id=owner)
    producer, norm = rs.split_key(key)
    if key != TERMINAL_REVIEW_KEY and producer != "critic":
        raise ValueError(f"not a cross-document key: {key!r}")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        state = rs.load_state(record.review_state)
        if key == TERMINAL_REVIEW_KEY:
            if keep:
                # WP-L: a held letter's open points (joined check) first; a CV reads
                # exactly the check it read before (open_points_check == its own row).
                check = open_points_check(record)
                if check is None:
                    raise ValueError(
                        f"{key!r} exists only on an unverified or open terminal review"
                    )
                current = decision_basis(check)
                if check_basis and (basis or None) != current:
                    raise OpenPointsChanged(current)
                state = rs.with_decision(
                    state, TERMINAL_REVIEW_KEY, "terminal-review", "kept",
                    basis=current,
                    with_basis=True,
                )
            else:
                state = rs.without_decision(state, TERMINAL_REVIEW_KEY)
        elif keep:
            item = next(
                (i for i in cross_document_items(record) if rs.split_key(i.key)[1] == norm),
                None,
            )
            if item is None:
                raise FindingNotListed(f"{key!r} is not listed on this document's critic report")
            state = rs.with_decision(state, item.key, item.letter_state[:300], "kept")
        else:
            state = rs.without_decision(state, key)
        record.review_state = state
        await db.commit()
        await db.refresh(record)
        return ActionOutcome(record=record)


def cross_document_target(record, key: str) -> tuple[str, list[str]]:
    """#702 RULING R-2 = A — what *Aus dem Anschreiben nehmen* removes for a
    cross-document item: the item's LETTER-ONLY concepts that literally stand in its
    letter sentence (``ats_audit.surface_present``, the shared presence predicate) —
    so the rewrite keeps the sentence and drops the facts the CV does not carry ("Applire schreibt den Satz ohne diese Angaben neu"). When no
    concept label is literally in the sentence (a paraphrased label), the whole
    sentence is the wording. Returns ``(label, wording)``; FindingNotListed when the
    live critic report does not list the item."""
    from applire.schemas.outcome_critic import OutcomeCriticReport
    from applire.services.ats_audit import _norm, surface_present

    _, norm = rs.split_key(key)
    raw = getattr(record, "critic_report", None)
    report = None
    if isinstance(raw, dict):
        try:
            report = OutcomeCriticReport.model_validate(raw)
        except Exception:
            report = None
    item = None
    if report is not None:
        item = next((i for i in report.cross_document if rs.split_key(i.key)[1] == norm), None)
    if item is None:
        raise FindingNotListed(f"{key!r} is not listed on this document's critic report")
    sentence = _norm(item.letter_state)
    # Adv-review finding 6 (2026-10-07): ONLY the letter-only facts the card shows
    # and counts. A letter_richer concept is CV-backed — handing it to the removal
    # rewrite stripped a true achievement ("… leitete ich die an 14 …").
    letter_only = item.letter_only or [
        a.concept for a in report.advisories
        if a.kind == "letter_only" and a.concept in item.concepts
    ]
    wording = [c for c in dict.fromkeys(letter_only) if surface_present(c, sentence)]
    return item.letter_state, (wording or [item.letter_state])


def locate_sentence(sections: list[tuple[str, str]], sentence: str) -> tuple[str, str, int, int] | None:
    """Adv-review finding 7 — where the item's letter sentence stands:
    ``(section_id, section_text, start, end)``, or None when the document no longer
    holds it. Exact first, then whitespace-tolerant (the critic quotes across a
    line break as a space)."""
    import re

    sentence = (sentence or "").strip()
    if not sentence:
        return None
    for sid, text in sections:
        i = (text or "").find(sentence)
        if i >= 0:
            return sid, text, i, i + len(sentence)
    pattern = r"\s+".join(re.escape(tok) for tok in sentence.split())
    for sid, text in sections:
        m = re.search(pattern, text or "")
        if m:
            return sid, text, m.start(), m.end()
    return None
