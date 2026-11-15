# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Characters no CV or letter ever means to print (Sorbet WP-Q3, #835:277).

Sorbet platform-1 delivery run 1: the CV writer (Requesty ``gpt-5.6-luna``,
thinking off) ended a Rasselstein bullet with U+FFFC OBJECT REPLACEMENT
CHARACTER; the corrector carried it through four rounds and the PDF printed a
box glyph on page 2. No prompt rule can ask a model not to emit a tokenizer
artefact, and the character carries no content in any language: removing it is
a character fact (ADR-062 clause 1), not a judgement about the text.

The set is closed and small on purpose (MD-Q7 F4): the object-replacement
character and the C0/C1 control characters other than newline, tab and carriage
return. Nothing else: the zero-width joiner and non-joiner carry meaning in
Persian, Devanagari and emoji sequences ("می‌خواهم", "👨‍👩‍👧"), and stripping
them made a recorded employer name unequal to itself (adversarial 1b, F4: an
unsatisfiable ``cv.recorded_name`` finding). Every letter, digit, symbol, dash
and typographic quote of any language passes untouched.
"""
from __future__ import annotations

import re
from typing import Any

_STRAY = re.compile("[\ufffc\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_stray_glyphs(value: Any) -> Any:
    """``value`` with every stray character removed from every string in it
    (dicts and lists are copied, never mutated). A string that held only stray
    characters becomes ``""``."""
    if isinstance(value, str):
        if not _STRAY.search(value):
            return value
        return _STRAY.sub("", value)
    if isinstance(value, dict):
        return {k: strip_stray_glyphs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_stray_glyphs(v) for v in value]
    return value
