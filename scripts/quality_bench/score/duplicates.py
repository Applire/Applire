# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — class D, duplicates (SPEC A.3).

Code computes CANDIDATE pairs (an acronym beside its expansion, one name
contained in another, near-identical wording, heavily overlapping bullets);
only the judge decides sameness (ADR-062 clause 1). The judge sees every prose
item of the ONE document, not just the candidates — a candidate filter would
cap recall at the filter's recall. Candidates are used as a cross-check only:
each issue records ``deterministic_candidate`` so the instrument's two views can
be compared.

A group of k items counts k-1 issues: the first member in document order is the
anchor, every other member is one issue. Groups that share a member are merged
(one competence), and no item is counted twice.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from . import prompts
from .common import as_dicts, make_issue
from .items import Item, norm

D_KINDS_CV = frozenset({"summary", "bullet", "skill"})
_STOP = frozenset("of for and the a an in on to with und der die das den dem von für mit zu im am bei".split())


def _initials(text: str) -> str:
    return "".join(w[0] for w in norm(text).split() if w not in _STOP)


def _is_acronym(text: str) -> bool:
    t = re.sub(r"[^\w]", "", text)
    return 2 <= len(t) <= 8 and (t.isupper() or sum(c.isupper() for c in t) >= 2) and " " not in text.strip()


def candidate_pairs(items: list[Item]) -> list[tuple[str, str, str]]:
    """Deterministic candidate pairs ``(loc_a, loc_b, reason)`` — NOT verdicts."""
    out: list[tuple[str, str, str]] = []
    normed = [(i, norm(i.text), set(w for w in norm(i.text).split() if w not in _STOP)) for i in items]
    for a in range(len(normed)):
        ia, na, ta = normed[a]
        for b in range(a + 1, len(normed)):
            ib, nb, tb = normed[b]
            if not na or not nb:
                continue
            reason = None
            if _is_acronym(ia.text) and _initials(ib.text).startswith(norm(ia.text).replace(" ", "")) and len(nb.split()) >= 2:
                reason = "acronym"
            elif _is_acronym(ib.text) and _initials(ia.text).startswith(norm(ib.text).replace(" ", "")) and len(na.split()) >= 2:
                reason = "acronym"
            elif re.search(rf"\({re.escape(ia.text.strip())}\)", ib.text) or re.search(rf"\({re.escape(ib.text.strip())}\)", ia.text):
                reason = "acronym"
            if reason is None:
                short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
                if short and short <= long_ and len(long_) <= 8 and len(short) < len(long_):
                    reason = "containment"
                elif SequenceMatcher(None, na, nb).ratio() >= 0.8:
                    reason = "similar"
                elif min(len(ta), len(tb)) >= 5 and len(ta & tb) / len(ta | tb) >= 0.6:
                    reason = "overlap"
            if reason:
                out.append((ia.loc, ib.loc, reason))
    return out


def _structure(kind_a: str, kind_b: str) -> str:
    return "~".join(sorted((kind_a, kind_b)))


def score_duplicates(judge: Any, doc: str, items: list[Item], language: str) -> dict[str, Any]:
    out: dict[str, Any] = {"issues": [], "groups": [], "dropped_unknown_loc": 0, "candidates": [], "items_judged": len(items)}
    if len(items) < 2:
        return out
    order = {i.loc: n for n, i in enumerate(items)}
    by_loc = {i.loc: i for i in items}
    cands = candidate_pairs(items)
    out["candidates"] = [list(c) for c in cands]
    cand_set = {frozenset((a, b)) for a, b, _ in cands}

    ans = judge.complete_json(prompts.system_prompt("D"), prompts.user_duplicates(doc, language, items), cls="D")

    groups: list[dict[str, Any]] = []
    for g in as_dicts(ans.get("groups")):
        locs: list[str] = []
        for loc in g.get("locs") or []:
            if loc in by_loc:
                if loc not in locs:
                    locs.append(loc)
            else:
                out["dropped_unknown_loc"] += 1
        if len(locs) >= 2:
            groups.append({"locs": locs, "why": g.get("why")})

    # merge groups sharing a member (union-find over locs)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g in groups:
        for loc in g["locs"][1:]:
            parent[find(loc)] = find(g["locs"][0])
    merged: dict[str, dict[str, Any]] = {}
    for g in groups:
        root = find(g["locs"][0])
        slot = merged.setdefault(root, {"locs": set(), "why": []})
        slot["locs"].update(g["locs"])
        if g.get("why"):
            slot["why"].append(str(g["why"]))

    for slot in merged.values():
        locs = sorted(slot["locs"], key=lambda l: order[l])
        anchor = by_loc[locs[0]]
        why = " / ".join(slot["why"])
        out["groups"].append({"locs": locs, "texts": [by_loc[l].text for l in locs], "why": why})
        for loc in locs[1:]:
            m = by_loc[loc]
            out["issues"].append(
                make_issue(
                    "D", doc, loc, m.text,
                    {
                        "anchor_loc": anchor.loc,
                        "anchor_text": anchor.text,
                        "group_locs": locs,
                        "group_texts": [by_loc[l].text for l in locs],
                        "why": why,
                    },
                    _structure(anchor.kind, m.kind),
                    deterministic_candidate=any(frozenset((x, loc)) in cand_set for x in locs if x != loc),
                )
            )
    return out
