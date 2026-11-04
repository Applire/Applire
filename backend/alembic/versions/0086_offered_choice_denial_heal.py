# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ask about every "not yet in my profile" choice that was saved as a denial (Hazelnut build 6, A-1 heal).

Founder UAT 2026-11-04 (G-3): before build 6, clicking an offered R2-2 interview
choice ("…, aber zu X, Y und Z steht in meinem Profil noch nichts") could record
X, Y and Z as the candidate's own ``direct`` denials. That clause only says the
PROFILE is silent, so it is not the candidate's no. Build 6 stops minting such
rows (G-3 forward fix, ``stance.offered_choice_gate``). This step handles the
rows that already exist.

RULING A-1 = B: ONE Health-hub question per affected statement; one click withdraws
every concept of that statement, and "Doch, das fehlt mir" keeps them.
RULING A-2 = A: the question is parked HERE, at upgrade, so a user who only writes
letters sees it as well. The committer parks the same item on every later write
(``stance.park_offered_choice_confirmations``). Both use the same key
(``offered_choice|<statement hash>``), so the question is never parked twice.

This file holds a VERBATIM copy of the detector and the copy
(``stance.profile_silence_clauses`` / ``offered_choice_denial_hits`` /
``offered_choice_denial_confirmation``) because a migration never imports
application code: that code keeps changing after the migration has shipped (the
0083 precedent). ``tests/unit/test_b6_a_offered_choice_heal.py`` pins that the two
agree.

* Only live vaults (``deleted_at IS NULL``) are touched. Nothing is withdrawn here:
  the only write is the parked question in ``metadata.pending_confirmations``.
* Idempotent: a statement with an open or answered item is skipped.
* No timestamp column moves.

**Downgrade** removes the OPEN items of this family (older code cannot act on
them: its denial_scope branch reads one ``denial_concept``). Answers already
given, and withdrawals already made, stay — they are the candidate's acts.

Chain: 0085 (cv page overflow) -> 0086.
"""

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0086"
down_revision: Union[str, None] = "0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# ── verbatim copy of the app's detector (stance.py, A-1 heal) ──────────────────

_BOUNDARY = "␞"

_PROFILE_SILENCE_MARKERS = (
    re.compile(r"steht in meinem profil (?:(?:noch|bisher|derzeit) )*nichts"),
    re.compile(
        r"in meinem profil (?:(?:ist|sind|steht|stehen|wird|werden) )?[^.;]{0,120}?"
        r"(?:(?:noch|bisher|derzeit) )*nicht (?:dokumentiert|beschrieben|erwähnt|enthalten|belegt)"
    ),
    re.compile(
        r"my profile (?:does not|doesn't|does n't) (?:yet )?"
        r"(?:document|mention|cover|show|list|include|describe)"
    ),
    re.compile(r"my profile (?:has|says) nothing (?:yet )?(?:on|about)"),
)
_CLAUSE_START = re.compile(r";|[.!?](?=\s)|,?\s(?:aber|but|jedoch|however)\b")
_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")


def _norm(s: str) -> str:
    s = s.replace("­", "")
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[-‐-―−]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


def _word_present(form_norm: str, text_norm: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(form_norm)}(?![a-z0-9])", text_norm) is not None


def profile_silence_clauses(statement: str) -> list[str]:
    text = _norm(statement or "")
    out: list[str] = []
    for pattern in _PROFILE_SILENCE_MARKERS:
        for m in pattern.finditer(text):
            starts = [s.end() for s in _CLAUSE_START.finditer(text, 0, m.start())]
            start = starts[-1] if starts else 0
            end_m = _CLAUSE_END.search(text, m.end())
            end = end_m.start() if end_m else len(text)
            clause = text[start:end].strip(" ,")
            if clause and clause not in out:
                out.append(clause)
    return out


def offered_choice_denial_hits(profile_json: Any) -> list[dict[str, Any]]:
    if not isinstance(profile_json, dict):
        return []
    metadata = profile_json.get("metadata")
    raw = metadata.get("denied_concepts") if isinstance(metadata, dict) else None
    groups: dict[str, dict[str, Any]] = {}
    for d in raw or []:
        if not isinstance(d, dict) or d.get("source") != "interview":
            continue
        concept, statement = d.get("concept"), d.get("statement")
        if not (isinstance(concept, str) and concept.strip() and isinstance(statement, str)):
            continue
        c_norm = _norm(concept)
        clauses = profile_silence_clauses(statement)
        if not clauses or not any(_word_present(c_norm, c) for c in clauses):
            continue
        rest = _norm(statement)
        for c in clauses:
            rest = rest.replace(c, f" {_BOUNDARY} ")
        if _word_present(c_norm, rest):
            continue
        key = hashlib.sha1(_norm(statement).encode("utf-8")).hexdigest()[:16]
        group = groups.setdefault(
            key, {"statement_key": key, "denial_concepts": [], "denial_date": ""}
        )
        group["denial_concepts"].append(concept)
        date = d.get("date") if isinstance(d.get("date"), str) else ""
        if date and (not group["denial_date"] or date < group["denial_date"]):
            group["denial_date"] = date
    return list(groups.values())


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _date_de(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d):02d}.{int(m):02d}.{y}"
    except ValueError:
        return ""


def _date_en(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{int(d)} {_MONTHS_EN[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return ""


def _join_de(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} und {items[-1]}"


def _join_en(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


_SOURCE = "denial_scope"
_CONFLICT = "offered_choice"
_KEYS = ("not_a_no", "lacks_it")


def offered_choice_denial_confirmation(hit: dict[str, Any]) -> dict[str, Any]:
    """The persisted (``model_dump(mode="json")``) shape of the app's
    ``PendingConfirmation`` for one hit."""
    concepts = [str(c) for c in hit.get("denial_concepts") or [] if str(c).strip()]
    iso = str(hit.get("denial_date") or "")
    d_de, d_en = _date_de(iso), _date_en(iso)
    lead_de = f"Am {d_de} hast du im Interview" if d_de else "Im Interview hast du"
    lead_en = f"On {d_en} you picked" if d_en else "In the interview you picked"
    where_en = " in the interview" if d_en else ""
    question_de = (
        f"{lead_de} eine vorgeschlagene Antwort gewählt. Diese Antwort sagte nur, dass in deinem "
        f"Profil zu {_join_de(concepts)} noch nichts steht, nicht dass dir das fehlt. "
        "Gespeichert wurde es trotzdem als „trifft nicht zu“. Sollen wir das zurücknehmen?"
    )
    question_en = (
        f"{lead_en} a suggested answer{where_en}. It only said that your profile does not "
        f"mention {_join_en(concepts)} yet, not that you lack them. It was still saved as "
        "“does not apply”. Should we take that back?"
    )
    options = {
        "not_a_no": {"de": "Ja, zurücknehmen", "en": "Yes, take it back"},
        "lacks_it": {"de": "Doch, das fehlt mir", "en": "No, I do lack that"},
    }
    return {
        "confirmation_id": str(uuid.uuid4()),
        "question": question_en,
        "options": [options[k]["en"] for k in _KEYS],
        "context": {
            "kind": _SOURCE,
            "conflict_kind": _CONFLICT,
            "denial_concepts": concepts,
            "statement_key": str(hit.get("statement_key") or ""),
        },
        "source": _SOURCE,
        "resolved": False,
        "chosen_option": None,
        "question_i18n": {"de": question_de, "en": question_en},
        "options_i18n": [dict(options[k]) for k in _KEYS],
        "option_keys": list(_KEYS),
    }


def _key_of(context: Any) -> str:
    if not isinstance(context, dict) or context.get("conflict_kind") != _CONFLICT:
        return ""
    stmt = str(context.get("statement_key") or "")
    return f"{_CONFLICT}|{stmt}" if stmt else ""


def park(profile_json: Any) -> int:
    """Park the questions into ``profile_json`` in place; return how many."""
    if not isinstance(profile_json, dict):
        return 0
    metadata = profile_json.get("metadata")
    if not isinstance(metadata, dict):
        return 0
    pending = metadata.get("pending_confirmations")
    if not isinstance(pending, list):
        pending = []
    answered = metadata.get("denial_scope_answered")
    asked = {
        _key_of(c.get("context"))
        for c in pending
        if isinstance(c, dict) and c.get("source") == _SOURCE
    } | set(answered if isinstance(answered, list) else [])
    added = 0
    for hit in offered_choice_denial_hits(profile_json):
        key = f"{_CONFLICT}|{hit['statement_key']}"
        if key in asked:
            continue
        asked.add(key)
        pending.append(offered_choice_denial_confirmation(hit))
        added += 1
    if added:
        metadata["pending_confirmations"] = pending
    return added


def unpark(profile_json: Any) -> int:
    """Remove the OPEN items of this family in place; return how many."""
    metadata = profile_json.get("metadata") if isinstance(profile_json, dict) else None
    pending = metadata.get("pending_confirmations") if isinstance(metadata, dict) else None
    if not isinstance(pending, list):
        return 0
    keep = [
        c
        for c in pending
        if not (
            isinstance(c, dict)
            and not c.get("resolved")
            and isinstance(c.get("context"), dict)
            and c["context"].get("kind") == _SOURCE
            and c["context"].get("conflict_kind") == _CONFLICT
        )
    ]
    removed = len(pending) - len(keep)
    if removed:
        metadata["pending_confirmations"] = keep
    return removed


# ── the step ────────────────────────────────────────────────────────────────

_profiles = sa.table(
    "master_profiles",
    sa.column("id"),
    sa.column("profile_json", sa.JSON().with_variant(JSONB(), "postgresql")),
    sa.column("deleted_at"),
)


def _rewrite(fn) -> int:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_profiles.c.id, _profiles.c.profile_json).where(_profiles.c.deleted_at.is_(None))
    ).fetchall()
    total = 0
    for row_id, raw in rows:
        data = json.loads(raw) if isinstance(raw, str) else raw
        n = fn(data)
        if n:
            bind.execute(
                _profiles.update().where(_profiles.c.id == row_id).values(profile_json=data)
            )
            total += n
    return total


def upgrade() -> None:
    n = _rewrite(park)
    log.info("0086: parked %d offered-choice denial question(s)", n)


def downgrade() -> None:
    n = _rewrite(unpark)
    log.info("0086 downgrade: removed %d open offered-choice denial question(s)", n)
