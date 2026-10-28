# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 11 (UAT-1, SF-VAULT.23) — the fact-presence verifier for a
model-proposed merge of two vault items.

A merged sentence is a NEW claim written by a model, so before it can be
offered (and again before it is written) every number and every name token in
it must be present in one of the two sources. That is a FACT in ADR-062
clause 1's sense — "is this token present in this text" — never a judgement
about whether the merge is good; the judgement is the model's (the proposal)
and the candidate's (confirm or edit).

Fail closed: a token the check cannot place is a violation. A failed proposal
is not retried — the candidate gets the other three outcomes (adv-decision C3).

What counts as a name token: a word with an upper-case letter that is not the
first word of a sentence, an all-caps word of two or more letters, or any
word carrying a digit. German capitalises every noun, so a new German noun
also fails the check — deliberately: a noun absent from both bullets is new
content, which is exactly what a merge may not add.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

__all__ = ["merge_violations", "verify_merge"]

# A number with optional thousands/decimal separators: 7,500 · 7.500 · 3.5 · 60
_NUMBER_RE = re.compile(r"\d+(?:[.,']\d+)*")
# A word: letters/digits plus inner joiners (ISO-25010, C#, .NET handled loosely)
_WORD_RE = re.compile(r"[^\W_][\w\-/&+#.]*", re.UNICODE)
_SENTENCE_END_RE = re.compile(r"[.!?:;]\s*$")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _norm_number(raw: str) -> str:
    """'7,500' / '7.500' / "7'500" → '7500'; '3.5' stays distinguishable from
    '35' by keeping a decimal part shorter than three digits."""
    parts = re.split(r"[.,']", raw)
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
        return "".join(parts)  # thousands separators
    return ".".join(parts)


def _numbers(text: str) -> set[str]:
    return {_norm_number(m.group(0)) for m in _NUMBER_RE.finditer(_nfc(text))}


def _strip_word(word: str) -> str:
    return word.strip(".-/&+#").casefold()


def _source_words(sources: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for src in sources:
        for m in _WORD_RE.finditer(_nfc(src)):
            w = _strip_word(m.group(0))
            if w:
                out.add(w)
                # compound / hyphenated parts count on their own too
                out.update(p for p in re.split(r"[-/&+]", w) if p)
    return out


def _present(word: str, src_words: set[str]) -> bool:
    w = _strip_word(word)
    if not w:
        return True
    if w in src_words:
        return True
    parts = [p for p in re.split(r"[-/&+]", w) if p]
    if len(parts) > 1 and all(_present(p, src_words) for p in parts):
        return True
    # inflection only (system/systems, enabled/enabling, Laborsystem/
    # Laborsysteme): a shared stem of at least five letters that leaves at
    # most three letters of the shorter word unmatched.
    if len(w) >= 5:
        for s in src_words:
            if len(s) < 5:
                continue
            shared = 0
            for a, b in zip(w, s):
                if a != b:
                    break
                shared += 1
            if shared >= max(5, min(len(w), len(s)) - 3):
                return True
            # a German compound's MODIFIER ("Lieferanten" in
            # "Lieferantenauswahl"): the word opens a source compound. Never a
            # HEAD alone (ADV-7): "Verantwortung" is not written in
            # "Mitverantwortung", "Leitung" not in "Begleitung" — dropping the
            # qualifying prefix turns co-responsibility into sole
            # responsibility, scope a merge may not add.
            if s.startswith(w):
                return True
    return False


def _is_name_token(word: str, sentence_initial: bool) -> bool:
    core = word.strip(".-/&+#")
    if not core:
        return False
    if any(ch.isdigit() for ch in core):
        return True
    letters = [ch for ch in core if ch.isalpha()]
    if len(letters) >= 2 and all(ch.isupper() for ch in letters):
        return True  # acronym
    if sentence_initial:
        # The first word of a sentence is capitalised by grammar; only an
        # inner capital (camel case: "LeanIX", "eBR") still marks a name.
        return any(ch.isupper() for ch in core[1:])
    return any(ch.isupper() for ch in core)


#: Connectors a merged sentence may add (EN + DE). Every OTHER word of three
#: or more letters must be written in a source (``mode="content"``).
_FUNCTION_WORDS: frozenset[str] = frozenset(
    """a an and as at by for from in into of on or the to with while which that
    this these those its their both also well plus including incl via through
    across over under per than then thereby resulting result leading
    und oder sowie als auch bei durch für fuer von vom zum zur zu im in an am
    auf aus mit nach über ueber unter der die das den dem des ein eine einer
    eines einem einen dabei damit wodurch sodass so dass sowohl inklusive inkl
    while whilst""".split()
)


def merge_violations(candidate: str, sources: Iterable[str], *, mode: str = "content") -> list[str]:
    """Every number or name token in ``candidate`` absent from all ``sources``
    — and with ``mode="content"`` (the default) every other word of three or
    more letters that is neither in a source nor a connector. ``"names"``
    checks numbers and name tokens only (a lowercase tool name such as "dbt"
    or a new scope word such as "global" then passes unseen).

    Empty list = the candidate passes. Order = first appearance.
    """
    sources = [s for s in sources if isinstance(s, str)]
    text = _nfc(candidate).strip()
    if not text:
        return ["<empty>"]
    src_numbers: set[str] = set()
    for s in sources:
        src_numbers |= _numbers(s)
    src_words = _source_words(sources)

    violations: list[str] = []
    for m in _NUMBER_RE.finditer(text):
        n = _norm_number(m.group(0))
        if n not in src_numbers and m.group(0) not in violations:
            violations.append(m.group(0))

    sentence_initial = True
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        if not _NUMBER_RE.fullmatch(word.strip(".")):
            core = word.strip(".-/&+#")
            checked = _is_name_token(word, sentence_initial) or (
                mode == "content"
                and sum(ch.isalpha() for ch in core) >= 3
                and core.casefold() not in _FUNCTION_WORDS
            )
            if checked and not _present(word, src_words):
                if core not in violations:
                    violations.append(core)
        # the next word starts a sentence when this one closed it
        tail = text[m.end(): m.end() + 2]
        sentence_initial = bool(_SENTENCE_END_RE.search(word)) or tail.startswith((". ", "! ", "? "))
    return violations


def verify_merge(candidate: str | None, sources: Iterable[str], *, mode: str = "content") -> bool:
    """True when ``candidate`` is a non-empty sentence that adds no number or
    name token to ``sources``."""
    if not candidate or not candidate.strip():
        return False
    return not merge_violations(candidate, sources, mode=mode)
