# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (attribution) — which stage introduced each issue.

Method (``applire-prompt-first`` step 4a, per-round attribution): order every
captured draft of the document by ``ts``, run the class predicate over each,
and name the FIRST draft where the issue appears, as ``(stage, review_role,
review_attempt)``.

Predicates, all on fuzzy text presence (``items.TextIndex``):

* **L** — the issue's item text appears in the draft (the item is foreign in the
  delivered document; the draft that first carried the same text introduced it).
* **D** — BOTH members of the pair (anchor and member) are present in the draft.
* **S** — the claim text (the delivered item, or the judge's quoted claim)
  is present.
* **U-del** — the step that REMOVED it: the first draft after the last draft
  that still contains the text; when no later model draft lacks it, the
  deterministic tail (``"compose"``).
* **U-omit / U-under** — omit: removed like U-del when some draft carried the
  vault evidence, ``"never_drafted"`` when none ever did; under: like L (the
  weak statement's first draft).

``"compose"`` means the issue is absent from every model draft but present in the
delivered document, or (U-del) that the last model draft still had it: the
deterministic tail produced the outcome. ``"unattributed"`` means no draft of
that document was captured (the log is the only evidence there is).

Writes ``issue["stage"]`` (a stable label such as ``cv_tailoring/writer``,
``cv_terminal_review/corrector#2``, ``letter_condense_final_floor``,
``compose``) and ``issue["stage_detail"]``.
"""

from __future__ import annotations

from typing import Any

from .drafts import Draft
from .items import TextIndex

COMPOSE = "compose"
UNATTRIBUTED = "unattributed"
NEVER_DRAFTED = "never_drafted"


class _Drafts:
    """Drafts of one scope (a document, or both) with their presence indexes."""

    def __init__(self, drafts: list[Draft]):
        self.drafts = sorted(drafts, key=lambda d: (d.ts, d.doc, d.idx))
        self.indexes = [TextIndex(d.texts) for d in self.drafts]

    def __bool__(self) -> bool:
        return bool(self.drafts)

    def present(self, i: int, text: str, threshold: float = 0.9) -> bool:
        return self.indexes[i].contains(text, threshold)

    def first_with(self, *texts: str, threshold: float = 0.9) -> int | None:
        for i in range(len(self.drafts)):
            if all(self.present(i, t, threshold) for t in texts if t):
                return i
        return None

    def last_with(self, text: str, threshold: float = 0.9) -> int | None:
        last = None
        for i in range(len(self.drafts)):
            if self.present(i, text, threshold):
                last = i
        return last


def _result(draft: Draft | None, fallback: str) -> tuple[str, dict[str, Any]]:
    if draft is None:
        return fallback, {"stage": fallback}
    return draft.label, draft.stage_detail()


def _removed_by(scope: _Drafts, text: str, threshold: float = 0.8) -> tuple[str, dict[str, Any]]:
    last = scope.last_with(text, threshold)
    if last is None:
        return NEVER_DRAFTED, {"stage": NEVER_DRAFTED}
    if last + 1 < len(scope.drafts):
        return _result(scope.drafts[last + 1], COMPOSE)
    return COMPOSE, {"stage": COMPOSE, "last_draft": scope.drafts[last].stage_detail()}


def _first_appearance(scope: _Drafts, *texts: str, threshold: float = 0.9) -> tuple[str, dict[str, Any]]:
    i = scope.first_with(*texts, threshold=threshold)
    return _result(scope.drafts[i] if i is not None else None, COMPOSE)


def attribute_issue(issue: dict[str, Any], drafts: dict[str, list[Draft]]) -> None:
    doc = issue.get("doc")
    scope = _Drafts(drafts.get("cv", []) + drafts.get("letter", [])) if doc == "both" else _Drafts(drafts.get(doc or "", []))
    if not scope:
        issue["stage"], issue["stage_detail"] = UNATTRIBUTED, {"stage": UNATTRIBUTED, "reason": "no draft captured"}
        return
    cls, sub = issue.get("class"), issue.get("subclass") or ""
    text = issue.get("text") or ""
    ev = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    if cls == "L":
        label, detail = _first_appearance(scope, text)
    elif cls == "D":
        label, detail = _first_appearance(scope, text, ev.get("anchor_text") or "")
    elif cls == "S":
        i = scope.first_with(text, threshold=0.85)
        if i is None and ev.get("claim"):
            i = scope.first_with(str(ev["claim"]), threshold=0.85)
        label, detail = _result(scope.drafts[i] if i is not None else None, COMPOSE)
    elif cls == "U" and sub.startswith("del"):
        label, detail = _removed_by(scope, text)
    elif cls == "U" and sub == "omit":
        label, detail = _removed_by(scope, text)
    elif cls == "U":  # under
        label, detail = _first_appearance(scope, text)
    else:
        label, detail = UNATTRIBUTED, {"stage": UNATTRIBUTED, "reason": f"unknown class {cls!r}"}
    issue["stage"], issue["stage_detail"] = label, detail


def attribute_issues(issues: list[dict[str, Any]], drafts: dict[str, list[Draft]]) -> list[dict[str, Any]]:
    for issue in issues:
        attribute_issue(issue, drafts)
    return issues
