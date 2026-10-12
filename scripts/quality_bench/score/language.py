# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — class L, language mix (SPEC A.1).

The instrument is the judge, one call per delivered document, over EVERY text
item of that document (prose, chips, vault-joined fields, letter sentences and
header). The product's own ``item_language_mismatch`` (ADR-066: stopword
scoring, DE/EN only, >= 4-word floor) runs alongside purely as a cross-check:
its verdict is recorded on each issue (``deterministic_mismatch``) and
summarised per document (``cross_check``), never counted.

Judge findings whose ``loc`` is not an item we sent are dropped and counted
(``dropped_unknown_loc``): an invented location is an invented finding. The
text on an issue is always the delivered item's own text, never the judge's
re-quotation. Exemptions (employer / title / certificate / acronym / loanword,
founder O-2) are returned separately so their count stays visible.
"""

from __future__ import annotations

import re
from typing import Any

from . import prompts
from .common import as_dicts, make_issue
from .items import Item

try:  # product code is imported, never patched (COMMON-BRIEF)
    from applire.utils.language_detection import item_language_mismatch as _item_language_mismatch
except Exception:  # pragma: no cover - PYTHONPATH=backend not set
    _item_language_mismatch = None

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def deterministic_mismatch(text: str, language: str) -> bool | None:
    if _item_language_mismatch is None:
        return None
    return bool(_item_language_mismatch(text, language))


def score_language(judge: Any, doc: str, items: list[Item], language: str) -> dict[str, Any]:
    items = [i for i in items if _HAS_LETTER.search(i.text)]
    by_loc = {i.loc: i for i in items}
    out: dict[str, Any] = {"issues": [], "exempt": [], "dropped_unknown_loc": 0, "cross_check": None, "items_judged": len(items)}
    if not items:
        return out
    ans = judge.complete_json(prompts.system_prompt("L", language), prompts.user_language(doc, language, items), cls="L")

    seen: set[str] = set()
    for rec in as_dicts(ans.get("issues")):
        loc = str(rec.get("loc") or "")
        item = by_loc.get(loc)
        if item is None:
            out["dropped_unknown_loc"] += 1
            continue
        if loc in seen:
            continue
        seen.add(loc)
        detected = str(rec.get("detected_language") or "unknown").lower()
        out["issues"].append(
            make_issue(
                "L", doc, loc, item.text,
                {"detected_language": detected, "judge_text": rec.get("text")},
                f"{detected}-in-{language}",
                deterministic_mismatch=deterministic_mismatch(item.text, language),
            )
        )
    for rec in as_dicts(ans.get("exempt")):
        loc = str(rec.get("loc") or "")
        item = by_loc.get(loc)
        if item is not None and loc not in seen:
            out["exempt"].append({"doc": doc, "loc": loc, "text": item.text, "reason": rec.get("reason")})

    if _item_language_mismatch is not None:
        det = {i.loc for i in items if _item_language_mismatch(i.text, language)}
        judged = {i["loc"] for i in out["issues"]}
        out["cross_check"] = {
            "deterministic_flagged": sorted(det),
            "judge_flagged": sorted(judged),
            "both": sorted(det & judged),
            "deterministic_only": sorted(det - judged),
            "judge_only": sorted(judged - det),
        }
    return out
