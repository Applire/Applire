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
(``stance.inflected_present`` / ``profile_silence_clauses`` /
``_redenied_concepts`` / ``offered_choice_denial_hits`` /
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

def _norm(s: str) -> str:
    s = s.replace("­", "")
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[-‐-―−]", " ", s)
    return re.sub(r"\s+", " ", s).lower().strip()


_PROFILE_SILENCE_MARKERS: tuple[tuple[re.Pattern[str], bool], ...] = (
    # (pattern, the clause ENDS at the marker) — True where the concepts come
    # BEFORE the marker ("zu X steht in meinem Profil noch nichts"), so a
    # sentence the candidate continued after it ("…, und mit X habe ich nie
    # gearbeitet", adversarial H-3) is not part of the clause.
    (re.compile(r"steht in meinem profil (?:(?:noch|bisher|derzeit) )*nichts"), True),
    (
        re.compile(
            r"in meinem profil (?:(?:ist|sind|steht|stehen|wird|werden) )?[^.;]{0,120}?"
            r"(?:(?:noch|bisher|derzeit) )*nichts? "
            r"(?:dokumentiert|beschrieben|erwähnt|enthalten|belegt)"
        ),
        True,
    ),
    (
        re.compile(
            r"(?:is|are) not (?:yet )?"
            r"(?:documented|mentioned|covered|shown|listed|included|described) in my profile"
        ),
        True,
    ),
    (
        re.compile(
            r"my profile (?:does not|doesn't|does n't) (?:yet )?"
            r"(?:document|mention|cover|show|list|include|describe)"
        ),
        False,
    ),
    (re.compile(r"my profile (?:has|says) nothing (?:yet )?(?:on|about)"), False),
)
#: Where the clause starts: the last of these before a marker (or the start of
#: the statement). The R2-2 shape joins its halves with ", aber" / ", but"; the
#: scan also found ";" and a sentence break.
_CLAUSE_START = re.compile(r";|[.!?](?=\s)|,?\s(?:aber|but|jedoch|however)\b")
_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")
#: The closed inflection endings a concept may carry inside the clause (adversarial
#: H-2: "zu … Arbeitssicherheitsmanagementsystemen" for the denied "…systeme"; the
#: DE dative plural, EN/DE plural). Lexical, last token only, never below a
#: four-letter stem — a fact about the word form (ADR-062 cl. 1).
_INFLECTION_ENDINGS: tuple[str, ...] = ("en", "es", "er", "n", "e", "s")
_INFLECTION_MIN_STEM = 4


def inflected_present(concept: str, text_norm: str) -> bool:
    """Word-boundary presence of ``concept`` in ``text_norm`` up to one closed
    inflection ending on its last token (:data:`_INFLECTION_ENDINGS`), in both
    directions: "…systeme" finds "…systemen", "Referenzarchitekturen" finds
    "Referenzarchitektur". Shorter last tokens ("AWS", "ESB") match exactly. Pure."""
    form = _norm(concept or "")
    if not form:
        return False
    head, _, last = form.rpartition(" ")
    stems = {last}
    if len(last) >= _INFLECTION_MIN_STEM:
        for ending in _INFLECTION_ENDINGS:
            if last.endswith(ending) and len(last) - len(ending) >= _INFLECTION_MIN_STEM:
                stems.add(last[: -len(ending)])
    tail = (
        "(?:" + "|".join(_INFLECTION_ENDINGS) + ")?"
        if len(last) >= _INFLECTION_MIN_STEM
        else ""
    )
    alts = "|".join(re.escape(x) for x in sorted(stems, key=len, reverse=True))
    prefix = re.escape(head + " ") if head else ""
    return re.search(rf"(?<![a-z0-9]){prefix}(?:{alts}){tail}(?![a-z0-9])", text_norm) is not None


def profile_silence_clauses(statement: str) -> list[str]:
    """The ``_norm``-ed profile-silence clauses of ``statement`` — each from the
    separator before a :data:`_PROFILE_SILENCE_MARKERS` hit to the marker's end
    (concepts before it) or to the end of its sentence (concepts after it). A
    fact about the wording (ADR-062 cl. 1). Pure."""
    text = _norm(statement or "")
    out: list[str] = []
    for pattern, ends_at_marker in _PROFILE_SILENCE_MARKERS:
        for m in pattern.finditer(text):
            starts = [s.end() for s in _CLAUSE_START.finditer(text, 0, m.start())]
            start = starts[-1] if starts else 0
            if ends_at_marker:
                end = m.end()
            else:
                end_m = _CLAUSE_END.search(text, m.end())
                end = end_m.start() if end_m else len(text)
            clause = text[start:end].strip(" ,")
            if clause and clause not in out:
                out.append(clause)
    return out


def _redenied_concepts(metadata: Any) -> set[str]:
    """The ``_norm``-ed concepts a LATER turn denied again (adversarial H-1).

    ``DeniedConcept.statement`` is write-once (#348), so a typed "Mit Kafka habe
    ich nie gearbeitet" after the chip refreshes the chip-minted row and keeps
    the chip's words. What the later turn DID leave is its receipt:
    ``record_denials`` emits an ``updated`` ``denied_concepts`` change only for
    a re-denial from ANOTHER statement or source (or a probe escalation), and
    the committer files it on that turn's ``enrichment_history`` record. A
    fact about the trail (ADR-062 cl. 1)."""
    history = metadata.get("enrichment_history") if isinstance(metadata, dict) else None
    out: set[str] = set()
    for record in history or []:
        for change in (record.get("changes") if isinstance(record, dict) else None) or []:
            if (
                isinstance(change, dict)
                and change.get("section") == "metadata"
                and change.get("field") == "denied_concepts"
                and change.get("action") == "updated"
                and isinstance(change.get("new_value"), str)
            ):
                out.add(_norm(change["new_value"]))
    return out


def offered_choice_denial_hits(profile_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A-1 heal — the persisted denials an R2-2 choice minted before G-3, grouped
    by statement: ``[{"statement_key", "denial_concepts", "denial_date"}]``.

    The offered choice itself was never persisted (only its text, as the
    candidate's answer), so detection is phrase-coupled (RULING A-1): an
    ``interview``-source denial whose concept occurs (:func:`inflected_present`)
    inside a profile-silence clause of its own statement
    (:func:`profile_silence_clauses`), NOWHERE else in that statement, and that
    no later turn denied again (:func:`_redenied_concepts`). A concept the
    candidate typed themselves — beside the clause or in a later turn — is
    their own no and is never offered for withdrawal. A miss asks nothing; a
    false hit only asks. The 0086 data migration carries a verbatim copy of
    this block (parity-tested). Pure, malformed tolerant."""
    if not isinstance(profile_json, dict):
        return []
    metadata = profile_json.get("metadata")
    raw = metadata.get("denied_concepts") if isinstance(metadata, dict) else None
    redenied = _redenied_concepts(metadata)
    groups: dict[str, dict[str, Any]] = {}
    for d in raw or []:
        if not isinstance(d, dict) or d.get("source") != "interview":
            continue
        concept, statement = d.get("concept"), d.get("statement")
        if not (isinstance(concept, str) and concept.strip() and isinstance(statement, str)):
            continue
        if _norm(concept) in redenied:
            continue
        clauses = profile_silence_clauses(statement)
        if not clauses or not any(inflected_present(concept, c) for c in clauses):
            continue
        rest = _norm(statement)
        for c in clauses:
            rest = rest.replace(c, f" {_BOUNDARY} ")
        if inflected_present(concept, rest):
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
