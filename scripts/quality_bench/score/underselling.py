# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — class U, underselling (SPEC A.2).

Two detectors, one judge call per run (the judge reads both documents because
"omitted" means absent from BOTH):

**U-del (deterministic first).** From ``llm.jsonl`` the LAST model-written draft
of each document is recovered (``drafts.py``: CV writer / corrector / language /
terminal stages; letter writer / corrector / terminal / condense stages). Every
prose item (CV summary sentence, bullet, skill chip; letter sentence) present in
that draft but absent from the DELIVERED document is a removal by the
deterministic tail — bullet caps, ``_prefer_measured_outcomes``,
``cut_ungrounded_limits``, ``guard_letter_figures``, the skills cap …. Presence
is fuzzy (``items.TextIndex``): a sentence reworded below ratio 0.9 but above
0.6 is listed as ``altered`` with its delivered counterpart, below 0.6 as
``removed``. A chip still conveyed inside delivered prose is not a removal.
Those removals go to the judge, which decides per removal whether it was
grounded in the vault AND relevant (A.2 part 1). Optionally
(``include_llm_stage``) items that an earlier DRAFT had and a later draft lost
with no near rewording are listed too (``dropped_by_llm``), so a corrector that
deleted true evidence is visible.

**U-omit / U-under (judge).** Part 2 of the same call: vault evidence for a
REQUIRED JD item missing from both documents (``omitted``) and delivered
statements weaker than the vault (``understated``).

Judge claims that cite an unknown removal id, item ``loc`` or vault path are
dropped and counted — invented findings make the measurement worthless.
"""

from __future__ import annotations

from typing import Any

from . import prompts
from .common import as_dicts, make_issue, truthy
from .drafts import Draft
from .inputs import jd_text, resolve_vault_ref, vault_lines, vault_text
from .items import Item, TextIndex, norm

REMOVABLE_KINDS = frozenset({"summary", "bullet", "skill", "sentence"})
ALTERED_FLOOR = 0.6
MAX_REMOVALS = 120


def find_removals(
    doc: str,
    drafts: list[Draft],
    delivered: list[Item],
    *,
    include_llm_stage: bool = False,
    threshold: float = 0.9,
) -> list[dict[str, Any]]:
    """Deterministic U-del candidates for one document (see module docstring)."""
    if not drafts or not delivered:
        return []
    deliv_idx = TextIndex(i.text for i in delivered)
    deliv_by_norm = {norm(i.text): i.text for i in delivered}
    removals: list[dict[str, Any]] = []
    seen: set[str] = set()

    def present(text: str) -> bool:
        return deliv_idx.contains(text, threshold) or deliv_idx.contains_word_sequence(text)

    last = drafts[-1]
    for it in last.items:
        n = norm(it.text)
        if it.kind not in REMOVABLE_KINDS or not n or n in seen:
            continue
        if present(it.text):
            continue
        kind, counterpart = "removed", None
        if len(n.split()) >= 4:
            ratio, match = deliv_idx.best_ratio(it.text)
            if ALTERED_FLOOR <= ratio < threshold and match:
                kind, counterpart = "altered", deliv_by_norm.get(match, match)
        seen.add(n)
        removals.append(_removal(doc, it, kind, counterpart, source=last.idx, removed_by=None))

    if include_llm_stage:
        for prev, nxt in zip(drafts, drafts[1:]):
            nxt_idx = TextIndex(nxt.texts)
            for it in prev.items:
                n = norm(it.text)
                if it.kind not in REMOVABLE_KINDS or not n or n in seen:
                    continue
                if present(it.text) or nxt_idx.contains(it.text, ALTERED_FLOOR) or nxt_idx.contains_word_sequence(it.text):
                    continue
                if any(TextIndex(d.texts).contains(it.text, ALTERED_FLOOR) for d in drafts[prev.idx + 1 :]):
                    continue  # reappears later: not a loss
                seen.add(n)
                removals.append(_removal(doc, it, "dropped_by_llm", None, source=prev.idx, removed_by=nxt.idx))
    return removals


def _removal(doc: str, it: Item, kind: str, counterpart: str | None, *, source: int, removed_by: int | None) -> dict[str, Any]:
    return {
        "doc": doc,
        "draft_loc": it.loc,
        "text": it.text,
        "item_kind": it.kind,
        "kind": kind,
        "counterpart": counterpart,
        "source_draft_idx": source,
        "removed_by_draft_idx": removed_by,
    }


def number_removals(removals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for n, r in enumerate(removals[:MAX_REMOVALS], start=1):
        r["id"] = f"R{n}"
    return removals[:MAX_REMOVALS]


def score_underselling(
    judge: Any,
    *,
    language: str,
    vault: dict | None,
    job: dict | None,
    ledger: list[dict] | None,
    cv_items: list[Item],
    letter_items: list[Item],
    removals: list[dict[str, Any]],
) -> dict[str, Any]:
    truncated = len(removals) > MAX_REMOVALS
    removals = number_removals([dict(r) for r in removals])
    out: dict[str, Any] = {
        "issues": [],
        "removals": removals,
        "removals_truncated": truncated,
        "dropped_unknown_id": 0,
        "dropped_unknown_loc": 0,
        "dropped_unknown_vault_ref": 0,
        "removals_unjudged": 0,
    }
    if not cv_items and not letter_items:
        return out
    ans = judge.complete_json(
        prompts.system_prompt("U"),
        prompts.user_underselling(language, vault_text(vault), jd_text(job, ledger), cv_items, letter_items, removals),
        cls="U",
    )

    by_id = {r["id"]: r for r in removals}
    judged: set[str] = set()
    for rec in as_dicts(ans.get("removed")):
        r = by_id.get(str(rec.get("id")))
        if r is None:
            out["dropped_unknown_id"] += 1
            continue
        judged.add(r["id"])
        r["judge"] = {"grounded": truthy(rec.get("grounded")), "relevant": truthy(rec.get("relevant")), "why": rec.get("why")}
        if r["judge"]["grounded"] and r["judge"]["relevant"]:
            out["issues"].append(
                make_issue(
                    "U", r["doc"], r["draft_loc"], r["text"],
                    {"why": rec.get("why"), "removal_kind": r["kind"], "counterpart": r["counterpart"]},
                    "del" if r["kind"] != "dropped_by_llm" else "del-llm",
                    removal_id=r["id"],
                    removal=r,
                )
            )
    out["removals_unjudged"] = len([r for r in removals if r["id"] not in judged])

    lines = dict(vault_lines(vault))
    seen_refs: set[str] = set()
    for rec in as_dicts(ans.get("omitted")):
        ref = str(rec.get("vault_ref") or "")
        if not ref or ref in seen_refs:
            continue
        if lines and ref not in lines and resolve_vault_ref(vault, ref) == ref:
            out["dropped_unknown_vault_ref"] += 1
            continue
        seen_refs.add(ref)
        out["issues"].append(
            make_issue(
                "U", "both", ref, resolve_vault_ref(vault, ref),
                {"jd_item": rec.get("jd_item"), "why": rec.get("why")},
                "omit",
                vault_ref=ref,
            )
        )
    by_loc = {i.loc: i for i in [*cv_items, *letter_items]}
    seen_locs: set[str] = set()
    for rec in as_dicts(ans.get("understated")):
        loc = str(rec.get("loc") or "")
        item = by_loc.get(loc)
        if item is None:
            out["dropped_unknown_loc"] += 1
            continue
        if loc in seen_locs:
            continue
        seen_locs.add(loc)
        out["issues"].append(
            make_issue(
                "U", item.doc, loc, item.text,
                {"vault_ref": rec.get("vault_ref"), "why": rec.get("why")},
                "under",
                vault_ref=rec.get("vault_ref"),
            )
        )
    return out
