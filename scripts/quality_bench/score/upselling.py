# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — class S, upselling (SPEC A.4).

A per-claim judge reads each prose claim of one delivered document against the
frozen vault. It is INDEPENDENT of the product's Truthfulness Oracle: the
Oracle is part of the system under test. ``reports.json`` carries the Oracle's
own verdict per claim; :func:`oracle_agreement` lines those up beside the
judge's findings (an agreement table — the Oracle's verdicts are never judge
input, and their disagreement is itself reported).
"""

from __future__ import annotations

from typing import Any

from . import prompts
from .common import as_dicts, make_issue
from .inputs import vault_text
from .items import Item, norm, tokens

S_KINDS_CV = frozenset({"summary", "bullet", "skill"})
S_KINDS = ("unbacked", "inflated", "misattributed")
ORACLE_FLAGGED = frozenset({"unbacked", "inflated", "misattributed"})


def claim_items(items: list[Item]) -> list[Item]:
    """The claims of a document: CV summary/bullets/skills, letter sentences (>= 3 words)."""
    out = []
    for i in items:
        if i.doc == "cv" and i.kind in S_KINDS_CV:
            out.append(i)
        elif i.doc == "letter" and i.kind == "sentence" and len(i.text.split()) >= 3:
            out.append(i)
    return out


def score_upselling(judge: Any, doc: str, items: list[Item], language: str, vault: dict | None) -> dict[str, Any]:
    claims = claim_items(items)
    out: dict[str, Any] = {"issues": [], "dropped_unknown_loc": 0, "dropped_bad_kind": 0, "claims_judged": len(claims)}
    if not claims:
        return out
    by_loc = {c.loc: c for c in claims}
    ans = judge.complete_json(
        prompts.system_prompt("S"),
        prompts.user_upselling(doc, language, vault_text(vault), claims),
        cls="S",
    )
    seen: set[str] = set()
    for rec in as_dicts(ans.get("issues")):
        loc = str(rec.get("loc") or "")
        item = by_loc.get(loc)
        if item is None:
            out["dropped_unknown_loc"] += 1
            continue
        kind = str(rec.get("kind") or "").lower()
        if kind not in S_KINDS:
            out["dropped_bad_kind"] += 1
            continue
        key = f"{loc}|{kind}"
        if key in seen:
            continue
        seen.add(key)
        out["issues"].append(
            make_issue(
                "S", doc, loc, item.text,
                {
                    "claim": rec.get("claim") or item.text,
                    "vault_ref": rec.get("vault_ref_or_null"),
                    "why": rec.get("why"),
                },
                kind,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Oracle agreement table
# --------------------------------------------------------------------------- #


def _match_item(claim_text: str, items: list[Item]) -> Item | None:
    cn = norm(claim_text)
    ct = set(tokens(claim_text))
    if not cn:
        return None
    best, best_score = None, 0.0
    for it in items:
        n = norm(it.text)
        if cn == n:
            return it
        if cn in n or (n and n in cn):
            score = 0.95
        else:
            t = set(tokens(it.text))
            score = len(ct & t) / len(ct) if ct else 0.0
        if score > best_score:
            best, best_score = it, score
    return best if best_score >= 0.7 else None


def oracle_agreement(doc: str, items: list[Item], report: dict | None, judge_issues: list[dict]) -> dict[str, Any]:
    """Oracle verdicts beside the judge's S findings, at item level.

    Each Oracle claim is matched to one of our claim items by text. Per matched
    item: the Oracle flags it when any of its claims is unbacked / inflated /
    misattributed; the judge flags it when it has an S issue on that ``loc``.
    """
    if not isinstance(report, dict) or not report.get("claims"):
        return {"available": False}
    claims = claim_items(items)
    judge_by_loc = {i["loc"]: i for i in judge_issues}
    rows: dict[str, dict[str, Any]] = {}
    unmatched = 0
    for cr in as_dicts(report.get("claims")):
        claim = cr.get("claim") or {}
        verdict = (cr.get("verdict") or {}).get("verdict")
        item = _match_item(str(claim.get("text") or ""), claims)
        if item is None:
            unmatched += 1
            continue
        row = rows.setdefault(item.loc, {"loc": item.loc, "text": item.text, "oracle_verdicts": [], "oracle_locations": []})
        row["oracle_verdicts"].append(verdict)
        row["oracle_locations"].append(claim.get("location"))
    table = []
    counts = {"both_flag": 0, "oracle_only": 0, "judge_only": 0, "neither": 0}
    for loc, row in sorted(rows.items()):
        o = any(v in ORACLE_FLAGGED for v in row["oracle_verdicts"])
        j = loc in judge_by_loc
        row.update(oracle_flagged=o, judge_flagged=j, judge_kind=(judge_by_loc.get(loc) or {}).get("subclass"))
        counts["both_flag" if o and j else "oracle_only" if o else "judge_only" if j else "neither"] += 1
        table.append(row)
    return {
        "available": True,
        "doc": doc,
        "oracle_claims": len(report.get("claims") or []),
        "oracle_claims_unmatched": unmatched,
        "items_compared": len(table),
        "counts": counts,
        "judge_flags_outside_oracle_claims": sorted(set(judge_by_loc) - set(rows)),
        "rows": table,
    }
