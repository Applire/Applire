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


def unverified_terminal_check(record) -> dict | None:
    """The document's persisted ``terminal-review`` check when it reads *changed
    after the last check* (an unverified state), else None."""
    return _terminal_check(record, _UNVERIFIED_KEYS)


def decidable_terminal_check(record) -> dict | None:
    """The persisted ``terminal-review`` check when the person can decide on it:
    an unverified state (R-2b) or open points on the reviewed final draft (WP-C)."""
    return _terminal_check(record, _UNVERIFIED_KEYS + _OPEN_KEYS)


class DeliveryHeld(Exception):
    """WP-C (C-1, recommendation B): the document's download is held until the person
    has decided on the open points of its reviewed final draft. The REST door maps it
    to 409 ``open_points_undecided``."""

    def __init__(self, count: int, open_points_hash: str | None):
        super().__init__(f"{count} open point(s) on the reviewed final draft are undecided")
        self.count = count
        self.open_points_hash = open_points_hash


def open_points_pending(record) -> dict | None:
    """The persisted ``terminal-review`` check while its open points are UNDECIDED, else
    None — THE predicate of the C-1 hold (download, Word export, the agent door).

    Decided = a ``kept`` decision on :data:`TERMINAL_REVIEW_KEY` whose ``basis`` is the
    check's ``open_points_hash``: a different set of open points (a new version) voids
    it, exactly as the review surface's card reads it (``isOpenPointsAcknowledged``)."""
    check = open_points_check(record)
    if check is None:
        return None
    decision = rs.get_decision(rs.load_state(getattr(record, "review_state", None)), TERMINAL_REVIEW_KEY)
    if (
        decision is not None
        and decision.get("action") == "kept"
        and (decision.get("basis") or None) == (check.get("open_points_hash") or None)
    ):
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
    """Raise :class:`DeliveryHeld` while the record's open points are undecided."""
    check = open_points_pending(record)
    if check is not None:
        raise DeliveryHeld(open_points_count(check), check.get("open_points_hash"))


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
# An agent-authored letter (ADR-054, ``origin == "agent"``) ran no loop and is never held.

#: The card's line for a held letter statement (``ats.terminalReview.code.<key with - for .>``).
_TRUTH_POINT_KEYS = {
    "misattributed": "oracle.misattributed",
    "inflated": "oracle.inflated",
    "unbacked": "oracle.unbacked",
}
_TRUTH_POINT_POSTING_TERM = "oracle.posting_term"


def _is_letter(record) -> bool:
    return hasattr(record, "letter_data")


def letter_truth_points(record) -> list[dict]:
    """The held letter's open statements as card notes (``kind: code``), one per
    counted group-1 row of the LIVE reports; ``[]`` for a CV, an agent-authored letter,
    an unavailable truthfulness report (unknown is the U row's business, not a hold) or
    a letter whose check passes."""
    if not _is_letter(record) or getattr(record, "origin", None) == "agent":
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


def _points_hash(base_hash: str | None, finding_keys: list[str]) -> str:
    import hashlib
    import json

    payload = json.dumps({"terminal": base_hash, "truth": sorted(finding_keys)}, sort_keys=True,
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def open_points_check(record) -> dict | None:
    """THE open-points check of a document: the persisted ``terminal-review-open`` check
    (a CV, unchanged), or — for a held letter — that check joined with the letter's open
    statements (:func:`letter_truth_points`) into one ``terminal-review-open`` check whose
    ``open_points_hash`` covers both. None when nothing is open."""
    base = _terminal_check(record, _OPEN_KEYS)
    points = letter_truth_points(record)
    if not points:
        return base
    base_notes = [n for n in (base.get("notes") or []) if isinstance(n, dict)] if base else []
    count = (open_points_count(base) if base else 0) + len(points)
    finding_keys = [p.pop("finding_key") for p in points]
    statements = "; ".join(f'"{p["text"]}"' for p in points)
    return {
        "id": "terminal-review",
        "status": "fail",
        "details": (
            f"The final check still finds {count} open point(s) on the delivered letter that "
            f"Applire could not resolve: {statements}. The PDF and Word download stay held "
            "until the person keeps them as they are or changes the letter."
        ),
        "details_key": "terminal-review-open",
        "details_params": {"count": count},
        "notes": base_notes + points,
        "delivery_class": "A",
        "resolver": "decide_open",
        "open_points_hash": _points_hash(base.get("open_points_hash") if base else None, finding_keys),
    }


def with_open_points_check(report: dict | None, record) -> dict | None:
    """The persisted ATS report as the doors serve it: for a held letter its
    ``terminal-review`` row is :func:`open_points_check` (the persisted row is never
    rewritten — the points stay live). Every other report is returned unchanged."""
    if not isinstance(report, dict) or not letter_truth_points(record):
        return report
    check = open_points_check(record)
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
    if old_status != "fail":
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
    if not isinstance(report, dict):
        return None
    for c in report.get("checks") or []:
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
) -> ActionOutcome:
    """Record (``keep=True``) or withdraw (``keep=False``) the ``kept`` decision on a
    cross-document item, or on the terminal-review row (``send:terminal-review``,
    R-2b "Passt so"). ValueError (→ 422) on any other key;
    FindingNotListed (→ 409) when the live report does not list the item."""
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
                check = open_points_check(record) or decidable_terminal_check(record)
                if check is None:
                    raise ValueError(
                        f"{key!r} exists only on an unverified or open terminal review"
                    )
                state = rs.with_decision(
                    state, TERMINAL_REVIEW_KEY, "terminal-review", "kept",
                    basis=(
                        check.get("open_points_hash")
                        if check.get("details_key") in _OPEN_KEYS
                        else check.get("unreviewed_changes_hash")
                    ) or None,
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
