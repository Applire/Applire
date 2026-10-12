# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — recover every model-written DRAFT of a document from llm.jsonl.

The per-run LLM debug log (``providers/llm/debug_log.py``) records one JSON line
per call with ``ts``, ``stage``, ``review_role`` ("reviewer" | "generator" |
null), ``review_attempt`` and the parsed ``response``. A record is a DRAFT of
the CV or the letter when its response has the document's prose shape —
decided on the SHAPE, not the stage alone, because stage labels leak
(``set_stage`` is imperative: build-1 logs carry outcome-critic findings under
``cv_language``). Both ends are required: the stage must belong to a document
chain *and* the response must look like that document.

Captured response shapes (inspected on the 2026-07…10 dev logs):

* CV writer / corrector / language / terminal: ``{summary, work[{id, bullets,
  projects}], skills[, projects]}`` — the ADR-067 prose shape; older logs carry
  the full ``tailored_data`` shape ``{contact, work_history, ...}``.
* CV segmented writer (ADR-047): partial responses — ``{summary}``,
  ``{skills}``, ``{projects}``, ``{bullets, projects}`` per role (no id).
* Letter writer / corrector / condense: ``{header, recipient, body{paragraphs},
  signature}``.

Segmented partials are accumulated into a running state so every draft carries
the document AS IT STOOD after that call; a full-shape response replaces the
state. The per-role ``{bullets, projects}`` segments carry no id, so they join
the running state as id-less work entries (their text is what matters to
attribution and to the removal diff).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .items import Item, flatten_cv, flatten_letter

CV_STAGES = frozenset(
    {"cv_tailoring", "cv_language", "cv_language_recheck", "cv_terminal_review"}
)
LETTER_STAGES = frozenset(
    {
        "cover_letter",
        "letter_terminal_review",
        "cover_letter_condense",
        "letter_condense_pre_verdict",
        "letter_condense_final_floor",
        "letter_condense_recondense_repair",
    }
)


def _stage_doc(stage: str | None) -> str | None:
    s = stage or ""
    if s in CV_STAGES or "attribution" in s or (s.startswith("cv_") and s not in ("cv_extraction", "cv_audit")):
        return "cv"
    if s in LETTER_STAGES or s.startswith(("cover_letter", "letter_condense")):
        return "letter"
    return None


def stage_label(stage: str | None, role: str | None, attempt: int | None) -> str:
    """``cv_tailoring/writer``, ``cv_tailoring/corrector#2``, ``letter_condense_final_floor`` …"""
    stage = stage or "unlabelled"
    if role == "generator":
        return f"{stage}/corrector#{attempt if attempt is not None else '?'}"
    if role is None and stage in ("cv_tailoring", "cover_letter"):
        return f"{stage}/writer"
    if role == "reviewer":
        return f"{stage}/reviewer#{attempt if attempt is not None else '?'}"
    return stage


@dataclass
class Draft:
    """A snapshot of one document after one model call."""

    idx: int
    ts: str
    doc: str
    stage: str
    review_role: str | None
    review_attempt: int | None
    label: str
    partial: bool
    items: list[Item] = field(default_factory=list)

    @property
    def texts(self) -> list[str]:
        return [i.text for i in self.items]

    def stage_detail(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "review_role": self.review_role,
            "review_attempt": self.review_attempt,
            "draft_idx": self.idx,
            "ts": self.ts,
        }


def load_records(path: Path) -> list[dict[str, Any]]:
    """Stream a llm.jsonl; unparseable lines are skipped, records sorted by ts."""
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    out.sort(key=lambda r: str(r.get("ts") or ""))  # stable: file order breaks ties
    return out


def _response(rec: dict[str, Any]) -> dict | None:
    resp = rec.get("response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError:
            return None
    return resp if isinstance(resp, dict) else None


def _cv_shape(resp: dict) -> str | None:
    """'full' | 'partial' | None."""
    if "summary" in resp and ("work" in resp or "work_history" in resp):
        return "full"
    keys = set(resp)
    if keys and keys <= {"summary", "skills", "projects", "bullets"} and not keys & {"approved", "findings"}:
        return "partial"
    return None


def _letter_shape(resp: dict) -> bool:
    return "body" in resp and ("header" in resp or "recipient" in resp)


class _CVState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.segments: list[dict] = []

    def apply(self, resp: dict, shape: str) -> None:
        if shape == "full":
            self.data = copy.deepcopy(resp)
            self.segments = []
            return
        for key in ("summary", "skills"):
            if key in resp:
                self.data[key] = resp[key]
        if "projects" in resp and "bullets" not in resp:
            self.data["projects"] = resp["projects"]
        if "bullets" in resp:  # one role segment, no id
            self.segments.append({"bullets": resp.get("bullets"), "projects": resp.get("projects")})

    def snapshot(self) -> dict[str, Any]:
        snap = copy.deepcopy(self.data)
        if self.segments:
            work_key = "work_history" if "work_history" in snap else "work"
            snap[work_key] = list(snap.get(work_key) or []) + copy.deepcopy(self.segments)
        return snap


def _infer_generator(rec: dict[str, Any]) -> bool:
    """A corrector / rewriter call whose ``review_role`` was logged as null.

    The package-1 smoke run (mock provider) logs every corrector with
    ``review_role=null`` where real dev logs carry ``"generator"``; the system
    prompt names the call ("You are a tailored CV corrector", "You are a
    cover-letter corrector", "You rewrite a tailored CV prose draft ...").
    """
    head = str(rec.get("system") or "")[:300].lower()
    return "corrector" in head or head.lstrip().startswith("you rewrite")


def extract_drafts(records: list[dict[str, Any]], index_map: dict[str, int] | None = None) -> dict[str, list[Draft]]:
    """Return ``{"cv": [Draft...], "letter": [Draft...]}`` ordered by ts.

    Document and chain are decided from the response SHAPE first (stage labels
    leak: the letter writer is logged with an empty stage in real dev logs and
    under ``cv_audit`` in the smoke run). A draft whose stage does not belong to
    its document is attributed to the document's first chain (``cv_tailoring`` /
    ``cover_letter``) — as ``writer``, or as ``corrector#n`` when the system
    prompt says it is a corrector.
    """
    drafts: dict[str, list[Draft]] = {"cv": [], "letter": []}
    state = _CVState()
    corrector_n: dict[tuple[str, str], int] = {}
    for rec in records:
        if rec.get("review_role") == "reviewer" or not rec.get("ok", True):
            continue
        resp = _response(rec)
        if resp is None:
            continue
        stage = rec.get("stage") or ""
        role, attempt = rec.get("review_role"), rec.get("review_attempt")
        if _letter_shape(resp):
            shape_doc, cv_shape = "letter", None
        elif (cv_shape := _cv_shape(resp)) is not None:
            shape_doc = "cv"
        else:
            continue
        stage_doc = _stage_doc(stage)
        if stage_doc != shape_doc:
            if shape_doc == "cv" and cv_shape == "partial":
                continue  # a partial CV shape is only meaningful inside the CV writer chain
            stage = "cover_letter" if shape_doc == "letter" else "cv_tailoring"
        doc = shape_doc
        if role is None and _infer_generator(rec):
            role = "generator"
            key = (doc, stage)
            corrector_n[key] = corrector_n.get(key, 0) + 1
            attempt = corrector_n[key]
        elif role is None and stage in ("cv_tailoring", "cover_letter"):
            for k in [k for k in corrector_n if k[0] == doc]:
                corrector_n[k] = 0  # a new writer draft starts a new chain
        label = stage_label(stage, role, attempt)
        if doc == "cv":
            state.apply(resp, cv_shape or "full")
            items = flatten_cv(state.snapshot(), index_map=index_map)
            partial = cv_shape == "partial"
        else:
            items = flatten_letter(resp)
            partial = False
        drafts[doc].append(
            Draft(
                idx=len(drafts[doc]),
                ts=str(rec.get("ts") or ""),
                doc=doc,
                stage=stage,
                review_role=role,
                review_attempt=attempt,
                label=label,
                partial=partial,
                items=items,
            )
        )
    return drafts
