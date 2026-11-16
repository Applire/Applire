# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Q8-6 (founder ruling, Sorbet quality wave 2b) — a corrected team size meets
a bullet that still names the old number.

**Why.** The delivery run on 82154267 answered Stefan's kind question with the
chip and a corrected number ("… – 12 Personen" over a stored 14). The answer
wrote ``team_size`` 12, and the CV printed "Führung von 12 Mitarbeitenden" and
his own bullet "Schicht mit 14 Mitarbeitenden" side by side; two panel readers
named the contradiction.

**The rule (ask right away).** When the kind answer changes a stored size,
every bullet of that role that names the OLD number as a people count gets one
follow-up: "Du hast die Teamgröße auf 12 korrigiert. Dein Punkt nennt noch 14:
»…«" [Auf 12 ändern] [So lassen].

* "Ändern" changes ONLY the qualifying number token(s) in that bullet; the old
  text stays in the edit history (the receipt). The person's own decision, so a
  legitimate write, guarded like the kind answer: only from the exact closed
  answer (adapter-only :class:`ResolveSizeCorrection`), never model-emitted.
* "So lassen" keeps the bullet; the record stays (``kept``).
* While a record (``open`` or ``kept``) still names a bullet of the entry, the
  CV prints no leadership line for that role (:func:`size_line_withheld`) and
  no prompt view carries the role's ``team_size`` — the two numbers never
  appear together (an unanswered follow-up included).

**Which bullets qualify — structural, a doubt asks** (a question too many is
the cheap error). The old number as a WHOLE token (not inside "2014", "14,5",
"1.400"), and

* (a) a people word within the next two words ("14 Mitarbeitenden", "14
  direkten Mitarbeitern", "14 people", "14-köpfiges Team"), or
* (b) doubt: the bullet speaks about leading (a lead stem — "Team",
  "Führung", "led" …) and the word after the number is not a known
  non-people unit ("%", "Mio", "Maschinen", "Jahre", "Projekte" …): "Team von
  14", "Führte 14 im Werk Kassel".

The word lists can only add a QUESTION, never a write.
"""
from __future__ import annotations

import re
from typing import Any

from applire.schemas.profile import FieldChange, MasterProfileData, SizeCorrection

RATIONALE_CHANGED = "team_size_bullet_changed"
RATIONALE_KEPT = "team_size_bullet_kept"
_FIELDS = ("responsibilities", "achievements")

#: People words, as stems (a bullet's word STARTS with one).
_PEOPLE = (
    "mitarbeit", "person", "beschäftigt", "kolleg", "fachkr", "angestellt", "azubi",
    "auszubild", "werkstud", "praktikant", "schichtleit", "meister", "ingenieur",
    "entwickl", "technik", "köpf", "leute", "fte", "vollzeit", "direct", "direkt",
    "people", "employee", "staff", "report", "engineer", "developer", "member",
    "operator", "worker", "colleague", "technician", "headcount", "head", "analyst",
    "consultant", "berater", "controller", "student", "trainee", "apprentice",
)
#: Words that make the number NOT a people count (stems).
_NOT_PEOPLE = (
    "prozent", "percent", "mio", "million", "mrd", "milliard", "billion", "tsd",
    "tausend", "thousand", "eur", "euro", "usd", "chf", "jahr", "year", "monat",
    "month", "woch", "week", "tag", "day", "stund", "hour", "maschin", "machine",
    "anlag", "linie", "line", "werk", "plant", "site", "standort", "location",
    "projekt", "project", "kund", "customer", "client", "produkt", "product",
    "artikel", "auftr", "order", "schicht", "shift", "sprint", "release", "land",
    "länder", "countr", "markt", "market", "filial", "branch", "system", "server",
    "service", "dienst", "lieferant", "supplier",
)
#: Stems that make a bullet speak about leading people (for the doubt arm).
_LEAD = (
    "führ", "leit", "lead", "manag", "team", "verantwort", "betreu", "anleit",
    "coach", "mentor", "superv", "report", "personal", "disziplin",
)
_WORD = re.compile(r"\w+")


def _number_spans(text: str, n: int) -> list[tuple[int, int]]:
    """Where ``n`` stands as a whole number: not part of a longer digit run,
    not a decimal or thousands part ("14,5", "1.400", "2014")."""
    pat = re.compile(rf"(?<![\d.,]){n}(?![\d]|[.,]\d)")
    return [(m.start(), m.end()) for m in pat.finditer(text or "")]


def _starts(word: str, stems: tuple[str, ...]) -> bool:
    return any(word.startswith(s) for s in stems)


def qualifying_spans(text: str, old: int) -> list[tuple[int, int]]:
    """The spans of ``old`` in ``text`` that name a people count (module doc:
    (a) a people word next, (b) doubt)."""
    out = []
    folded = (text or "").casefold()
    leads = any(_starts(w, _LEAD) for w in _WORD.findall(folded))
    for start, end in _number_spans(text, old):
        rest = folded[end:]
        after = _WORD.findall(rest)[:2]
        symbol = rest.lstrip()[:1]
        if any(_starts(w, _PEOPLE) for w in after):
            out.append((start, end))
        elif symbol in ("%", "€", "$") or (after and _starts(after[0], _NOT_PEOPLE)):
            continue
        elif leads:
            out.append((start, end))
    return out


def corrections_for(entry: Any, old: Any, new: int) -> list[SizeCorrection]:
    """The records a corrected size (``old`` → ``new``) leaves on ``entry``."""
    if not isinstance(old, int) or isinstance(old, bool) or old <= 0 or old == new:
        return []
    out = []
    for field in _FIELDS:
        for bullet in getattr(entry, field, None) or []:
            if isinstance(bullet, str) and qualifying_spans(bullet, old):
                out.append(SizeCorrection(text=bullet, field=field, old=old, new=new))
    return out


def changed_text(text: str, old: int, new: int) -> str:
    """``text`` with ONLY its qualifying ``old`` number token(s) set to ``new``."""
    spans = qualifying_spans(text, old)
    for start, end in reversed(spans):
        text = text[:start] + str(new) + text[end:]
    return text


def _entry(profile: MasterProfileData, entry_id: str) -> Any:
    for e in profile.work_experience:
        if str(e.id) == str(entry_id):
            return e
    return None


class SizeCorrectionNotFound(LookupError):
    """No open size-correction record with that text on that entry."""


def apply_resolve(op: Any, profile: MasterProfileData, changes: list[FieldChange]) -> None:
    """The committer's branch for :class:`ResolveSizeCorrection`."""
    entry = _entry(profile, op.entry_id)
    record = next((r for r in (entry.size_corrections if entry else []) if r.text == op.text), None)
    if record is None:
        # Never the bullet text in the error (log hygiene).
        raise SizeCorrectionNotFound("no size-correction record with that text on the entry")
    label = entry.company or entry.role or entry.id
    if op.decision == "keep":
        record.status = "kept"
        changes.append(FieldChange(
            section="work_experience", field="size_corrections", action="updated",
            entity_id=entry.id, rationale_key=RATIONALE_KEPT,
            rationale=(f"Kept on {label!r}: the candidate left the bullet as it is; the CV "
                       "prints no team-size line for this role."),
        ))
        return
    bullets = list(getattr(entry, record.field) or [])
    entry.size_corrections = [r for r in entry.size_corrections if r is not record]
    if record.text not in bullets:
        return
    new_text = changed_text(record.text, record.old, record.new)
    bullets[bullets.index(record.text)] = new_text
    setattr(entry, record.field, bullets)
    changes.append(FieldChange(
        section="work_experience", field=record.field, action="updated",
        old_value=record.text, new_value=new_text, entity_id=entry.id,
        rationale_key=RATIONALE_CHANGED,
        rationale=(f"Changed on {label!r}: the candidate set the team size in this bullet "
                   f"from {record.old} to {record.new}."),
    ))


def size_line_withheld(entry: Any) -> bool:
    """True while a size-correction record still names a bullet of ``entry``
    (open or kept): the CV prints no leadership line for the role, and no
    prompt view carries its ``team_size``. Reads a dict or a model."""
    get = entry.get if isinstance(entry, dict) else (lambda k, d=None: getattr(entry, k, d))
    for r in get("size_corrections") or []:
        text = r.get("text") if isinstance(r, dict) else getattr(r, "text", None)
        field = (r.get("field") if isinstance(r, dict) else getattr(r, "field", None)) or "responsibilities"
        if text and text in (get(field) or []):
            return True
    return False


def restore_size_corrections(
    before: MasterProfileData, after: MasterProfileData, changes: list[FieldChange],
    touched: set[str], merged: list[tuple[str, str]] | None = None,
) -> list[FieldChange]:
    """Class guard: an entry neither the kind answer nor a resolve op of this
    batch touched keeps the records it held before the batch (none for a new
    entry); an entry merge carries the source's records to the target (in
    batch order); a receipt another op wrote on the field is dropped."""
    held = {str(e.id): [r.model_copy() for r in e.size_corrections] for e in before.work_experience}
    for target, source in merged or []:
        into = held.setdefault(str(target), [])
        texts = {r.text for r in into}
        for r in held.pop(str(source), []):
            if r.text not in texts:
                into.append(r)
                texts.add(r.text)
    restored: set[str] = set()
    for entry in after.work_experience:
        eid = str(entry.id)
        if eid in touched:
            continue
        want = held.get(eid, [])
        if [r.model_dump() for r in entry.size_corrections] != [r.model_dump() for r in want]:
            entry.size_corrections = want
            restored.add(eid)
    return [
        c for c in changes
        if not (c.field == "size_corrections" and (str(c.entity_id or "") in restored or c.entity_id is None))
    ]


def prune_size_corrections(profile: MasterProfileData) -> None:
    """A record whose bullet is no longer a bullet of its entry ends."""
    for entry in profile.work_experience:
        if entry.size_corrections:
            entry.size_corrections = [
                r for r in entry.size_corrections if r.text in (getattr(entry, r.field) or [])
            ]


def keep_size_corrections(section: str, before: Any, after: Any) -> Any:
    """The section door (PATCH, MCP ``update_profile``): each entry keeps the
    records the entry with that id held, whatever the payload says."""
    if section != "work_experience" or not isinstance(after, list):
        return after
    held = {
        str(e["id"]): e.get("size_corrections")
        for e in before or [] if isinstance(e, dict) and e.get("id")
    }
    result = []
    for entry in after:
        if isinstance(entry, dict):
            entry = dict(entry)
            kept = held.get(str(entry.get("id") or ""))
            if kept:
                entry["size_corrections"] = kept
            else:
                entry.pop("size_corrections", None)
        result.append(entry)
    return result


def open_corrections(profile_json: Any) -> list[dict[str, Any]]:
    """The interview's follow-up asks: every ``open`` record whose bullet is
    still a bullet of its entry, as ``{entry_id, company, role, text, field,
    old, new}`` in entry order."""
    out = []
    data = profile_json if isinstance(profile_json, dict) else {}
    for e in data.get("work_experience") or []:
        if not isinstance(e, dict):
            continue
        for r in e.get("size_corrections") or []:
            if not isinstance(r, dict) or r.get("status", "open") != "open":
                continue
            field = r.get("field") or "responsibilities"
            if r.get("text") in (e.get(field) or []):
                out.append({
                    "entry_id": str(e.get("id") or ""), "company": str(e.get("company") or ""),
                    "role": str(e.get("role") or ""), "text": r["text"], "field": field,
                    "old": r.get("old"), "new": r.get("new"),
                })
    return out
