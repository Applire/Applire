# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — the issue record shared by all four class scorers."""

from __future__ import annotations

from typing import Any

CLASSES = ("L", "U", "D", "S")
CLASS_NAMES = {"L": "language mix", "U": "underselling", "D": "duplicates", "S": "upselling"}


def make_issue(
    cls: str,
    doc: str,
    loc: str,
    text: str,
    evidence: Any,
    subclass: str,
    **extra: Any,
) -> dict[str, Any]:
    """``{class, doc, loc, text, evidence, subclass, stage}`` (+ extras).

    ``doc`` is ``"cv"`` or ``"letter"``; a run-level finding that belongs to
    neither alone (a vault fact omitted from BOTH documents) uses ``"both"``.
    ``stage`` starts ``None`` and is filled by ``attribution.attribute_issues``.
    """
    rec: dict[str, Any] = {
        "class": cls,
        "doc": doc,
        "loc": loc,
        "text": text,
        "evidence": evidence,
        "subclass": subclass,
        "stage": None,
    }
    rec.update(extra)
    return rec


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dicts(value: Any) -> list[dict]:
    return [v for v in as_list(value) if isinstance(v, dict)]
