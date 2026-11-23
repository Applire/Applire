# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import restatement check (ADR-046 + ADR-063 amended 2026-10-22, D-B3V6;
founder ruling V-3 = A).

**Why it exists — prompt-first category C.** A second source merged onto a CV
(LinkedIn/XING after the CV) adds its bullets to the roles the CV already
described, and most of them restate a held bullet in other words (UX audit
2026-10-20, Felix and Marcus). The reconcile prompt has asked since build 2 not
to add a bullet "a SECOND TIME"; the replay of the two captured import calls
(records 182 Daniel LinkedIn→CV and 71 Stefan XING→CV, luna, thinking off,
n = 5) showed the rule cut the volume (41→11, 41→25) and was still violated by
most survivors; a stronger clause gained nothing (20 / 15).

**What it is.** One extra LLM call per import merge, between the reconciler's
ops and ``apply_ops``: for every ``add_bullets`` op onto an EXISTING entry that
already holds bullets, the model names the new bullets that state no fact the
held ones do not (a judgement — the model's, ADR-062 clause 1). Code removes
exactly the bullets the model named by a valid id and returns a
``RestatedReceipt`` for each; nothing held is touched, nothing is deleted.

**Fail-open.** Any error, truncation, non-JSON answer or invalid id leaves the
ops unchanged — the behaviour before this check existed.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from applire.prompts.restatement import RESTATEMENT_SYSTEM_PROMPT, build_restatement_prompt
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
from applire.schemas.profile import MasterProfileData, RestatedReceipt
from applire.services.profile.entity_label import label_for
from applire.services.oracle.matchers.figures import extract_figures
from applire.services.profile.reconcile.ops import AddBullets, ReplaceBullet
from applire.content_log import content_logger

logger = logging.getLogger(__name__)
clog = content_logger(__name__)

_ENGAGEMENT_SECTIONS = ("work_experience", "projects", "volunteer_activities")
_BULLET_FIELDS = ("responsibilities", "achievements")
_ID = re.compile(r"^e(\d+)\.([hn])(\d+)$")
RESTATEMENT_MAX_TOKENS = 2048


def _held_entities(profile: MasterProfileData) -> dict[str, tuple[str, Any]]:
    out: dict[str, tuple[str, Any]] = {}
    for section in _ENGAGEMENT_SECTIONS:
        for entity in getattr(profile, section, None) or []:
            entity_id = getattr(entity, "id", None)
            if entity_id:
                out[str(entity_id)] = (section, entity)
    return out


def _held_bullets(entity: Any) -> list[str]:
    return [
        b for f in _BULLET_FIELDS for b in (getattr(entity, f, None) or [])
        if isinstance(b, str) and b.strip()
    ]


def _held_for(profile: MasterProfileData, section: str, entity: Any) -> list[str]:
    """What the profile already says about this entry (D-17 residue, delivery
    run 4): its own bullets, its ``not_yet_reached`` aims, and the bullets of
    its KIN — a project's position (``associated_experience``) and a
    position's projects. A project and its position are one engagement on the
    CV, so a fact the one holds is not new on the other."""
    held = _held_bullets(entity) + [
        b for b in (getattr(entity, "not_yet_reached", None) or []) if isinstance(b, str) and b.strip()
    ]
    entity_id = str(getattr(entity, "id", "") or "")
    if section == "projects":
        parent = str(getattr(entity, "associated_experience", "") or "")
        kin = [e for s in ("work_experience", "volunteer_activities") for e in getattr(profile, s, None) or []
               if str(getattr(e, "id", "") or "") == parent]
    else:
        kin = [p for p in profile.projects if entity_id and str(p.associated_experience or "") == entity_id]
    for other in kin:
        held.extend(_held_bullets(other))
    return list(dict.fromkeys(held))


def _parse_pairs(data: Any) -> list[tuple[int, int, int]]:
    """``(entry, new, held)`` 1-based triples from the model's answer; anything
    malformed is dropped (never guessed)."""
    pairs: list[tuple[int, int, int]] = []
    items = data.get("items") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        # The model names the words of the NEW bullet no held bullet states;
        # only an item whose `added` is present AND empty restates (a missing
        # key is malformed, never read as "nothing added").
        added = item.get("added")
        if not isinstance(added, str) or added.strip():
            continue
        new = _ID.match(str(item.get("new") or "").strip())
        held = _ID.match(str(item.get("held") or "").strip())
        if not new or not held or new.group(2) != "n" or held.group(2) != "h":
            continue
        if new.group(1) != held.group(1):
            continue
        pairs.append((int(new.group(1)), int(new.group(3)), int(held.group(3))))
    return pairs


#: A bullet's tokens: a word, a number, or a name with its ``+``/``#`` tail
#: ("C++", "C#"); hyphens and slashes split ("SAP-Modul" -> "SAP", "Modul").
_TOK = re.compile(r"\w+[+#]*")
#: The new bullet's clause marks: a clause is the text between two of them.
_CLAUSE_MARK = re.compile(r"[.;:!?,()\[\]\u2013\u2014]|\s-\s")
#: Negation words (German, English) of ANY length. Lexical on purpose: it can
#: only BLOCK a replace (MD-Q17 — a lexical blocker is acceptable because the
#: failing side keeps both bullets).
_NEGATORS = frozenset({
    "nie", "niemals", "nicht", "nichts", "kein", "keine", "keinen", "keinem", "keiner",
    "keines", "ohne", "weder", "niemand", "nirgends",
    "no", "not", "never", "none", "nor", "neither", "without", "nothing", "nobody",
    "cannot", "didn", "doesn", "don", "isn", "wasn", "aren", "weren", "hasn", "haven",
    "hadn", "couldn", "wouldn", "shouldn",
})
#: Number words that bind to the word after them like a figure does.
_NUMBER_WORDS = frozenset({
    "ein", "eine", "einen", "einem", "einer", "zwei", "drei", "vier", "fünf", "sechs",
    "sieben", "acht", "neun", "zehn", "elf", "zwölf", "dutzend", "hundert", "tausend",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "dozen", "hundred", "thousand",
})


def _tokens(text: str) -> list[tuple[str, bool, int, int]]:
    """``(folded token, exact, start, end)``: ``exact`` for a token whose spelling
    is the fact itself — a number, a name (two capitals: "SAP", "KVP", "4HANA"),
    a negation or a number word — which never matches another ending."""
    out = []
    for m in _TOK.finditer(text or ""):
        tok = m.group()
        folded = tok.casefold()
        exact = (
            any(c.isdigit() for c in tok) or sum(c.isupper() for c in tok) >= 2
            or folded in _NEGATORS or folded in _NUMBER_WORDS
        )
        out.append((folded, exact, m.start(), m.end()))
    return out


#: The inflection endings two spellings of one word may differ by ("Werk" /
#: "Werke", "fachliche" / "fachlichen", "reduziert" / "reduzierte").
_ENDINGS = frozenset({"", "e", "n", "en", "er", "es", "s", "em", "ern", "ed", "d"})


def _same_word(a: str, b: str) -> bool:
    """Equal, or one word with another inflection ending: a common stem of at
    least four letters and both rests in ``_ENDINGS`` ("Hand" is not "Handel")."""
    if a == b:
        return True
    for cut in range(4, min(len(a), len(b)) + 1):
        if a[:cut] != b[:cut]:
            break
        if a[cut:] in _ENDINGS and b[cut:] in _ENDINGS:
            return True
    return False


def _in_order(old: list, new: list) -> list[int] | None:
    """The positions in ``new`` where EVERY token of ``old`` reappears, in
    order (the leftmost such placement), or ``None``. An exact token matches
    only itself; another token may differ in its ending. A number or number
    word stays bound to the token after it: the two are adjacent in ``new``
    too ("14 Mitarbeitende" is not "14 Projekte mit Mitarbeitende")."""
    n, m = len(old), len(new)

    def same(i: int, k: int) -> bool:
        o, n_ = old[i], new[k]
        return o[0] == n_[0] if (o[1] or n_[1]) else _same_word(o[0], n_[0])

    def bound(i: int) -> bool:
        tok = old[i][0]
        return i + 1 < n and (any(c.isdigit() for c in tok) or tok in _NUMBER_WORDS)

    memo: dict[tuple[int, int], bool] = {}

    def at(i: int, k: int) -> bool:
        """``old[i]`` placed at ``new[k]`` and the rest placeable after it."""
        key = (i, k)
        if key not in memo:
            ok = same(i, k)
            if ok and i + 1 < n:
                if bound(i):
                    ok = k + 1 < m and at(i + 1, k + 1)
                else:
                    ok = any(at(i + 1, k2) for k2 in range(k + 1, m))
            memo[key] = ok
        return memo[key]

    placed: list[int] = []
    k = 0
    for i in range(n):
        if placed and bound(i - 1):
            k = placed[-1] + 1
            if not (k < m and at(i, k)):
                return None
            placed.append(k)
            continue
        while k < m and not at(i, k):
            k += 1
        if k >= m:
            return None
        placed.append(k)
        k += 1
    return placed


def _adds_negation(new_text: str, new: list, placed: list[int]) -> bool:
    """A negation word of ``new`` that no old token placed, in a clause of
    ``new`` that holds an old token."""
    marks = [m.start() for m in _CLAUSE_MARK.finditer(new_text)]

    def clause(pos: int) -> int:
        return sum(1 for x in marks if x < pos)

    held = {clause(new[k][2]) for k in placed}
    used = set(placed)
    return any(
        tok[0] in _NEGATORS and k not in used and clause(tok[2]) in held
        for k, tok in enumerate(new)
    )


def blocking_check(old: str, new: str) -> str | None:
    """Q3-4's structural test that the new bullet KEEPS the old one (MD-Q17,
    adversarial finding F5): the name of the first check it fails, or ``None``.

    * ``order`` — EVERY token of the old bullet (short words, negations and
      number words included) reappears in the new one as an in-order
      subsequence (:func:`_in_order`; a figure stays next to its word);
    * ``negation`` — the new bullet adds no negation word to a clause that
      holds an old token;
    * ``figure`` — every figure of the old bullet (``extract_figures``: kind and
      value, so "30 %" is not "30 Tonnen") is a figure of the new one.

    It only ever BLOCKS a replace: any doubt keeps both bullets."""
    old_toks, new_toks = _tokens(old), _tokens(new)
    placed = _in_order(old_toks, new_toks) if old_toks else None
    if placed is None:
        return "order"
    if _adds_negation(new or "", new_toks, placed):
        return "negation"
    figs_new = {(f.kind, f.value) for f in extract_figures(new)}
    if any((f.kind, f.value) not in figs_new for f in extract_figures(old)):
        return "figure"
    return None


def keeps_every_fact_marker(old: str, new: str) -> bool:
    return blocking_check(old, new) is None


def _parse_replacements(data: Any) -> list[tuple[int, int, int]]:
    """``(entry, new, held)`` 1-based triples that MAY replace (Q3-4): a NEW
    bullet the model says adds facts (``added`` present and non-empty) and whose
    ``held`` names the held bullet that states it most completely. Whether it
    keeps every part of that bullet is code's structural test
    (:func:`blocking_check`). Anything malformed is no replacement."""
    out: list[tuple[int, int, int]] = []
    items = data.get("items") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        added = item.get("added")
        if not isinstance(added, str) or not added.strip():
            continue
        new = _ID.match(str(item.get("new") or "").strip())
        held = _ID.match(str(item.get("held") or "").strip())
        if not new or not held or new.group(2) != "n" or held.group(2) != "h":
            continue
        if new.group(1) != held.group(1):
            continue
        out.append((int(new.group(1)), int(new.group(3)), int(held.group(3))))
    return out


def _own_bullets(entity: Any) -> list[tuple[str, str]]:
    """The entry's own bullets as ``(field, text)``, deduplicated in the order
    ``_held_for`` lists them first (so HELD id j <= len is an own bullet)."""
    seen: dict[str, str] = {}
    for f in _BULLET_FIELDS:
        for b in getattr(entity, f, None) or []:
            if isinstance(b, str) and b.strip() and b not in seen:
                seen[b] = f
    return [(f, b) for b, f in seen.items()]


async def hold_back_restatements(
    existing: MasterProfileData,
    ops: Sequence[Any],
    provider: LLMProvider | None,
    *,
    replace: bool = False,
    reworded: list[dict[str, str]] | None = None,
) -> tuple[list[Any], list[RestatedReceipt]]:
    """The ops with model-named restatements removed, and their receipts.

    ``replace`` (Q3-4, the interview door only; same call, same prompt): a NEW
    bullet the model says adds facts to the held bullet that states it most
    completely, and that keeps every word, figure and name of that bullet (a
    WORK entry's OWN bullet), leaves its ``add_bullets`` op and becomes a
    ``ReplaceBullet`` op (module ``bullet_replace``). Any doubt keeps both.

    ``reworded`` (Sorbet invite-gate WP-V, with ``replace``): the candidates
    the in-order check blocked are appended to it — ``entry_id``, ``field`` (the
    held bullet's), ``held``, ``new`` — for the bullet-replace loop
    (``services/profile/replace_loop``), which asks the person. Their new
    bullets are written here as before: both bullets stay until the person
    answers."""
    ops = list(ops)
    if provider is None:
        return ops, []
    held_by_id = _held_entities(existing)

    # One check-entry per (op, entity): the op's new bullets in field order.
    entries: list[dict] = []
    slots: list[tuple[int, list[tuple[str, str]]]] = []  # (op index, [(field, text)])
    for idx, op in enumerate(ops):
        if not isinstance(op, AddBullets) or op.target not in held_by_id:
            continue
        section, entity = held_by_id[op.target]
        held = _held_for(existing, section, entity)
        new = [(f, t) for f in _BULLET_FIELDS for t in getattr(op, f) if isinstance(t, str) and t.strip()]
        if not held or not new:
            continue
        entries.append({
            "label": label_for(existing, op.target) or section,
            "held": held,
            "new": [t for _, t in new],
            "entity_id": op.target,
            "section": section,
            "own": _own_bullets(entity) if section == "work_experience" else [],
        })
        slots.append((idx, new))
    if not entries:
        return ops, []

    try:
        with llm_log_stage("restatement_check"):
            data = await provider.aparse_json(
                build_restatement_prompt(entries),
                system=RESTATEMENT_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=RESTATEMENT_MAX_TOKENS,
            )
    except Exception as exc:  # noqa: BLE001 — fail-open: the merge writes what it wrote before
        logger.warning("restatement_check: call failed; every bullet is written (fail-open): %s", type(exc).__name__)
        clog.debug("restatement_check: call failed; every bullet is written (fail-open)", exc_info=True)
        return ops, []

    drop: dict[int, set[int]] = {}
    restates: dict[tuple[int, int], int] = {}
    for e, n, h in _parse_pairs(data):
        if not (1 <= e <= len(entries)):
            continue
        if not (1 <= n <= len(entries[e - 1]["new"])) or not (1 <= h <= len(entries[e - 1]["held"])):
            continue
        drop.setdefault(e - 1, set()).add(n - 1)
        restates[(e - 1, n - 1)] = h - 1
    replacements = _replacements(entries, data, drop, reworded) if replace else {}
    if not drop and not replacements:
        return ops, []

    receipts: list[RestatedReceipt] = []
    added_ops: list[Any] = []
    for entry_idx in sorted(set(drop) | set(replacements)):
        removed = drop.get(entry_idx, set())
        replaced = replacements.get(entry_idx, {})
        op_idx, new = slots[entry_idx]
        op = ops[op_idx]
        entry = entries[entry_idx]
        keep: dict[str, list[str]] = {f: [] for f in _BULLET_FIELDS}
        for k, (field, text) in enumerate(new):
            if k in replaced:
                held_field, held_text = entry["own"][replaced[k]]
                added_ops.append(ReplaceBullet(
                    entry_id=entry["entity_id"], field=held_field, old=held_text, new=text,
                ))
            elif k in removed:
                receipts.append(RestatedReceipt(
                    entity_id=entry["entity_id"],
                    entity_label=entry["label"],
                    section=entry["section"],
                    field=field,
                    text=text,
                    restates=entry["held"][restates[(entry_idx, k)]],
                ))
            else:
                keep[field].append(text)
        ops[op_idx] = op.model_copy(update=keep)
    ops = ops + added_ops
    # An op left with nothing to add is no op.
    ops = [
        o for o in ops
        if not (isinstance(o, AddBullets) and not o.responsibilities and not o.achievements and not o.technologies)
    ]
    return ops, receipts


def _replacements(
    entries: list[dict],
    data: Any,
    drop: dict[int, set[int]],
    reworded: list[dict[str, str]] | None = None,
) -> dict[int, dict[int, int]]:
    """Entry index -> {new index -> own-bullet index} for the NEW bullets that
    replace a held one (Q3-4). Safe side only: a work entry's OWN bullet, a NEW
    bullet not held back as a restatement, one NEW per held bullet and one held
    per NEW bullet, and :func:`blocking_check` passes."""
    claims: dict[tuple[int, int], list[int]] = {}
    per_new: dict[tuple[int, int], list[int]] = {}
    for e, n, h in _parse_replacements(data):
        if not (1 <= e <= len(entries)):
            continue
        entry = entries[e - 1]
        if not (1 <= n <= len(entry["new"])) or not (1 <= h <= len(entry["own"])):
            continue
        if (n - 1) in drop.get(e - 1, set()):
            continue
        claims.setdefault((e - 1, h - 1), []).append(n - 1)
        per_new.setdefault((e - 1, n - 1), []).append(h - 1)
    out: dict[int, dict[int, int]] = {}
    for (e, h), news in claims.items():
        if len(set(news)) != 1:
            continue  # two NEW bullets claim one held bullet: keep all
        n = news[0]
        if len(set(per_new[(e, n)])) != 1:
            continue
        old_text = entries[e]["own"][h][1]
        blocked = blocking_check(old_text, entries[e]["new"][n])
        if blocked:
            # Never the bullet text in the application log (personal data):
            # the entry id, the bullet's position and length, the check.
            logger.info(
                "restatement_check: replace blocked by the %s check (Q3-4) — entry %s, "
                "held bullet %d (%d chars)", blocked, entries[e]["entity_id"], h + 1, len(old_text),
            )
            if reworded is not None:
                reworded.append({
                    "entry_id": entries[e]["entity_id"], "field": entries[e]["own"][h][0],
                    "held": old_text, "new": entries[e]["new"][n],
                })
            continue
        out.setdefault(e, {})[n] = h
    return out
