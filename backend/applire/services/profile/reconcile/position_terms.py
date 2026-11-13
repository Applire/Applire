# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Position-claim hold for the ADR-046 reconciler (#831; ADR-046 amended 2026-11-13).

Ground truth (UX audit round 4, 2026-11-07, Sonnet 5.5, testimony door): the
person wrote "Verantwortlich zeichnet unsere Leiterin Rechnungswesen — ich bin
die operative Nummer zwei dafür." The reconciler wrote the bullet "Erstellt seit
2023 die konsolidierten Quartalszahlen in LucaNet mit; operative
Stellvertreterin der Leiterin Rechnungswesen, die verantwortlich zeichnet". "Die
operative Nummer zwei" for one task became a deputy's position in the
organisation, and the vault said so.

Earned (applire-prompt-first, category C). Rule 4 of the reconcile prompt already
forbids embellishing "a date, title, metric". Replays of the captured turn (Sorbet
WP-T, report.md): the incident's own system prompt gave the deputy wording 7/10
on Sonnet and 0/10 on Luna; HEAD 0/8 Sonnet, 0/4 Luna; a 16-case position corpus
0/20 on both. The rate moved with an unrelated rule; this module makes a silent
raise impossible whatever the model.

Founder RULING T-1 (2026-11-13) — the NARROW check, plus ask once:

* It fires only on a POSITION claim: a title tied to an organisational unit
  ("Stellvertreterin der Leiterin X", "Leiterin Rechnungswesen",
  "Abteilungsleiter", "Teamleiterin", "head of / deputy head of <unit>",
  "team lead"), or a change to the edited entry's role field, that neither the
  person's answer nor the edited entry holds. A role within a task or project
  ("Projektleitung für die Migration", "verantwortlich für die Konsolidierung",
  "led the project") passes: a role within a task is not a position in the
  organisation.
* A hit is HELD, never cut silently: the bullet (or role) leaves the op and the
  person is asked ONE keyed question per turn over the existing confirmation
  path (family 7, ``confirmations.position_title_confirmation``). Their answer
  decides (``plan_position_resolution``): the title is written when they
  confirm it, otherwise the task-scoped wording — the bullet without the
  clause that carries the title.

The check is a FACT (ADR-062 cl. 1): a closed pattern — a unit-head or deputy
title, by a closed list of organisational-unit nouns — present in what an op
writes and absent from the answer and the edited entry. Whether the title is
true is the person's answer, never this module's.

Scope: the doors where the person speaks — ``interview`` / ``agent_interview``
(the ANSWER only: the gap label and the question are Applire's words, "a
paraphrase is not a source", ADR-061/048 amended) and ``testimony``. Never a CV
import or a manual edit: there the incoming document's titles are the person's
own.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileOp,
    SetField,
    SetSummary,
    UpsertProject,
    UpsertStory,
    UpsertVolunteer,
    UpsertWork,
)

logger = logging.getLogger(__name__)

#: The doors whose new_info["answer"] is the person's own words.
GUARDED_SOURCES = frozenset({"interview", "agent_interview", "testimony"})

#: ``context["kind"]`` of the ask; the applier dispatches its answer on it.
KIND = "position_title"
#: The answer that writes the title; every other answer writes the task wording.
TITLE_HOLDS = "title_holds"
TASK_ONLY = "task_only"
#: The code-built option keys of a family-7 ask, in order. Their presence on a
#: parked record is what proves code built it (MD-4): the model cannot emit them.
FAMILY_OPTION_KEYS = (TITLE_HOLDS, TASK_ONLY)

# ── the closed vocabulary ────────────────────────────────────────────────────

#: Organisational units — the nouns that make a head/lead word a POSITION.
#: Matched at a word start ("Controlling-Team", "Rechnungswesens"). Projekt,
#: Teilprojekt, Migration, Einführung, Rollout, Workstream … are deliberately
#: absent: a lead of those is a role within a task.
_UNITS_DE = (
    "abteilung", "bereich", "team", "gruppe", "werk", "standort", "niederlassung",
    "filiale", "rechnungswesen", "buchhaltung", "controlling", "finanz", "produktion",
    "fertigung", "einkauf", "vertrieb", "verkauf", "personal", "marketing", "logistik",
    "qualität", "qualitaet", "entwicklung", "forschung", "labor", "station", "pflege",
    "schicht", "instandhaltung", "technik", "kundenservice", "lager", "versand",
    "disposition", "arbeitsvorbereitung", "it", "steuer", "treasury", "konzern",
    "geschäftsbereich", "sparte", "referat", "dezernat", "stabsstelle",
)
_UNITS_EN = (
    "department", "division", "unit", "team", "group", "site", "plant", "office",
    "branch", "accounting", "finance", "controlling", "production", "manufacturing",
    "operations", "sales", "procurement", "purchasing", "hr", "people", "marketing",
    "logistics", "quality", "engineering", "it", "ward", "nursing", "maintenance",
    "warehouse", "customer service", "support", "treasury", "tax", "research",
    "development", "legal", "compliance",
)
_UNIT = "(?:" + "|".join(
    re.escape(u) for u in sorted(set(_UNITS_DE + _UNITS_EN), key=len, reverse=True)
) + ")"
#: A unit noun that heads a project word ("IT-Projekt", "Team project") names
#: a task, not a unit.
_NOT_PROJECT = r"(?![\w-]*(?:projekt|project|programm|program))"
_UNIT_W = _UNIT + _NOT_PROJECT
_HEAD_DE = r"(?:leiter|leiterin|leitung|chef|chefin)"
_DEPUTY_MARK = r"(?:stellvertret\w*|stellvertr\.|stellv\.|stv\.|deputy|vice|vize\w*)"

#: A unit-head title. DE: a compound ("Abteilungsleiterin", "Teamleiter",
#: "Werksleitung") or "Leiter(in) [der/des] [Abteilung] <Einheit>"; EN: "head of
#: <unit>", "<unit> lead/manager/director", "team lead", "director of <unit>".
_UNIT_HEAD = re.compile(
    rf"\b{_UNIT_W}\w*?-?{_HEAD_DE}\b"
    rf"|\b{_HEAD_DE}\s+(?:(?:der|des|im|vom)\s+)?(?:(?:abteilung|bereich|team)\s+)?{_UNIT_W}"
    rf"|\bhead\s+of\s+(?:the\s+)?{_UNIT_W}"
    rf"|\b(?:lead|director|manager)\s+of\s+(?:the\s+)?{_UNIT_W}"
    rf"|\b{_UNIT_W}\s+(?:lead|leader|manager|director|supervisor)\b"
    r"|\bteam\s?lead(?:er)?\b",
    re.IGNORECASE,
)
#: A deputy title: a deputy marker before a head word in the same clause
#: ("Stellvertreterin der Leiterin Rechnungswesen", "stellvertretende
#: Teamleiterin", "Deputy Head of Accounting", "Vice President").
_DEPUTY_TITLE = re.compile(
    rf"\b{_DEPUTY_MARK}[^.;:]{{0,60}}?(?:leiter|leitung|chef|gesch[äa]ftsf[üu]hr|vorstand"
    r"|head|director|manager|lead|chief|president)",
    re.IGNORECASE,
)
#: What HOLDS a unit-head position besides the title itself: the person saying
#: they lead / head / manage the unit ("ich leite das Team Controlling").
_UNIT_HEAD_VERB = re.compile(
    r"\b(?:leite|leitest|leitet|leitete|leiteten|geleitet|führe|fuehre|führte|fuehrte"
    r"|lead|leads|led|head|heads|headed|manage|manages|managed|run|runs|ran)\b"
    rf"[^.;:]{{0,40}}?\b{_UNIT}",
    re.IGNORECASE,
)
_DASHES = re.compile(r"[‐-―−]")
#: Clause boundaries a bullet is cut at: ";", a spaced dash, a sentence end.
_CLAUSE_SPLIT = re.compile(r"(\s*;\s*|\s+[—–-]\s+|(?<=[.!?])\s+)")

# ── whose title is it (MD-8, adversarial F1/F3) ──────────────────────────────
#
# A head or deputy word names a POSITION OF THE PERSON only when nothing marks it
# as someone else's or as negated. Facts (ADR-062 cl. 1), read off the words
# directly before the title in its own clause:
#
# * an article or a possessive right before it ("berichtete an DIE
#   Abteilungsleiterin", "Vertretung DER Teamleiterin im Urlaub", "UNSERE Leiterin
#   Rechnungswesen", "MEINE Stellvertreterin", "reported to THE Head of Finance")
#   makes it a third party's title — unless a self marker precedes the article
#   ("ALS die zuständige Teamleiterin", "worked AS a team lead");
# * a negation before it in the clause ("war NIE Abteilungsleiter");
# * a task scope after it ("Teamlead FÜR DAS PROJEKT X"): a role within a task
#   (T-1) is not a position.
_ARTICLE = (
    r"(?:der|die|den|dem|des|eine|einer|einem|eines|the|a|an"
    r"|mein\w*|unser\w*|sein\w*|ihr\w*|dein\w*|euer\w*|eure\w*"
    r"|my|our|his|her|their|your)"
)
_THIRD_PARTY_BEFORE = re.compile(
    rf"(?:^|\s)(?P<pre>\S+\s+)?{_ARTICLE}\s+(?:[\w-]+\s+){{0,2}}$", re.IGNORECASE,
)
_SELF_BEFORE_ARTICLE = re.compile(
    r"^(?:als|as|bin|war|wurde|bist|is|was|am|became|become)\s+$", re.IGNORECASE,
)
_NEGATION_BEFORE = re.compile(
    r"\b(?:nie|niemals|nicht|kein\w*|never|not|no\s+longer)\b", re.IGNORECASE,
)
_TASK_SCOPE_AFTER = re.compile(
    r"^[^.;:]{0,40}?\b(?:für|fuer|for|im|in)\b[^.;:]{0,30}?"
    r"(?:projekt|project|programm|program|migration|rollout|einführung|einfuehrung)",
    re.IGNORECASE,
)
_CLAUSE_START = re.compile(r"[.;:!?\n]|\s[—–-]\s")
_TAIL_STOP = re.compile(r"[.;:,!?()\n]|\s[—–-]\s")
#: Units that say only "a unit" — matched only when the title names no specific one.
_GENERIC_UNITS = frozenset({
    "abteilung", "bereich", "team", "gruppe", "department", "division", "unit", "group",
})
_UNIT_FIND = re.compile(rf"\b({_UNIT}){_NOT_PROJECT}", re.IGNORECASE)
_FIRST_PERSON = re.compile(
    r"\b(?:ich|mich|mir|mein\w*|i|i'm|i've|i'd|me|my)\b", re.IGNORECASE,
)


def _clause_prefix(folded: str, at: int) -> str:
    starts = [m.end() for m in _CLAUSE_START.finditer(folded, 0, at)]
    return folded[(starts[-1] if starts else 0):at]


def _is_persons(folded: str, start: int, end: int) -> bool:
    """Whether the title at ``folded[start:end]`` is claimed FOR THE PERSON:
    not someone else's (article / possessive), not negated, not task-scoped."""
    prefix = _clause_prefix(folded, start)
    third = _THIRD_PARTY_BEFORE.search(prefix)
    if third is not None:
        pre = third.group("pre") or ""
        if not _SELF_BEFORE_ARTICLE.match(pre):
            return False
    if _NEGATION_BEFORE.search(prefix):
        return False
    return not _TASK_SCOPE_AFTER.match(folded[end:])


def _units(folded: str, start: int, end: int) -> frozenset[str]:
    """The unit nouns the title at ``[start:end]`` names: in the title itself
    and in the rest of its clause segment (≤ 40 chars, cut at a comma)."""
    tail = folded[end:end + 40]
    stop = _TAIL_STOP.search(tail)
    span = folded[start:end] + (tail[:stop.start()] if stop else tail)
    return frozenset(m.group(1).lower() for m in _UNIT_FIND.finditer(span))


def _claim_spans(folded: str) -> list[tuple[str, int, int]]:
    """``(family, start, end)`` of every title FOR THE PERSON in ``folded``.
    A deputy title wins over the head title it contains."""
    deputy = [("deputy", m.start(), m.end()) for m in _DEPUTY_TITLE.finditer(folded)
              if _is_persons(folded, m.start(), m.end())]
    if deputy:
        return deputy
    return [("unit_head", m.start(), m.end()) for m in _UNIT_HEAD.finditer(folded)
            if _is_persons(folded, m.start(), m.end())]


def position_claims(text: str) -> list[tuple[str, str]]:
    """``(family, phrase)`` for every position claim FOR THE PERSON in ``text`` —
    family ``deputy`` or ``unit_head``."""
    folded = _fold(text)
    return [(f, folded[a:b]) for f, a, b in _claim_spans(folded)]


#: A claim key: the family and the units the title names (MD-8 F1: held per
#: title — title word + unit — never per family).
TitleKey = tuple[str, frozenset[str]]


def _claim_keys(text: str) -> list[tuple[TitleKey, str]]:
    folded = _fold(text)
    return [((f, _units(folded, a, b)), folded[a:b]) for f, a, b in _claim_spans(folded)]


def _held_titles(text: str, *, first_person: bool) -> set[TitleKey]:
    """The titles ``text`` HOLDS for the person: its own claims, and the person
    saying they lead the unit. ``first_person``: for the person's ANSWER, only a
    sentence that speaks as "ich / I" holds anything (a third party's title
    named without an article — "Frau Weber ist Teamleiterin" — holds nothing)."""
    held: set[TitleKey] = set()
    parts = _SENTENCE_SPLIT.split(text) if first_person else [text]
    for part in parts:
        if first_person and not _FIRST_PERSON.search(part):
            continue
        held.update(k for k, _p in _claim_keys(part))
        folded = _fold(part)
        for m in _UNIT_HEAD_VERB.finditer(folded):
            if _NEGATION_BEFORE.search(_clause_prefix(folded, m.start()) + " " + m.group(0)):
                continue  # "ich habe nie …", "ich leite nicht das Team …"
            if _PROJECT_WORD.search(m.group(0)):
                continue  # "ich leite das Projekt im Team X" leads a task, not the unit
            held.add(("unit_head", _units(folded, m.start(), m.end())))
    return held


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_PROJECT_WORD = re.compile(r"projekt|project|programm|program", re.IGNORECASE)


def _key_held(key: TitleKey, held: set[TitleKey]) -> bool:
    family, units = key
    specific = units - _GENERIC_UNITS
    for h_family, h_units in held:
        if h_family != family:
            continue
        h_specific = h_units - _GENERIC_UNITS
        if specific:
            if specific & h_specific:
                return True
        elif not h_specific and (units & h_units or not units):
            return True
    return False


def _fold(text: str) -> str:
    return _DASHES.sub("-", unicodedata.normalize("NFKC", text or "").casefold())


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


def _entry(target: str | None, profile: MasterProfileData) -> Any | None:
    if not target:
        return None
    for section in ("work_experience", "projects", "volunteer_activities"):
        for entry in getattr(profile, section, None) or []:
            if getattr(entry, "id", None) == target:
                return entry
    return None


def _entry_text(entry: Any) -> str:
    return " \n ".join(_strings(entry.model_dump(mode="json"))) if entry is not None else ""


def _unbacked(text: str, held: set[TitleKey]) -> list[tuple[str, str]]:
    """The person's titles in ``text`` that ``held`` does not hold — per title
    (family + unit), MD-8 F1."""
    return [(k[0], p) for k, p in _claim_keys(text) if not _key_held(k, held)]


_AS_WORD = re.compile(r"\b(?:als|as)\s+", re.IGNORECASE)
_WORD = re.compile(r"\S+")
_LINKERS = frozenset({"der", "des", "of", "the"})


def _bare(word: str) -> str:
    return word.strip(",.;:()").casefold()


def _drop_as_title(clause: str, held: set[TitleKey]) -> str | None:
    """``clause`` without its "als <Titel> [Einheit]" / "as <title> [of unit]"
    phrase, when that alone removes every unheld title ("Koordinierte als
    Teamleiterin Controlling die Konsolidierung" → "Koordinierte die
    Konsolidierung"). ``None`` when no such phrase does it (MD-8 F3)."""
    for m in _AS_WORD.finditer(clause):
        words = list(_WORD.finditer(clause, m.end()))
        for k in range(1, min(6, len(words)) + 1):
            j = k
            while j < len(words):
                w = _bare(words[j].group(0))
                if _UNIT_FIND.fullmatch(w):
                    j += 1
                elif w in _LINKERS and j + 1 < len(words) and _UNIT_FIND.fullmatch(
                    _bare(words[j + 1].group(0))
                ):
                    j += 2
                else:
                    break
            cut = clause[:m.start()] + clause[words[j - 1].end():]
            cut = re.sub(r"\s{2,}", " ", cut).strip()
            if cut and not _unbacked(cut, held):
                return cut
    return None


def task_wording(text: str, held: set[TitleKey]) -> str:
    """``text`` without the person's unheld titles — the task-scoped wording the
    person gets when they do not confirm the title: a clause's "als <Titel>"
    phrase where that suffices, else the whole clause that carries it."""
    parts = _CLAUSE_SPLIT.split(text)
    clauses, seps = parts[0::2], parts[1::2]
    kept: list[str] = []
    for i, clause in enumerate(clauses):
        if not clause.strip():
            continue
        if _unbacked(clause, held):
            reduced = _drop_as_title(clause, held)
            if reduced is None:
                continue
            clause = reduced
        if kept:
            kept.append(seps[i - 1])
        kept.append(clause)
    return "".join(kept).strip().rstrip(";,—–-").strip()


def _claim_clause(text: str, held: set[TitleKey]) -> str:
    for clause in _CLAUSE_SPLIT.split(text)[0::2]:
        if clause.strip() and _unbacked(clause, held):
            return clause.strip().rstrip(".;,")
    return text.strip()


def _log(op: ReconcileOp, field: str, hits: list[tuple[str, str]]) -> None:
    logger.info(
        "RECONCILE_POSITION_CLAIM_HELD op=%s target=%s field=%s claims=%s",
        getattr(op, "op", "?"), getattr(op, "target", None), field,
        sorted({p for _f, p in hits}),
    )


def _norm_role(value: str) -> str:
    return re.sub(r"\s+", " ", _fold(value)).strip()


def _role_change_unheld(value: str, entry: WorkEntry, answer: str) -> bool:
    """A change to an EXISTING entry's role that neither the answer nor the entry
    holds: the new value is not the entry's role or an alias, and the answer does
    not contain it."""
    v = _norm_role(value)
    if not v:
        return False
    known = {_norm_role(entry.role or "")} | {_norm_role(a) for a in entry.role_aliases}
    if v in known:
        return False
    return v not in _norm_role(answer)


def enforce_position_terms(
    ops: list[ReconcileOp],
    *,
    profile: MasterProfileData,
    new_info: Any,
    source: str,
) -> list[ReconcileOp]:
    """Hold every position claim the person's words do not carry (#831, T-1).

    Returns ``ops`` unchanged off the guarded doors. Held content leaves its op
    (copied, never mutated in place) and is carried in ONE appended
    ``request_confirmation`` for the whole turn."""
    answer = _answer_text(new_info, source)
    if answer is None:
        return ops
    answer_held = _held_titles(answer, first_person=True)
    out: list[ReconcileOp] = []
    held_items: list[dict[str, Any]] = []

    def _hold_text(op: ReconcileOp, field: str, value: str, held: set[TitleKey]) -> bool:
        """MD-8 F2 — a free-text field (summary, description, story, a
        project/volunteer role) carrying an unheld title: recorded as a
        ``text`` item. Family 7's allow-list cannot write it back, so a "yes"
        leaves it unwritten (``confirmation_unresolvable``) — never written
        unasked."""
        hits = _unbacked(value, held)
        if not hits:
            return False
        _log(op, field, hits)
        held_items.append({
            "item": "text", "op": getattr(op, "op", "?"),
            "target": getattr(op, "target", None), "field": field, "text": value,
            "claim": _claim_clause(value, held), "task_text": task_wording(value, held),
        })
        return True

    for op in ops:
        target = getattr(op, "target", None)
        entry = _entry(target if isinstance(target, str) else None, profile)
        held = answer_held | _held_titles(_entry_text(entry), first_person=False)
        if isinstance(op, AddBullets) or (
            isinstance(op, SetField) and op.field in ("responsibilities", "achievements")
            and isinstance(op.value, list)
        ):
            fields = (
                ("responsibilities", "achievements") if isinstance(op, AddBullets) else (op.field,)
            )
            update: dict[str, list[Any]] = {}
            for field in fields:
                current = getattr(op, field) if isinstance(op, AddBullets) else list(op.value)
                kept: list[Any] = []
                for bullet in current:
                    hits = _unbacked(bullet, held) if isinstance(bullet, str) else []
                    if not hits:
                        kept.append(bullet)
                        continue
                    _log(op, field, hits)
                    held_items.append({
                        "item": "bullet", "target": op.target, "field": field,
                        "text": bullet, "claim": _claim_clause(bullet, held),
                        "task_text": task_wording(bullet, held),
                    })
                if len(kept) != len(current):
                    update[field] = kept
            if not update:
                out.append(op)
            elif isinstance(op, SetField):
                if update[op.field]:
                    out.append(op.model_copy(update={"value": update[op.field]}))
            elif (
                any(update.get(f, getattr(op, f)) for f in ("responsibilities", "achievements"))
                or op.technologies
            ):
                out.append(op.model_copy(update=update))
            continue
        role_field: str | None = None
        value = ""
        if isinstance(op, UpsertWork) and op.role:
            role_field, value = "role", op.role
        elif isinstance(op, SetField) and op.field in ("role", "title") and isinstance(op.value, str):
            role_field, value = op.field, op.value
        if role_field is not None:
            existing = entry if isinstance(entry, WorkEntry) else None
            hits = _unbacked(value, held)
            if not hits and existing is not None and _role_change_unheld(value, existing, answer):
                hits = [("role_change", _norm_role(value))]
            if hits:
                _log(op, role_field, hits)
                held_items.append({
                    "item": "role", "target": existing.id if existing is not None else None,
                    "company": getattr(op, "company", None)
                    or (existing.company if existing is not None else None),
                    "field": "role", "text": value, "claim": value, "task_text": "",
                })
                if isinstance(op, UpsertWork):
                    out.append(op.model_copy(update={"role": ""}))
                continue
        # MD-8 F2 — every other field that can carry a title, through the same check.
        if isinstance(op, (UpsertProject, UpsertVolunteer)):
            update_fields: dict[str, Any] = {}
            if isinstance(op.role, str) and _hold_text(op, "role", op.role, held):
                update_fields["role"] = "" if isinstance(op, UpsertVolunteer) else None
            if isinstance(op.description, str) and _hold_text(op, "description", op.description, held):
                update_fields["description"] = None
            out.append(op.model_copy(update=update_fields) if update_fields else op)
            continue
        if isinstance(op, SetSummary):
            if not _hold_text(op, "summary", op.text, held):
                out.append(op)
            continue
        if isinstance(op, UpsertStory):
            fields = ("title", "challenge", "mechanism", "outcome", "benchmark")
            hit = [f for f in fields if isinstance(getattr(op, f), str)
                   and _hold_text(op, f, getattr(op, f), held)]
            if not hit:
                out.append(op)
            continue
        if isinstance(op, SetField) and isinstance(op.value, (str, list)):
            values = [op.value] if isinstance(op.value, str) else [
                v for v in op.value if isinstance(v, str)
            ]
            if any([_hold_text(op, op.field, v, held) for v in values]):
                continue
        out.append(op)
    if held_items:
        from applire.services.profile.reconcile.confirmations import position_title_confirmation

        out.append(position_title_confirmation(
            items=held_items, context={"kind": KIND, "items": held_items},
        ))
    return out


# ── the answer (family 7) ────────────────────────────────────────────────────


def plan_position_resolution(
    context: dict[str, Any], option_key: str | None
) -> list[tuple[dict[str, Any], str]]:
    """``(item, text_to_write)`` per held item — the title when the person
    confirmed it, otherwise the task-scoped wording (empty: nothing to write).

    Pure; the applier executes it. Any answer but ``title_holds`` — a free-text
    agent answer that matches no option included — writes the task wording: the
    title is written only on the person's explicit yes (T-1)."""
    confirmed = option_key == TITLE_HOLDS
    plan: list[tuple[dict[str, Any], str]] = []
    for item in context.get("items") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "") if confirmed else str(item.get("task_text") or "")
        plan.append((item, text.strip()))
    return plan
