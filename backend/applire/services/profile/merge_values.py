# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 11, am. 2026-11-02 (founder UAT-3, rulings M-1c, M-1e) — the
facts code checks about a "Zusammenführen" VALUE QUESTION.

The merged sentence itself is not checked by code (M-1e): the prompt states
the rules, the candidate sees both originals and confirms. A word-presence
check on the sentence caught no invented fact in ~90 real proposals and
rejected every correct translation of a bullet written in another language.

What stays deterministic is the question the card asks ("Was stimmt?"):
each value must be written in its own bullet, and the two must really differ
— the same 7 worded twice, a fact only one bullet adds, or "sieben" and "7"
is nothing to ask. A pick sent back by the client is held to the same
predicate (stateless; a forged value is refused).
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["value_in", "values_differ"]

# A number standing on its own ("8", "8-month", "7,500") — not the digit
# inside a name ("PSD2", "ISO9001"), which a value is never written as.
_STANDALONE_NUMBER_RE = re.compile(r"(?<![^\W\d_])\d+(?:[.,']\d+)*")

#: Number words the bullets of a DACH profile write out (de + en, 0–12).
_NUMBER_WORDS: dict[str, str] = {
    w: str(n)
    for n, words in enumerate(
        [
            ("null", "zero"), ("eins", "one"), ("zwei", "two"),  # "ein/eine" is the article
            ("drei", "three"), ("vier", "four"), ("fünf", "five"), ("sechs", "six"),
            ("sieben", "seven"), ("acht", "eight"), ("neun", "nine"), ("zehn", "ten"),
            ("elf", "eleven"), ("zwölf", "twelve"),
        ]
    )
    for w in words
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

#: A numeric value that is not written verbatim may be at most this long —
#: "8 months" for "8-month", never a sentence riding on one shared number.
_NUMERIC_VALUE_MAX = 25


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _norm(text: str) -> str:
    return " ".join(_nfc(text).split()).casefold().strip(" .,;:-")


def _norm_number(raw: str) -> str:
    """'7,500' / '7.500' / "7'500" → '7500'; '3.5' stays apart from '35'."""
    parts = re.split(r"[.,']", raw)
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
        return "".join(parts)
    return ".".join(parts)


def _digits(text: str) -> set[str]:
    return {_norm_number(m.group(0)) for m in _STANDALONE_NUMBER_RE.finditer(_nfc(text))}


def _numbers(text: str) -> set[str]:
    """Standalone numbers plus written-out ones ("sieben" → "7")."""
    words = {_NUMBER_WORDS[w] for w in (m.group(0).casefold() for m in _WORD_RE.finditer(_nfc(text)))
             if w in _NUMBER_WORDS}
    return _digits(text) | words


def value_in(value: str, text: str) -> bool:
    """``value`` is written in ``text``: verbatim (normalised), or — a short
    numeric value only — its numbers are ("8 months" in "8-month project")."""
    v = _norm(value)
    if not v:
        return False
    if v in _norm(text):
        return True
    nums = _digits(value)
    return bool(nums) and len(v) <= _NUMERIC_VALUE_MAX and nums <= _digits(text)


def values_differ(a: str, b: str) -> bool:
    """Two values the model calls different really are. Numeric values carry
    DISJOINT numbers: 7 vs 8 differ; "innerhalb von 7 Monaten" vs "7-month
    project completion" do not (the same 7); {5} vs {5, 30} do not (one bullet
    only adds a fact); "sieben" vs "7" do not. Other values differ in wording."""
    na, nb = _numbers(a), _numbers(b)
    if na and nb:
        return not (na & nb)
    if na or nb:
        return True
    return _norm(a) != _norm(b)
