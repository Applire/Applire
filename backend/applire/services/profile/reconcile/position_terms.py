# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Position-term guard for the ADR-046 reconciler (#831; ADR-046 amended 2026-11-13).

Ground truth (UX audit round 4, 2026-11-07, Sonnet 5.5, testimony door): the
person wrote "Verantwortlich zeichnet unsere Leiterin Rechnungswesen — ich bin
die operative Nummer zwei dafür." The reconciler wrote the bullet "Erstellt seit
2023 die konsolidierten Quartalszahlen in LucaNet mit; operative
Stellvertreterin der Leiterin Rechnungswesen, die verantwortlich zeichnet". "Die
operative Nummer zwei" is not a deputy's position, and the vault now said it was.

Earned (applire-prompt-first, category C). The reconcile prompt's rule 4 already
forbids embellishing "a date, title, metric". Replays of the captured turn (Sorbet
WP-T, report.md): the incident's own system prompt gave the deputy wording 4/5 on
Sonnet; HEAD 0/8 on Sonnet and 0/4 on Luna; a 16-case position corpus 0/20 on
both. The rate on HEAD moved with an unrelated rule, so a louder rule buys
nothing measurable — this module makes the outcome impossible whatever the model.

The check is a FACT (ADR-062 cl. 1): does a word of a closed position-term
family (deputy · lead · manager · disciplinary · seniority) occur in what an
op writes, while no word of the SAME family occurs in the person's answer or in
the entry the op edits? Whether the wording is a fair paraphrase is not judged
here — only whether the person's own words, or the record they already hold,
carry the family at all.

Scope: the doors where the person speaks — ``interview`` / ``agent_interview``
(the ANSWER only: the gap label and the question are Applire's words, "a
paraphrase is not a source", ADR-061/048 amended) and ``testimony`` (the whole
text is the person's). Never a CV import or a manual edit: there the incoming
document is the source and its titles are the person's own.

What a hit does (founder RULING T-1, see ``RECONCILE_POSITION_TERM_CUT``): the
clause carrying the term is not written — a bullet is cut at ``;`` / `` — `` /
sentence end and keeps its other clauses; the bullet is dropped only when the
term sits in its only clause. A role/title field carrying an unbacked term is
not written. One INFO log line per cut.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.ops import (
    AddBullets,
    FlagConflict,
    ReconcileOp,
    SetField,
    UpsertProject,
    UpsertStory,
    UpsertVolunteer,
    UpsertWork,
)

logger = logging.getLogger(__name__)

#: The doors whose new_info["answer"] is the person's own words.
GUARDED_SOURCES = frozenset({"interview", "agent_interview", "testimony"})

#: Position-term families — words that state the PERSON's rank. A family is
#: backed when ANY of its members occurs in the answer or the edited entry —
#: "Leiterin" in the answer backs "leitete"; what the guard refuses is a family
#: the person's words never touch.
FAMILIES: dict[str, str] = {
    "deputy": (
        r"stellvertret\w*|\bstellv\.|\bstv\.|\bdeputy\b|\bvice[- ]\w+|\bvize\w*|\w*vertretung\w*"
        r"|\bvertrat\b|\bvertritt\b|\bvertreten\w*|\bstand-in\b|second[- ]in[- ]command"
    ),
    "lead": (
        r"\b\w*?leit(?:e|er|erin|erinnen|ung|ete|eten|et|en|end|ende|ender|enden)\b"
        r"|\b\w*geleitet\w*|\bhead\b(?! office| ?quarters)|\bheaded\b|\bheads\b"
        r"|\blead\b(?![- ]?times?\b)|\bled\b|\bleader\w*|\bleadership\b|\bteam ?lead\w*"
        r"|\btech ?lead\w*|\bchef\w*|\bchief\b|\bdirektor\w*|\bdirector\w*"
        r"|f[üu]hrungskraft\w*"
    ),
    "manager": (
        r"\bmanager\w*|\bmanaged\b|\bmanages\b|\boversaw\b|\boversee\w*|\bsupervis\w*"
        r"|\bbeaufsichtig\w*"
    ),
    "disciplinary": (
        r"disziplinar\w*|personalverantwort\w*|f[üu]hrungsverantwort\w*|\bline manage\w*"
        r"|\bdirect reports?\b"
    ),
    "seniority": r"\bsenior\b|\bprincipal\b|\bstaff engineer\b",
}
#: Measured and left out (Sorbet WP-T, report.md, 552 captured guarded-door
#: reconcile calls): "responsible" (verantwortlich / zuständig / responsibilities)
#: fired 4 times, 0 of them a raised position — a duty, a "Key-User für PP"
#: paraphrase, the noun "responsibilities"; "executive" (Geschäftsführung,
#: Vorstand, CFO …) fired twice, both naming the AUDIENCE of a report. Neither
#: family had a true positive; a control that fires only falsely is removed
#: (memory: count a control's true positives first).
_FAMILY_RE = {name: re.compile(rx, re.IGNORECASE) for name, rx in FAMILIES.items()}
# "ableiten", "begleiten", "einleiten", "weiterleiten", "umgeleitet" … are verbs of
# deriving / accompanying / forwarding, not of leading.
_NOT_LEAD = re.compile(
    r"^(?:ab|an|be|beg|ein|um|ver|weiter|her|zu|aus|fort|durch|über|ueber|vor|zurück|zurueck)"
    r"(?:ge)?leit"
)
_DASHES = re.compile(r"[‐-―−]")
#: Clause boundaries a bullet is cut at: ";", a spaced dash, a sentence end.
_CLAUSE_SPLIT = re.compile(r"(\s*;\s*|\s+[—–-]\s+|(?<=[.!?])\s+)")


def _fold(text: str) -> str:
    return _DASHES.sub("-", unicodedata.normalize("NFKC", text or "").casefold())


def position_terms(text: str) -> list[tuple[str, str]]:
    """``(family, term)`` for every position term in ``text`` (casefolded)."""
    folded = _fold(text)
    out: list[tuple[str, str]] = []
    for family, rx in _FAMILY_RE.items():
        for m in rx.finditer(folded):
            if family == "lead" and _NOT_LEAD.match(m.group(0)):
                continue
            out.append((family, m.group(0)))
    return out


def _families(text: str) -> set[str]:
    return {family for family, _t in position_terms(text)}


def _answer_text(new_info: Any, source: str) -> str | None:
    """The person's own words on a guarded door; ``None`` elsewhere."""
    if source not in GUARDED_SOURCES or not isinstance(new_info, dict):
        return None
    answer = new_info.get("answer")
    return answer if isinstance(answer, str) else ""


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() if k not in ("id", "status") for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _entry_families(target: str | None, profile: MasterProfileData) -> set[str]:
    """Families the EDITED entry already carries (its role, bullets, aliases …)."""
    if not target:
        return set()
    for section in ("work_experience", "projects", "volunteer_activities", "signature_stories"):
        for entry in getattr(profile, section, None) or []:
            if getattr(entry, "id", None) == target:
                return _families(" \n ".join(_strings(entry.model_dump(mode="json"))))
    return set()


def _unbacked(text: str, backed: set[str]) -> list[tuple[str, str]]:
    return [(f, t) for f, t in position_terms(text) if f not in backed]


def _cut(text: str, backed: set[str]) -> tuple[str, list[tuple[str, str]]]:
    """``text`` without its clauses that carry an unbacked term; the cut terms."""
    hits = _unbacked(text, backed)
    if not hits:
        return text, []
    parts = _CLAUSE_SPLIT.split(text)
    clauses = parts[0::2]
    seps = parts[1::2]
    kept: list[str] = []
    for i, clause in enumerate(clauses):
        if not clause.strip() or _unbacked(clause, backed):
            continue
        if kept:
            kept.append(seps[i - 1])
        kept.append(clause)
    out = "".join(kept).strip().rstrip(";,—–-").strip()
    return out, hits


def _log(op: ReconcileOp, field: str, before: str, after: str, hits: list[tuple[str, str]]) -> None:
    logger.info(
        "RECONCILE_POSITION_TERM_CUT op=%s target=%s field=%s terms=%s kept=%s",
        getattr(op, "op", "?"), getattr(op, "target", None), field,
        sorted({t for _f, t in hits}), "clause" if after else "nothing",
    )


def _cut_list(op: ReconcileOp, field: str, items: list[str], backed: set[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            out.append(item)
            continue
        after, hits = _cut(item, backed)
        if hits:
            _log(op, field, item, after, hits)
        if after:
            out.append(after)
    return out


def _cut_str(op: ReconcileOp, field: str, value: str | None, backed: set[str]) -> str | None:
    if not isinstance(value, str):
        return value
    after, hits = _cut(value, backed)
    if hits:
        _log(op, field, value, after, hits)
    return after if hits else value


def _drop_title(op: ReconcileOp, field: str, value: str | None, backed: set[str]) -> bool:
    """True when a title/role value carries an unbacked term (it is not written)."""
    if not isinstance(value, str):
        return False
    hits = _unbacked(value, backed)
    if hits:
        _log(op, field, value, "", hits)
    return bool(hits)


_TEXT_SET_FIELDS = frozenset({
    "description", "responsibilities", "achievements", "industry_context",
    "challenge", "mechanism", "outcome", "benchmark",
})
_TITLE_SET_FIELDS = frozenset({"role", "title", "position"})


def enforce_position_terms(
    ops: list[ReconcileOp],
    *,
    profile: MasterProfileData,
    new_info: Any,
    source: str,
) -> list[ReconcileOp]:
    """Remove every position term the person's words do not carry (#831).

    Returns ``ops`` unchanged off the guarded doors. Each op is copied, never
    mutated in place."""
    answer = _answer_text(new_info, source)
    if answer is None:
        return ops
    answer_families = _families(answer)
    out: list[ReconcileOp] = []
    for op in ops:
        target = getattr(op, "target", None)
        backed = answer_families | _entry_families(target if isinstance(target, str) else None, profile)
        if len(backed) == len(FAMILIES):
            out.append(op)
            continue
        if isinstance(op, AddBullets):
            resp = _cut_list(op, "responsibilities", op.responsibilities, backed)
            ach = _cut_list(op, "achievements", op.achievements, backed)
            if not (resp or ach or op.technologies):
                continue
            out.append(op.model_copy(update={"responsibilities": resp, "achievements": ach}))
        elif isinstance(op, UpsertWork):
            out.append(op.model_copy(update={"role": ""}) if _drop_title(op, "role", op.role, backed) else op)
        elif isinstance(op, (UpsertProject, UpsertVolunteer)):
            update: dict[str, Any] = {}
            if _drop_title(op, "role", op.role, backed):
                update["role"] = "" if isinstance(op, UpsertVolunteer) else None
            desc = _cut_str(op, "description", op.description, backed)
            if desc != op.description:
                update["description"] = desc or None
            out.append(op.model_copy(update=update) if update else op)
        elif isinstance(op, SetField) and isinstance(op.value, (str, list)):
            if op.field in _TITLE_SET_FIELDS and isinstance(op.value, str):
                if _drop_title(op, op.field, op.value, backed):
                    continue
                out.append(op)
            elif op.field in _TEXT_SET_FIELDS:
                if isinstance(op.value, str):
                    value: Any = _cut_str(op, op.field, op.value, backed)
                else:
                    value = _cut_list(op, op.field, op.value, backed)
                if not value:
                    continue
                out.append(op.model_copy(update={"value": value}) if value != op.value else op)
            else:
                out.append(op)
        elif isinstance(op, UpsertStory):
            update = {}
            for field in ("challenge", "mechanism", "outcome", "benchmark"):
                before = getattr(op, field)
                after = _cut_str(op, field, before, backed)
                if after != before:
                    update[field] = after if field != "benchmark" else (after or None)
            if any(not update.get(f, getattr(op, f)) for f in ("challenge", "mechanism", "outcome")):
                continue
            out.append(op.model_copy(update=update) if update else op)
        elif isinstance(op, FlagConflict) and isinstance(op.incoming, str):
            after = _cut_str(op, "incoming", op.incoming, backed)
            if not after:
                continue
            out.append(op.model_copy(update={"incoming": after}) if after != op.incoming else op)
        else:
            out.append(op)
    return out
