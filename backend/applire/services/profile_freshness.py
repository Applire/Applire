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

"""Has the profile's CONTENT changed since a document was created? (Hazelnut
build 7 — ONE predicate for every "recreate?" cue: the CV's (WP-F) and the cover
letter's (WP-L). Founder ruling F-2 (2) 2026-11-07; ADR-062 cl. 1 — code computes
the fact.)

The cue says "Dein Profil wurde seit der Erstellung … aktualisiert", because the
profile changes through the interview, an import, a manual edit or the agent door
— not only the interview.

One persisted fact answers it: the Master Profile's ``enrichment_history`` trail.
Every content write goes through the single committer
(``services/profile/commit.py``), which appends one record per batch with its
``source``, ``timestamp`` and one ``FieldChange`` per changed field. A document is
behind the profile when a record NEWER than the document carries at least one
CONTENT change — a change to a profile section (default: any section but
``metadata``/``_meta``; a caller may narrow it, the CV to
:data:`CV_FEEDING_SECTIONS`) — or newly records a limit the candidate stated.

What does NOT count, by construction:

* writes a generation causes itself — fact pins, the keyword-ledger refresh,
  document receipts, health/notice bookkeeping, application pins — append no trail
  record (only ``commit.py``, ``snapshots.py`` and ``photo.py`` may write the
  profile at all, ``models/profile.py`` ``AUTHORIZED_PROFILE_WRITE_MODULES``), and
  the ledger's receipt filter rewrites existing records in place, keeping their
  timestamps;
* a record with no change (the trail is unconditional: a receipt of what was
  matched or held back);
* ``metadata``-only receipts — a resolved confirmation question
  (``pending_confirmations``), a re-confirmed or escalated limit
  (``denied_concepts`` ``updated``): they say what the system asked or noted, not
  what the profile now holds; whatever an answer changed lands as its own change.
  A NEW or WITHDRAWN limit (``added`` / ``removed``) does count;
* ``MasterProfile.updated_at`` — it moves on every column write, bookkeeping
  included, so it is never read here;
* a write the document ITSELF caused (adversarial A-5, main MD7-10): the review
  card's "Im Profil eintragen", the CV section assist's accept, a CV section edit
  saved to the profile. The committer tags those records with the originating
  document (``EnrichmentRecord.document_id``); the document already says what
  they wrote, so a caller passing its own ``document_id`` skips them. The OTHER
  document still gets the cue. A record without the tag (written before it
  existed) counts as before.

An "undo last merge" (adversarial A-8) restores the pre-merge JSON, trail
included, and appends ONE dated ``undo`` record listing what it took back
(``snapshots._with_undo_record``): a document created after the merge and
before the undo still carries the undone content and reads as behind; one
created before the merge (``reverts_since``) or after the undo does not.

The dossier's re-tailor nudge (E039/US221 ``stale_cv``, ``services/application.py``)
imports its section list and limit test from here — one rule (ADR-066).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "CV_FEEDING_SECTIONS",
    "INTERVIEW_SOURCES",
    "is_new_limit",
    "is_withdrawn_limit",
    "parse_trail_timestamp",
    "profile_updated_since",
]

#: Re-probe N3 (Hazelnut b3): the vault sections a tailored CV is written from
#: (`MasterProfileData` content the CV writer reads). Only a change to one of
#: these is "growth" for the re-tailor nudge. Fail-closed: a section not listed
#: here (`availability` — the letter's notice period, saved from the letter
#: form by B3-11 —, `metadata` bookkeeping such as pending confirmations or
#: denials, `_meta` N/A marks, an empty section) never offers a re-tailor, and
#: never reaches the nudge's copy as a raw field name. Every name here has a
#: label in the frontend's `SECTION_LABEL_KEYS` (`lib/stale-cv.ts`).
CV_FEEDING_SECTIONS: frozenset[str] = frozenset({
    "personal_info",
    "professional_summary",
    "work_experience",
    "education",
    "certifications",
    "skills",
    "languages",
    "projects",
    "publications",
    "volunteer_activities",
    "signature_stories",
})

#: Enrichment-record sources that are an interview door (the guided interview,
#: the agent interview, MCP testimony). A ``testimony`` record a document's own
#: surface wrote (tagged ``document_id``, A-5) is that document's door, not an
#: interview answer — :func:`profile_updated_since` never counts it under a
#: ``sources`` narrowing.
INTERVIEW_SOURCES: frozenset[str] = frozenset({"interview", "agent_interview", "testimony"})

#: Sections that are the system's bookkeeping, never profile content.
_BOOKKEEPING_SECTIONS: frozenset[str] = frozenset({"metadata", "_meta"})


def is_new_limit(change: dict) -> bool:
    """B3-12: a NEWLY recorded denial — the candidate stated a limit. An
    escalation, re-confirmation or probe bookkeeping is ``updated`` and does
    not count; the limit itself was recorded earlier."""
    return (
        change.get("section") == "metadata"
        and change.get("field") == "denied_concepts"
        and change.get("action") == "added"
    )


def is_withdrawn_limit(change: dict) -> bool:
    """A recorded denial taken back (``WithdrawDenial``, e.g. "Ja, zurücknehmen"
    on the 0086/0088 heal question). Under L-2 a letter states the person's
    recorded denials, so one written before the withdrawal states a "no" the
    person no longer gives — content, not bookkeeping."""
    return (
        change.get("section") == "metadata"
        and change.get("field") == "denied_concepts"
        and change.get("action") == "removed"
    )


def parse_trail_timestamp(value: object) -> datetime | None:
    """A trail ``timestamp`` (datetime or ISO string, naive = UTC) as an aware
    UTC datetime; ``None`` when unreadable."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _is_content_change(change: dict, sections: Collection[str] | None) -> bool:
    if is_new_limit(change) or is_withdrawn_limit(change):
        return True
    section = change.get("section")
    if sections is None:
        return isinstance(section, str) and section not in _BOOKKEEPING_SECTIONS
    return section in sections


def profile_updated_since(
    created_at: object,
    profile_json: dict[str, Any] | None,
    *,
    sections: Collection[str] | None = None,
    sources: Collection[str] | None = None,
    document_id: object = None,
) -> bool:
    """True when the profile's CONTENT changed AFTER ``created_at``.

    ``sections`` — the sections the document is written from (``None`` = any
    profile section; the CV passes :data:`CV_FEEDING_SECTIONS`). ``sources`` —
    the doors that count (``None`` = any: interview, agent interview, testimony,
    CV upload/paste, LinkedIn/XING import, manual edit, role add, the undo of
    a merge (A-8); the CV
    page's auto-start passes :data:`INTERVIEW_SOURCES`); a record a document's
    own surface wrote is never one of a narrowed set. ``document_id`` — the
    document asking: records that document's own surface wrote are skipped
    (A-5). Pure; ``False`` on anything unreadable — no cue is the safe default.
    """
    created = parse_trail_timestamp(created_at)
    if created is None or not isinstance(profile_json, dict):
        return False
    metadata = profile_json.get("metadata")
    trail = (metadata.get("enrichment_history") or []) if isinstance(metadata, dict) else []
    own = str(document_id) if document_id is not None else None
    for record in trail:
        if not isinstance(record, dict):
            continue
        origin = record.get("document_id")
        if own is not None and origin is not None and str(origin) == own:
            continue  # A-5: this document already says what its own surface wrote
        if sources is not None and (origin is not None or record.get("source") not in sources):
            continue
        ts = parse_trail_timestamp(record.get("timestamp"))
        if ts is None or ts <= created:
            continue
        if record.get("source") == "undo":
            # A-8: the undo took back what a merge wrote; a document created
            # before that merge never held it and matches the profile again.
            since = parse_trail_timestamp(record.get("reverts_since"))
            if since is not None and created < since:
                continue
        for change in record.get("changes") or []:
            if isinstance(change, dict) and _is_content_change(change, sections):
                return True
    return False
