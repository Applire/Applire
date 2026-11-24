# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet MD-G9 — what a generated document's persisted reports describe.

The download hold (``review_signals``) reads the reports. A report only speaks for
the content it was computed from, and three states had no report for the content
that ships (adv/holds D-3, D-4, D-5):

* **pending** — a save committed new content and its re-audit runs in the
  background (the section editor); until it lands the reports describe the old text;
* **not run** — the re-audit was refused (no log consent, no allowance, no key: any
  ``AdmissionRefused``) or failed (a provider error, a render crash); the truth
  report is then gone and nothing describes the new text;
* **unknown writer** — content changed after the last audit without either marker.

``audit_state`` (one JSON column, migration 0094) records it:

``{"basis": <content fingerprint>, "state": "done" | "pending" | "not_run",
"reason": str | None, "at": iso, "carried": [notes] | None, "carried_basis": str |
None, "terminal": <terminal-review row> | None}``

* ``basis`` — :func:`fingerprint` of the content the last audit read (``done`` /
  ``not_run``) or of the saved content awaiting its audit (``pending``);
* ``carried`` — the open points that stood BEFORE an audit that did not run (a
  re-audit that did not run keeps the previous hold, never releases it);
* ``terminal`` — the ``terminal-review`` row when the ATS engine failed and the
  report was left NULL: the row is a statement about the review, not about the
  text, so a render crash may not erase it (D-5).

MD-G9's rule: incomplete, refused, failed or pending never releases. NULL = not
recorded (a row from before 0094): no audit-state hold, exactly as before.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

#: A ``pending`` marker older than this is a re-audit that will not come (the
#: background task died with its process): it holds as "not re-checked", which the
#: person can decide, instead of holding forever.
PENDING_STALE_SECONDS = 300

DONE, PENDING, NOT_RUN = "done", "pending", "not_run"


def _kind(record) -> str:
    return "cover_letter" if hasattr(record, "letter_data") else "cv"


_CONTENT_COLUMNS = {
    "cv": ("tailored_data", "content_snapshot", "section_overrides"),
    "cover_letter": ("letter_data", "section_overrides"),
}


def _json_default(o: Any) -> str:
    # The value a JSON column round-trip gives back (a date/datetime as ISO text), so
    # a fingerprint taken before the commit equals the one taken after a reload.
    # Always a str: anything else would be handed back here (an object whose
    # ``isoformat`` returns another object recursed without end).
    from datetime import date, time

    if isinstance(o, (date, time)):
        return o.isoformat()
    return str(o)


def fingerprint(record) -> str:
    """sha256 over every column an audit reads content from (the columns of
    ``review_actions.content_fingerprint``), JSON-normalised."""
    import hashlib
    import json

    cols = [getattr(record, c, None) for c in _CONTENT_COLUMNS[_kind(record)]]
    payload = json.dumps(cols, sort_keys=True, default=_json_default, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load(record) -> dict[str, Any] | None:
    raw = getattr(record, "audit_state", None)
    return raw if isinstance(raw, dict) and raw.get("state") in (DONE, PENDING, NOT_RUN) else None


def _write(record, state: dict[str, Any]) -> None:
    # A NEW dict: a JSON column does not track in-place mutation.
    record.audit_state = copy.deepcopy(state)


def mark_pending(record) -> None:
    """The save of new content that a background re-audit will read: in the SAME
    commit as the content (D-4), so no door can serve the content with the old
    reports in between."""
    prev = load(record) or {}
    _write(record, {
        **prev,
        "basis": fingerprint(record),
        "state": PENDING,
        "reason": None,
        "at": _now().isoformat(),
    })


def record_audit(
    record,
    *,
    ran: bool,
    reason: str | None = None,
    carried: dict | None = None,
    terminal: dict | None = None,
) -> None:
    """The audit seam's last word on this content. ``ran`` = the ATS and the truth
    audit both describe the current content. ``carried`` = the open-points check
    that stood before this audit (kept only when the audit did not run).
    ``terminal`` = the terminal-review row to keep when the ATS report is NULL."""
    state: dict[str, Any] = {
        "basis": fingerprint(record),
        "state": DONE if ran else NOT_RUN,
        "reason": None if ran else (reason or "audit_failed"),
        "at": _now().isoformat(),
        "carried": None,
        "carried_basis": None,
        "terminal": terminal if isinstance(terminal, dict) else None,
    }
    if not ran and isinstance(carried, dict):
        notes = [n for n in (carried.get("notes") or []) if isinstance(n, dict)]
        state["carried"] = notes or None
        state["carried_basis"] = carried.get("open_points_hash")
    _write(record, state)


def terminal_row(record) -> dict | None:
    """The terminal-review row kept when the ATS report was left NULL (D-5)."""
    st = load(record)
    row = (st or {}).get("terminal")
    return row if isinstance(row, dict) and row.get("id") == "terminal-review" else None


def _age_seconds(st: dict[str, Any]) -> float | None:
    try:
        at = datetime.fromisoformat(str(st.get("at")))
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (_now() - at).total_seconds()


def pending(record) -> bool:
    """A re-audit of exactly this content is queued or running (and not stale)."""
    st = load(record)
    if st is None or st.get("state") != PENDING or st.get("basis") != fingerprint(record):
        return False
    age = _age_seconds(st)
    return age is not None and age < PENDING_STALE_SECONDS


def unchecked(record) -> dict[str, Any] | None:
    """The state when NO audit describes the current content and none is coming:
    the re-audit did not run, a pending re-audit went stale, or the content changed
    after the last audit without a marker. None otherwise (also for NULL)."""
    st = load(record)
    if st is None:
        return None
    if st.get("basis") != fingerprint(record):
        return {**st, "reason": st.get("reason") or "content_changed"}
    if st.get("state") == NOT_RUN:
        return st
    if st.get("state") == PENDING and not pending(record):
        return {**st, "reason": "audit_lost"}
    return None
