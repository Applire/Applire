# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Has the profile's CONTENT changed since a document was created? (Hazelnut build 7)

ONE predicate for every "recreate?" cue — the cover letter's (WP-L) and the CV's
(WP-F). Founder ruling F-2 (2026-11-07, applied to L-3): the cue says "Dein Profil
wurde seit der Erstellung … aktualisiert", because the profile can change through the
interview, an import, a manual edit or the agent door — not only the interview.

A FACT (ADR-062 cl. 1): two timestamps and the profile's own decision trail. The
trail (``metadata.enrichment_history``) is written by exactly one path — the profile
commit (``services/profile/commit.py``), once per content batch, with one
``FieldChange`` per field it changed. Writes a generation causes — fact pins, the
keyword-ledger refresh, health/receipt bookkeeping — never append a trail record, so
they can never fire this. A record with no change (a receipt of what was matched or
held back) is not a content change either.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _as_aware_utc(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def profile_updated_since(created_at: object, profile_json: dict[str, Any] | None) -> bool:
    """True when the profile's content changed AFTER ``created_at``: an
    ``enrichment_history`` record with at least one change whose timestamp is newer.
    Any source (interview, agent interview, testimony, CV upload/paste, LinkedIn/XING
    import, manual edit). Pure; ``False`` on anything unreadable (no cue)."""
    created = _as_aware_utc(created_at)
    if created is None:
        return False
    metadata = (profile_json or {}).get("metadata") if isinstance(profile_json, dict) else None
    trail = (metadata or {}).get("enrichment_history") or [] if isinstance(metadata, dict) else []
    for record in trail:
        if not isinstance(record, dict):
            continue
        if not [c for c in (record.get("changes") or []) if isinstance(c, dict)]:
            continue
        ts = _as_aware_utc(record.get("timestamp"))
        if ts is not None and ts > created:
            return True
    return False
