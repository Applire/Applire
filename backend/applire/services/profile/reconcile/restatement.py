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

from applire.prompts.restatement import (
    RESTATEMENT_REPLACE_ADDENDUM,
    RESTATEMENT_SYSTEM_PROMPT,
    build_restatement_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
from applire.schemas.profile import MasterProfileData, RestatedReceipt
from applire.services.profile.entity_label import label_for
from applire.services.oracle.matchers.figures import extract_figures
from applire.services.profile.reconcile.ops import AddBullets, ReplaceBullet

logger = logging.getLogger(__name__)

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


_DIGITS = re.compile(r"\d+")
_TOKEN = re.compile(r"[^\W_][\w\-/.+#]*")


_PART = re.compile(r"[-/]")


def _parts(text: str) -> list[str]:
    """The text's tokens split at hyphens and slashes, trailing dots stripped."""
    return [p.rstrip(".") for tok in _TOKEN.findall(text or "") for p in _PART.split(tok) if p.rstrip(".")]


def _name_like(text: str) -> set[str]:
    """Parts that name something: a part carrying a digit or two capitals
    (ISO, 45001, SAP, KVP, HGB, S/4HANA's 4HANA), casefolded."""
    return {
        p.casefold() for p in _parts(text)
        if any(c.isdigit() for c in p) or sum(c.isupper() for c in p) >= 2
    }


def blocking_check(old: str, new: str) -> str | None:
    """Q3-4's deterministic blockers (they only ever BLOCK a replace): the name
    of the first check the new bullet fails — every figure (``extract_figures``,
    kind + value), every digit run, every name-like part of the old bullet must
    reappear in the new one — or ``None`` when it keeps them all."""
    figs_new = {(f.kind, f.value) for f in extract_figures(new)}
    if any((f.kind, f.value) not in figs_new for f in extract_figures(old)):
        return "figure"
    if not set(_DIGITS.findall(old)) <= set(_DIGITS.findall(new)):
        return "digits"
    if not _name_like(old) <= {p.casefold() for p in _parts(new)}:
        return "name"
    return None


def keeps_every_fact_marker(old: str, new: str) -> bool:
    return blocking_check(old, new) is None


def _parse_replacements(data: Any) -> list[tuple[int, int, int]]:
    """``(entry, new, held)`` 1-based triples the model says may replace: a NEW
    bullet that adds facts (``added`` non-empty), names the ONE held bullet all
    of whose facts it states (``covers``) and drops none of them (``dropped``
    present and empty). Anything else — a missing key, a null, a cross-entry id
    — is no replacement (never guessed)."""
    out: list[tuple[int, int, int]] = []
    items = data.get("items") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        added, dropped = item.get("added"), item.get("dropped")
        if not isinstance(added, str) or not added.strip():
            continue
        if not isinstance(dropped, str) or dropped.strip():
            continue
        new = _ID.match(str(item.get("new") or "").strip())
        held = _ID.match(str(item.get("covers") or "").strip())
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
) -> tuple[list[Any], list[RestatedReceipt]]:
    """The ops with model-named restatements removed, and their receipts.

    ``replace`` (Q3-4, the interview door only): the call also asks, per NEW
    bullet, which held bullet it fully covers; a qualifying NEW bullet leaves
    its ``add_bullets`` op and becomes a ``ReplaceBullet`` op for a WORK entry's
    OWN held bullet (module ``bullet_replace``). Every other path is unchanged."""
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
                system=RESTATEMENT_SYSTEM_PROMPT + (RESTATEMENT_REPLACE_ADDENDUM if replace else ""),
                temperature=0.0,
                max_tokens=RESTATEMENT_MAX_TOKENS,
            )
    except Exception:  # noqa: BLE001 — fail-open: the merge writes what it wrote before
        logger.warning("restatement_check: call failed; every bullet is written (fail-open)", exc_info=True)
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
    replacements = _replacements(entries, data, drop) if replace else {}
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


def _replacements(entries: list[dict], data: Any, drop: dict[int, set[int]]) -> dict[int, dict[int, int]]:
    """Entry index -> {new index -> own-bullet index} for the NEW bullets that
    replace a held one (Q3-4). Safe side only: a work entry's OWN bullet, a NEW
    bullet not held back as a restatement, one NEW per held bullet and one held
    per NEW bullet, and :func:`keeps_every_fact_marker`."""
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
            continue
        out.setdefault(e, {})[n] = h
    return out
