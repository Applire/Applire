# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — figure extraction: percentages, currency amounts, years, numbers.

Pure functions, table-driven-tested. Figures are compared by canonical value
per kind; a figure in the document with no matching figure anywhere in the
vault is a deterministic red flag (``unbacked``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Figure:
    kind: str  # "percent" | "currency" | "year" | "number"
    value: str  # canonical: separators stripped, decimal dot, multiplier folded
    raw: str  # verbatim substring for report details


# Multiplier suffixes folded into the canonical value (DE + EN).
_MULTIPLIERS = {
    "k": "k", "tsd": "k", "tausend": "k",
    "m": "m", "mio": "m", "million": "m", "millionen": "m",
    "mrd": "b", "b": "b", "billion": "b", "milliarden": "b",
}
_MULT_RE = r"(?:k|tsd\.?|tausend|m|mio\.?|million(?:en)?|mrd\.?|b|billion|milliarden)"

_PERCENT_RE = re.compile(r"[~≈]?\s*(\d+(?:[.,]\d+)?)\s*%")
_CURRENCY_RE = re.compile(
    rf"(?:[€$£]\s*(\d[\d.,]*)\s*({_MULT_RE})?"
    rf"|(\d[\d.,]*)\s*({_MULT_RE})?\s*(?:€|EUR|USD|CHF|GBP|\$|£))",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
# Plain numbers: ≥ 2 digits (single digits sit below the signal floor — date
# fragments and spelled-out-number wording variance would produce false red
# flags; the 2026-07-18 bug classes are all multi-digit figures).
_NUMBER_RE = re.compile(r"\b\d{1,3}(?:[.,]\d{3})+\b|\b[1-9]\d+(?:[.,]\d+)?\b")

_GROUPED_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def _canonical_number(s: str) -> str:
    """Normalize separators: '1.000'/'1,000' → '1000'; '12,5' → '12.5'."""
    s = s.strip()
    if _GROUPED_RE.match(s):
        return re.sub(r"[.,]", "", s)
    s = s.replace(",", ".")
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _canonical_multiplier(suffix: str | None) -> str:
    if not suffix:
        return ""
    return _MULTIPLIERS.get(suffix.lower().rstrip("."), "")


def extract_figures(text: str) -> list[Figure]:
    """All figures in a text, in priority order percent > currency > year > number.

    A span consumed by a higher-priority kind is invisible to lower ones, so
    "70%" yields one percent figure, not a percent plus a number.
    """
    figures: list[Figure] = []
    consumed: list[tuple[int, int]] = []

    def _free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in consumed)

    for m in _PERCENT_RE.finditer(text):
        figures.append(Figure("percent", _canonical_number(m.group(1)), m.group(0).strip()))
        consumed.append(m.span())

    for m in _CURRENCY_RE.finditer(text):
        if not _free(*m.span()):
            continue
        digits = m.group(1) or m.group(3)
        mult = _canonical_multiplier(m.group(2) or m.group(4))
        figures.append(Figure("currency", _canonical_number(digits) + mult, m.group(0).strip()))
        consumed.append(m.span())

    for m in _YEAR_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(Figure("year", m.group(1), m.group(0)))
        consumed.append(m.span())

    for m in _NUMBER_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(Figure("number", _canonical_number(m.group(0)), m.group(0)))
        consumed.append(m.span())

    return figures
