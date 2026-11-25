# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH-1b (founder ruling DH-1, 2026-11-25, "reviewer tags, truth holds"; built as a
separate call on the main session's DH-1b instruction): sort the final review's OPEN
findings into truth | coverage | form.

Why a separate call and not a field on the reviewer: a ``class`` field inside the
terminal reviewer's schema made that reviewer stricter — on four CVs the delivery had
approved, blocking verdicts went from 1/20 to 11/20 (replays of the persisted input,
gpt-5.6-luna, reasoning off). The reviewer's prompt stays byte-identical; this call
only labels what it already found. It never judges a finding right or wrong and never
adds one. Unsure → truth (fails closed), and every failure of the call reads as truth
in ``services/point_class``.
"""
from __future__ import annotations

import json

POINT_CLASS_SYSTEM_PROMPT = """You sort the open findings of a final review of a job-application document into three classes. You do not judge whether a finding is right, you do not add or drop findings, and you do not rewrite them.

- truth: the finding says the document states something the candidate's profile does not back as written — untrue, unbacked, overstated, attributed to the wrong role, or the same claim said twice.
- coverage: the finding says something the profile backs, or the job posting asks for, is missing from the document or not shown clearly enough.
- form: the finding is about spelling, the written form of a name, shape, length or the size of a list — not about whether a statement is true.

A finding that is about both truth and something else is truth. When you are unsure, answer truth.

Respond ONLY with a valid JSON object — no markdown, no explanations:
{"classes": [{"index": 0, "class": "truth" or "coverage" or "form"}]}
One entry per finding, by its index."""


def build_point_class_prompt(findings: list[str], document: dict | None) -> str:
    """The findings as the reviewer wrote them, by index, after the document they
    point at (as delivered)."""
    parts = []
    if document:
        parts.append(
            "DOCUMENT (as delivered — the findings point at sentences in it):\n"
            + json.dumps(document, ensure_ascii=False, indent=1, default=str)
        )
    parts.append("FINDINGS:\n" + "\n".join(f"[{i}] {t}" for i, t in enumerate(findings)))
    return "\n\n".join(parts)
