# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The closed-domain DE/EN language-name table (#709, ADR-046 amended 2026-10-07).

Language names are a finite, authoritative list. Mapping ``Englisch`` to
``english`` is a lookup in that list — a FACT under ADR-062 clause 1, not a
judgement — so the vault's language applier and the import witness may use it
directly, with no model turn. Skills are an open domain and are deliberately NOT
covered: whether two skill names are one competence stays the model's judgement.

**Whole-string only.** ``canonical_language`` folds a name that IS, after
``strip`` + ``casefold``, a table key or value. It never strips a parenthetical
or a level (``Chinesisch (Mandarin)``, ``Deutsch (Muttersprache)``) and never
matches inside a longer name: ``Chinese (Cantonese)`` must not fold into
``Chinesisch (Mandarin)``, and ``Deutsche Gebärdensprache`` is not ``Deutsch``
(adversarial review of the WP-V delta, 2026-10-07). A name the table does not
know returns ``None`` and the caller falls back to whatever it did before.

One table for the codebase (ADR-066): ``services/cv._LANGUAGE_NAME_CANON`` — the
render-side copy that collapses ``Deutsch``/``German`` rows on a CV — holds the
same 24 pairs; it is meant to import :data:`LANGUAGE_NAME_DE_EN` from here.
"""
from __future__ import annotations

import unicodedata

#: German name -> English name, casefolded. The pairs the render side has used
#: since E049 charter run 11 (``services/cv._LANGUAGE_NAME_CANON``), verbatim.
LANGUAGE_NAME_DE_EN: dict[str, str] = {
    "deutsch": "german", "englisch": "english", "französisch": "french",
    "spanisch": "spanish", "italienisch": "italian", "polnisch": "polish",
    "türkisch": "turkish", "russisch": "russian", "niederländisch": "dutch",
    "portugiesisch": "portuguese", "arabisch": "arabic", "chinesisch": "chinese",
    "japanisch": "japanese", "koreanisch": "korean", "hindi": "hindi",
    "schwedisch": "swedish", "dänisch": "danish", "norwegisch": "norwegian",
    "finnisch": "finnish", "tschechisch": "czech", "ungarisch": "hungarian",
    "rumänisch": "romanian", "griechisch": "greek", "ukrainisch": "ukrainian",
}
_ENGLISH_NAMES: frozenset[str] = frozenset(LANGUAGE_NAME_DE_EN.values())


def _fold(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return unicodedata.normalize("NFC", name).strip().casefold()


def canonical_language(name: object) -> str | None:
    """The table's English key for ``name``, or ``None`` when the WHOLE name is
    not in the table (see the module docstring — no partial matching)."""
    folded = _fold(name)
    if not folded:
        return None
    if folded in LANGUAGE_NAME_DE_EN:
        return LANGUAGE_NAME_DE_EN[folded]
    if folded in _ENGLISH_NAMES:
        return folded
    return None


def same_language(a: object, b: object) -> bool:
    """True only when BOTH names are whole table entries naming one language."""
    ca = canonical_language(a)
    return ca is not None and ca == canonical_language(b)


# ---------------------------------------------------------------------------
# #759 (founder ruling E5-2, 2026-10-08): render the LANGUAGES list in the
# document's language. Same table, same whole-string rule, plus a closed
# vocabulary of level WORDS. CEFR codes (A1 … C2) are language-invariant and are
# never touched; a level the vocabulary does not know is rendered verbatim.
# ---------------------------------------------------------------------------

#: Level words, (German display form, English display form). Whole-string,
#: casefolded match on either side. Deliberately small: a level phrase the
#: candidate wrote in their own words ("conversational, rusty") is theirs.
LANGUAGE_LEVEL_DE_EN: tuple[tuple[str, str], ...] = (
    ("Muttersprache", "Native"),
    ("Muttersprachlich", "Native speaker"),
    ("Fließend", "Fluent"),
    ("Verhandlungssicher", "Business fluent"),
    ("Sehr gut", "Very good"),
    ("Sehr gute Kenntnisse", "Very good command"),
    ("Gut", "Good"),
    ("Gute Kenntnisse", "Good command"),
    ("Grundkenntnisse", "Basic knowledge"),
    ("Grundlagen", "Basic"),
    ("Fortgeschritten", "Advanced"),
    ("Mittelstufe", "Intermediate"),
    ("Anfänger", "Beginner"),
)
_LEVEL_BY_FOLD: dict[str, tuple[str, str]] = {}
for _de, _en in LANGUAGE_LEVEL_DE_EN:
    _LEVEL_BY_FOLD.setdefault(_fold(_de), (_de, _en))
    _LEVEL_BY_FOLD.setdefault(_fold(_en), (_de, _en))
# Common English synonyms, folded onto one row (never a second German form).
_LEVEL_BY_FOLD.setdefault("mother tongue", ("Muttersprache", "Native"))
_LEVEL_BY_FOLD.setdefault("native language", ("Muttersprache", "Native"))
_LEVEL_BY_FOLD.setdefault("muttersprachler", ("Muttersprachlich", "Native speaker"))

_DE_BY_EN: dict[str, str] = {en: de for de, en in LANGUAGE_NAME_DE_EN.items()}


def localized_language_name(name: object, lang: str) -> object:
    """``name`` rendered in ``lang`` ("de"/"en") when the WHOLE name is a table
    entry; otherwise ``name`` unchanged (a name the table does not know, or a
    parenthetical form, is the candidate's own wording)."""
    canon = canonical_language(name)
    if canon is None or lang not in ("de", "en"):
        return name
    target = canon if lang == "en" else _DE_BY_EN.get(canon)
    if not target:
        return name
    if _fold(name) == target:
        return name  # already in the document's language — keep its casing
    return target.capitalize()


def localized_language_level(level: object, lang: str) -> object:
    """``level`` rendered in ``lang`` when the WHOLE level is a known level word;
    CEFR codes and unknown phrases unchanged."""
    row = _LEVEL_BY_FOLD.get(_fold(level))
    if row is None or lang not in ("de", "en"):
        return level
    target = row[0] if lang == "de" else row[1]
    if _fold(level) == _fold(target):
        return level
    return target
