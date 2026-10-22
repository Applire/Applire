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
from applire.services.profile.reconcile.ops import AddBullets

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


async def hold_back_restatements(
    existing: MasterProfileData,
    ops: Sequence[Any],
    provider: LLMProvider | None,
) -> tuple[list[Any], list[RestatedReceipt]]:
    """The ops with model-named restatements removed, and their receipts."""
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
        held = _held_bullets(entity)
        new = [(f, t) for f in _BULLET_FIELDS for t in getattr(op, f) if isinstance(t, str) and t.strip()]
        if not held or not new:
            continue
        entries.append({
            "label": label_for(existing, op.target) or section,
            "held": held,
            "new": [t for _, t in new],
            "entity_id": op.target,
            "section": section,
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
    if not drop:
        return ops, []

    receipts: list[RestatedReceipt] = []
    for entry_idx, removed in drop.items():
        op_idx, new = slots[entry_idx]
        op = ops[op_idx]
        entry = entries[entry_idx]
        keep: dict[str, list[str]] = {f: [] for f in _BULLET_FIELDS}
        for k, (field, text) in enumerate(new):
            if k in removed:
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
    # An op left with nothing to add is no op.
    ops = [
        o for o in ops
        if not (isinstance(o, AddBullets) and not o.responsibilities and not o.achievements and not o.technologies)
    ]
    return ops, receipts
