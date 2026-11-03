# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R-2 (UAT-3 review cards): what changed between the document the last terminal
review verdict read and the document that was delivered.

Pure and deterministic — no LLM, no I/O. The terminal-review check persists the
result (``ATSCheck.unreviewed_changes``) when the delivered draft is not the
reviewed one, so the page can show the passages nobody re-read.
"""

from __future__ import annotations

import logging
from typing import Any

from applire.schemas.cv import TailoredCVData
from applire.schemas.cv_sections import ContentSnapshot
from applire.services.cv_section_editor import (
    _section_contents,
    build_content_snapshot,
    snapshot_entry_lines,
)

logger = logging.getLogger(__name__)

#: The most sections one delta names, and the most characters of one side of one
#: section. A bound on the persisted report, not on what is compared.
MAX_CHANGES = 8
MAX_SIDE_CHARS = 4000


def _clip(text: str) -> str:
    if len(text) <= MAX_SIDE_CHARS:
        return text
    return text[: MAX_SIDE_CHARS - 1].rstrip() + "…"


def _position_key(p: Any) -> str:
    return f"position::{p.work_id}" if p.work_id else f"position#{p.index}"


def _keyed(snap: ContentSnapshot, contents: dict[str, str]) -> dict[str, str]:
    """``contents`` (``_section_contents``) re-keyed so a position is named by its
    ``work_id`` — the snapshot ``id`` is a fresh uuid4 per snapshot."""
    rename = {f"position::{p.id}": _position_key(p) for p in snap.positions}
    return {rename.get(sid, sid): text for sid, text in contents.items()}


def _keyed_contents(td: TailoredCVData) -> dict[str, str]:
    snap_dict = build_content_snapshot(td)
    snap = ContentSnapshot.model_validate(snap_dict)
    return _keyed(snap, _section_contents(snap, {}, snapshot_entry_lines(snap_dict, td.model_dump())))


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def cv_unreviewed_changes(
    reviewed: TailoredCVData,
    delivered: TailoredCVData,
    delivered_snapshot: dict | None,
    *,
    final_raw: TailoredCVData | None = None,
) -> list[dict]:
    """The sections the corrector changed after the last review, as
    ``{section_id, before, after}`` (``before`` = the reviewed text, ``after`` = the
    delivered text), in document order, at most :data:`MAX_CHANGES`.

    ``reviewed`` is the RAW compose of the reviewed draft and ``final_raw`` the RAW
    compose of the final prose draft (``None`` = ``delivered``): the corrector's work
    is the diff of those two, so what the page ladder or the condense pass cut after
    the verdict (the page card's "already left out") never appears as a correction.
    Of that diff only units still present in the DELIVERED section are kept — a unit
    the condense then removed is not something the reader can see."""
    if not delivered_snapshot:
        return []
    snap = ContentSnapshot.model_validate(delivered_snapshot)
    delivered_contents = _section_contents(
        snap, {}, snapshot_entry_lines(delivered_snapshot, delivered.model_dump())
    )
    delivered_keyed = _keyed(snap, delivered_contents)
    key_to_sid = {_position_key(p): f"position::{p.id}" for p in snap.positions}
    before_all = _keyed_contents(reviewed)
    after_all = _keyed_contents(final_raw if final_raw is not None else delivered)

    out: list[dict] = []
    for key, before in before_all.items():
        after = after_all.get(key)
        if after is None or after == before or key not in delivered_keyed:
            continue
        delivered_text = delivered_keyed[key]
        before_lines, after_lines = _lines(before), _lines(after)
        delivered_lines = set(_lines(delivered_text))
        added = [ln for ln in after_lines if ln not in before_lines]
        removed = [ln for ln in before_lines if ln not in after_lines]
        if added:
            visible = any(ln in delivered_lines for ln in added)
        else:  # a pure removal: visible as an absence in the delivered section
            visible = bool(removed) and not any(ln in delivered_lines for ln in removed)
        if not visible:
            continue
        # Lines the ladder/condense cut AFTER the verdict (in the final raw section,
        # gone from the delivered one) are the page card's business: drop them from
        # the "before" side too, or they read as struck-through corrector work.
        cut = {ln for ln in after_lines if ln not in delivered_lines}
        if cut:
            kept = [ln for ln in before_lines if ln not in cut]
            if kept:
                before = "\n".join(kept)
        out.append({
            "section_id": key_to_sid.get(key, key),
            "before": _clip(before),
            "after": _clip(delivered_text),
        })
        if len(out) >= MAX_CHANGES:
            break
    return out


def _letter_body(letter: Any) -> str:
    body = letter.get("body") if isinstance(letter, dict) else None
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not isinstance(paragraphs, list):
        return ""
    return "\n\n".join(str(p).strip() for p in paragraphs if str(p or "").strip())


def letter_unreviewed_changes(reviewed: dict, delivered: dict) -> list[dict]:
    """The letter is one body: ``[{"section_id": "body", before, after}]`` when the
    delivered paragraphs differ from the reviewed ones, else ``[]``."""
    before, after = _letter_body(reviewed), _letter_body(delivered)
    if before == after:
        return []
    return [{"section_id": "body", "before": _clip(before), "after": _clip(after)}]
