# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 clause 6 — review decisions persisted per generated document.

``review_state`` (a nullable JSON column on ``generated_cvs`` and
``generated_cover_letters``, migration 0069) has exactly this shape::

    {"walked_at": ts | null,
     "decisions": [{"finding_key", "label", "action": "added"|"taken_out"|"edited",
                    "at", "undo": {"sections": [{"section_id", "before"}]} | null}]}

**A decision never hides a finding.** Every count derives from the LIVE report:
a finding the current report lists is open, whatever ``review_state`` says; a
decision only LABELS a finding the report no longer lists; a decision whose
finding reappears is open again (:func:`derive_review`, SF-REVIEW.9). The
browser-local walked bit (ADR-081 clause 5a) is retired in favour of
``walked_at``.

``finding_key`` = ``"<producer>:<_norm_quote(term or claim text)>"`` with producer
``ats`` or ``oracle`` — WITHOUT the list index the frontend key carried
(``term-${i}-…``), which shifts when an earlier finding clears. An ADR-081 clause 2
merged row (an Oracle claim folding into an ATS term) is rendered under ``ats:``;
the backend accepts either producer for it, because identity is the normalised
text (two findings that normalise equal are one finding, ADR-081 clause 2).

Group 1 is computed here the way the review surface computes it
(``frontend/lib/review-groups.ts`` ``buildGroup1`` + ``truthfulness-display.ts``
``flaggedClaims``): ATS ``present_unsupported`` terms first, then every Oracle
claim with a flag verdict that is not a "related" skill claim, folded into the
term it normalises equal to.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from applire.services.scope_requirements import _norm_quote

Producer = Literal["ats", "oracle"]
Action = Literal["added", "taken_out", "edited"]

PRODUCERS: tuple[str, ...] = ("ats", "oracle")
ACTIONS: tuple[str, ...] = ("added", "taken_out", "edited")
# frontend lib/truthfulness-display.ts FLAG_VERDICTS
FLAG_VERDICTS: frozenset[str] = frozenset({"inflated", "misattributed", "unbacked"})


def norm_quote(s: str | None) -> str:
    """ADR-077 / ADR-070 clause 1 fold (``scope_requirements._norm_quote``)."""
    return _norm_quote(s or "")


def finding_key(producer: str, text: str) -> str:
    if producer not in PRODUCERS:
        raise ValueError(f"unknown producer {producer!r}")
    return f"{producer}:{norm_quote(text)}"


def split_key(key: str) -> tuple[str, str]:
    """``"ats:foo"`` → ``("ats", "foo")``. Raises ValueError on a malformed key.

    The text half is re-folded, so a key sent un-normalised still resolves."""
    if not isinstance(key, str) or ":" not in key:
        raise ValueError(f"malformed finding_key {key!r}")
    producer, _, text = key.partition(":")
    if producer not in PRODUCERS or not norm_quote(text):
        raise ValueError(f"malformed finding_key {key!r}")
    return producer, norm_quote(text)


@dataclass
class GroupOneFinding:
    """One rendered group-1 row."""

    key: str
    producer: str
    norm: str
    label: str
    #: ATS term: the matched forms from the report (ADR-090 cl. 2); ``None`` when
    #: the report predates the field. Oracle-only row: ``None``.
    matches: list[dict[str, Any]] | None = None
    #: The Oracle claim folded into this row (or the row's own claim).
    claim_text: str | None = None
    claim_location: str | None = None

    def wording(self) -> list[str]:
        """The document wording this finding stands on — what *take it out*
        removes and *show me where* searches: the matched forms of an ATS term
        (the term itself for a legacy report without matches), plus the folded
        claim's text; an Oracle-only row's claim text."""
        out: list[str] = []
        if self.producer == "ats":
            if self.matches:
                out.extend(m.get("form", "") for m in self.matches if m.get("form"))
            else:
                out.append(self.label)
        if self.claim_text:
            out.append(self.claim_text)
        seen: set[str] = set()
        uniq: list[str] = []
        for w in out:
            n = norm_quote(w)
            if n and n not in seen:
                seen.add(n)
                uniq.append(w)
        return uniq


def _keywords(ats_report: dict | None) -> dict:
    return (ats_report or {}).get("keywords") or {}


def _flagged_claims(truth_report: dict | None, claimable_concepts: list[str]) -> list[dict]:
    claimable = {str(c).strip().lower() for c in claimable_concepts or []}
    out = []
    for c in (truth_report or {}).get("claims") or []:
        verdict = ((c or {}).get("verdict") or {}).get("verdict")
        claim = (c or {}).get("claim") or {}
        if verdict not in FLAG_VERDICTS:
            continue
        related = (
            verdict == "unbacked"
            and claim.get("kind") == "skill"
            and str(claim.get("text") or "").strip().lower() in claimable
        )
        if not related:
            out.append(c)
    return out


def group_one_findings(ats_report: dict | None, truth_report: dict | None) -> list[GroupOneFinding]:
    """Group 1 exactly as the review surface renders it (see module docstring)."""
    kw = _keywords(ats_report)
    terms: list[str] = list(kw.get("present_unsupported") or [])
    matches_map = kw.get("present_unsupported_matches")
    claims = _flagged_claims(truth_report, list(kw.get("claimable_concepts") or []))
    claims_by_norm: dict[str, int] = {}
    for i, c in enumerate(claims):
        n = norm_quote(((c.get("claim") or {}).get("text")))
        claims_by_norm.setdefault(n, i)
    consumed: set[int] = set()
    rows: list[GroupOneFinding] = []
    seen_norms: set[str] = set()
    for term in terms:
        n = norm_quote(term)
        if not n or n in seen_norms:
            continue
        seen_norms.add(n)
        ci = claims_by_norm.get(n)
        claim = None
        if ci is not None and ci not in consumed:
            consumed.add(ci)
            claim = claims[ci].get("claim") or {}
        ms = matches_map.get(term) if isinstance(matches_map, dict) else None
        rows.append(GroupOneFinding(
            key=f"ats:{n}", producer="ats", norm=n, label=term,
            matches=[dict(m) for m in ms] if isinstance(ms, list) else None,
            claim_text=(claim or {}).get("text"), claim_location=(claim or {}).get("location"),
        ))
    for i, c in enumerate(claims):
        if i in consumed:
            continue
        claim = c.get("claim") or {}
        n = norm_quote(claim.get("text"))
        if not n or n in seen_norms:
            continue
        seen_norms.add(n)
        rows.append(GroupOneFinding(
            key=f"oracle:{n}", producer="oracle", norm=n, label=str(claim.get("text") or ""),
            claim_text=claim.get("text"), claim_location=claim.get("location"),
        ))
    return rows


def find_listed(findings: list[GroupOneFinding], key: str) -> GroupOneFinding | None:
    """The listed finding for ``key`` — matched on the normalised text, either
    producer (a merged row is accepted under ``ats:`` or ``oracle:``)."""
    _, n = split_key(key)
    for f in findings:
        if f.norm == n:
            return f
    return None


# ── the stored state ─────────────────────────────────────────────────────────


def load_state(raw: Any) -> dict:
    """A normalised COPY of the stored value (NULL/garbage → empty state)."""
    state = {"walked_at": None, "decisions": []}
    if isinstance(raw, dict):
        state["walked_at"] = raw.get("walked_at")
        for d in raw.get("decisions") or []:
            if isinstance(d, dict) and d.get("action") in ACTIONS and d.get("finding_key"):
                state["decisions"].append(copy.deepcopy(d))
    return state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_decision(state: dict, key: str) -> dict | None:
    _, n = split_key(key)
    for d in state.get("decisions") or []:
        try:
            if split_key(d["finding_key"])[1] == n:
                return d
        except ValueError:
            continue
    return None


def without_decision(state: dict, key: str) -> dict:
    _, n = split_key(key)
    out = load_state(state)
    kept = []
    for d in out["decisions"]:
        try:
            same = split_key(d["finding_key"])[1] == n
        except ValueError:
            same = False
        if not same:
            kept.append(d)
    out["decisions"] = kept
    return out


def with_decision(
    state: dict,
    key: str,
    label: str,
    action: str,
    undo_sections: list[dict] | None = None,
    at: str | None = None,
) -> dict:
    """Record ``action`` on ``key``, REPLACING any earlier decision on the same
    finding (its ``undo.before`` is superseded by the new action's, cl. 6)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    split_key(key)
    out = without_decision(state, key)
    out["decisions"].append({
        "finding_key": key,
        "label": label,
        "action": action,
        "at": at or _now(),
        "undo": (
            {"sections": [{"section_id": s["section_id"], "before": s["before"]} for s in undo_sections]}
            if undo_sections else None
        ),
    })
    return out


def with_walked(state: dict, at: str | None = None) -> dict:
    out = load_state(state)
    out["walked_at"] = at or _now()
    return out


# ── the live derivation (SF-REVIEW.9) ────────────────────────────────────────


def derive_review(state: Any, ats_report: dict | None, truth_report: dict | None) -> dict:
    """What the surface shows, derived from the LIVE report.

    ``open`` = every group-1 finding the report lists (a decision on it is
    ignored). ``decided`` = the decisions whose finding the report no longer
    lists. ``unknown_producers`` names a producer whose report is missing
    (ADR-081 cl. 9): no figure reads as complete while it is non-empty.
    """
    st = load_state(state)
    findings = group_one_findings(ats_report, truth_report)
    listed_norms = {f.norm for f in findings}
    decided = []
    for d in st["decisions"]:
        try:
            n = split_key(d["finding_key"])[1]
        except ValueError:
            continue
        if n not in listed_norms:
            decided.append({k: d.get(k) for k in ("finding_key", "label", "action", "at")})
    unknown = [p for p, r in (("ats", ats_report), ("oracle", truth_report)) if r is None]
    return {
        "open": [{"finding_key": f.key, "label": f.label} for f in findings],
        "decided": decided,
        "open_count": len(findings),
        "decided_count": len(decided),
        "total": len(findings) + len(decided),
        "unknown_producers": unknown,
    }


# ── write serialisation ──────────────────────────────────────────────────────

# Weak values: a lock lives exactly as long as some coroutine holds or awaits it.
_LOCKS: "weakref.WeakValueDictionary[tuple[str, uuid.UUID], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def document_lock(kind: str, document_id: uuid.UUID) -> asyncio.Lock:
    """One asyncio lock per generated document, shared by the review actions and
    the background post-edit re-audit (``_update_ats_report_by_id`` /
    ``_update_ats_report_letter_by_id``), so a background re-audit of an earlier
    section save can never interleave with a review action's save → re-audit →
    decision write and persist a report computed from older content after it.

    Process-local: correct for the single-worker uvicorn the Community Edition
    ships; a multi-worker deployment needs a database-level lock instead (named
    on the backend collector)."""
    k = (kind, uuid.UUID(str(document_id)))
    lock = _LOCKS.get(k)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[k] = lock
    return lock
