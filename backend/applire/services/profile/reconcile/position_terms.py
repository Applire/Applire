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
_DEPUTY_MARK = r"(?:stellvertret\w*|stellv\.|stv\.|deputy|vice|vize\w*)"

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
_DEPUTY_HELD = re.compile(rf"\b{_DEPUTY_MARK}", re.IGNORECASE)
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


def _fold(text: str) -> str:
    return _DASHES.sub("-", unicodedata.normalize("NFKC", text or "").casefold())


def position_claims(text: str) -> list[tuple[str, str]]:
    """``(family, phrase)`` for every position claim in ``text`` — family
    ``deputy`` or ``unit_head``. A deputy title wins over the head title it
    contains."""
    folded = _fold(text)
    deputy = [("deputy", m.group(0)) for m in _DEPUTY_TITLE.finditer(folded)]
    if deputy:
        return deputy
    return [("unit_head", m.group(0)) for m in _UNIT_HEAD.finditer(folded)]


def _held_families(text: str) -> set[str]:
    """The claim families ``text`` already HOLDS (the answer, the edited entry)."""
    folded = _fold(text)
    held: set[str] = set()
    if _DEPUTY_HELD.search(folded):
        held.add("deputy")
    if _UNIT_HEAD.search(folded) or _UNIT_HEAD_VERB.search(folded):
        held.add("unit_head")
    return held


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
    for section in ("work_experience", "projects"):
        for entry in getattr(profile, section, None) or []:
            if getattr(entry, "id", None) == target:
                return entry
    return None


def _entry_text(entry: Any) -> str:
    return " \n ".join(_strings(entry.model_dump(mode="json"))) if entry is not None else ""


def _unbacked(text: str, held: set[str]) -> list[tuple[str, str]]:
    return [(f, p) for f, p in position_claims(text) if f not in held]


def task_wording(text: str, held: set[str]) -> str:
    """``text`` without the clauses that carry an unheld position claim — the
    task-scoped wording the person gets when they do not confirm the title."""
    parts = _CLAUSE_SPLIT.split(text)
    clauses, seps = parts[0::2], parts[1::2]
    kept: list[str] = []
    for i, clause in enumerate(clauses):
        if not clause.strip() or _unbacked(clause, held):
            continue
        if kept:
            kept.append(seps[i - 1])
        kept.append(clause)
    return "".join(kept).strip().rstrip(";,—–-").strip()


def _claim_clause(text: str, held: set[str]) -> str:
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
    answer_held = _held_families(answer)
    out: list[ReconcileOp] = []
    held_items: list[dict[str, Any]] = []
    for op in ops:
        target = getattr(op, "target", None)
        entry = _entry(target if isinstance(target, str) else None, profile)
        held = answer_held | _held_families(_entry_text(entry))
        if isinstance(op, AddBullets):
            update: dict[str, list[str]] = {}
            for field in ("responsibilities", "achievements"):
                kept: list[str] = []
                for bullet in getattr(op, field):
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
                if len(kept) != len(getattr(op, field)):
                    update[field] = kept
            if not update:
                out.append(op)
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
