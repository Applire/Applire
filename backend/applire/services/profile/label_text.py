# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Display truncation for receipt labels (one implementation, ADR-066)."""
from __future__ import annotations


def truncate_at_word(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters on a word boundary, with ``…``.

    Whitespace is collapsed first. Text that already fits is returned as-is.
    A single word longer than the budget is hard-cut (nothing better exists).
    """
    body = " ".join((text or "").split())
    if len(body) <= limit:
        return body
    cut = body[: max(limit - 1, 0)]
    if body[len(cut)] != " ":  # landed inside a word: back up to the last space
        space = cut.rfind(" ")
        if space > 0:
            cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"
