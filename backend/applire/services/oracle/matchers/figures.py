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
# #215: the alternation is generated LONGEST-FIRST from the table above, and
# must stay that way. Hand-written, the single-character "m"/"b" alternatives
# sat before "mio"/"mrd"/"million(en)"/"milliarden"/"billion", and Python's
# regex alternation is first-match, not longest-match. For the SYMBOL-
# PREFIXED currency form ("€7 Mrd.") nothing downstream forces the engine to
# backtrack past the trivial "M", so the figure canonicalised to "7m" — a
# factor of 1000 wrong, reported to the candidate as "No vault evidence for
# figure(s): €7 M." (The digit-then-symbol form "7 Mrd. €" was correct only
# by accident: the required trailing currency symbol forces backtracking.)
# Same lesson, same subsystem: ``oracle/extract.py``'s ``_ABBREVIATIONS`` is
# sorted longest-first for exactly this reason (#292).
#
# ADR-062 classification: FACT. Which magnitude token follows a number is
# settled by the two tokens alone — no reading for meaning.
_MULT_RE = (
    "(?:"
    + "|".join(
        re.escape(tok) + r"\.?"
        for tok in sorted(_MULTIPLIERS, key=lambda t: (-len(t), t))
    )
    + ")"
)

_PERCENT_RE = re.compile(r"[~≈]?\s*(\d+(?:[.,]\d+)?)\s*%")
_CURRENCY_RE = re.compile(
    rf"(?:[€$£]\s*(\d[\d.,]*)\s*({_MULT_RE})?"
    rf"|(\d[\d.,]*)\s*({_MULT_RE})?\s*(?:€|EUR|USD|CHF|GBP|\$|£))",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
# Plain numbers: ≥ 2 digits (single digits sit below the signal floor — date
# fragments and spelled-out-number wording variance would produce false red
# flags; the 2026-07-18 bug classes are all multi-digit figures), OR a single
# digit carrying an explicit decimal part ("8,2", "3.1" — #377/ADR-067 clause
# 4: a single-digit DECIMAL is unambiguous quantified substance, e.g. an LTIF
# safety ratio, and is never a spelled-out-number wording variant since it is
# already a digit; the bare single-digit exclusion above is NOT relaxed for
# non-decimal single digits, which stay excluded as date-fragment-prone).
_NUMBER_RE = re.compile(
    r"\b\d{1,3}(?:[.,]\d{3})+\b"
    r"|\b[1-9]\d+(?:[.,]\d+)?\b"
    r"|\b[1-9][.,]\d+\b"
)

# #237 (run-4 residual): a SINGLE digit immediately followed by "+" — "5+",
# "10+" (the "10" half is already caught by ``_NUMBER_RE`` above; only the
# single-digit case is new here) — is an unambiguous team-size/count
# quantifier, never a date fragment (dates never carry a trailing "+") and
# never subject to spelled-out-number wording variance (it is already a
# digit). Narrow and additive: it does not relax the general single-digit
# exclusion above for any other context.
_PLUS_QUANTIFIER_RE = re.compile(r"\b([1-9])\+")

_GROUPED_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")

# #412 (charter run 13 ground truth, operations_marcus_de): German states a
# percentage range's unit ONCE, at the end — "von 61 auf 73 %", "zwischen 4,1
# und 2,3 %", "61–73 %" — while explicit document prose repeats it ("von 61 %
# auf 73 %"). Without distributing the trailing % across the range, the two
# sides canonicalise to DIFFERENT kinds (number 61 vs percent 61) and a
# candidate-attested improvement grades "unbacked". Both the claim extractor
# and ``build_vault_index`` call ``extract_figures``, so the distribution
# here fixes both directions by construction (the #374 pattern). Scoped to
# percent — the issue's evidenced class; currency ranges stay as-is.
#
# The word form REQUIRES the range preposition (von/from/zwischen/between):
# a bare "stieg 2021 auf 15 %" must never distribute onto what is a year,
# and the preposition is what makes the two numbers one range construction.
_PERCENT_RANGE_WORD_RE = re.compile(
    r"\b(?:von|from|zwischen|between)\s+(\d+(?:[.,]\d+)?)"
    r"\s+(?:auf|bis|und|to|and)\s+\d+(?:[.,]\d+)?\s*%",
    re.IGNORECASE,
)
# Dash form: the leading number must be immediately range-joined ("61–73 %");
# a % or digit right before the candidate start means it is not a bare range
# start (either already unit-carrying or the tail of a larger number).
_PERCENT_RANGE_DASH_RE = re.compile(
    r"(?<![\d.,%])(\d+(?:[.,]\d+)?)\s*[-‒–—―−]\s*\d+(?:[.,]\d+)?\s*%"
)
# A year never receives the distributed unit ("von 2020 auf 25 %" is a
# time-to-value phrasing, not a percentage range).
_RANGE_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _percent_range_bare_spans(text: str) -> list[tuple[str, tuple[int, int]]]:
    """(raw, span) of every bare leading range number owed a trailing %."""
    out: list[tuple[str, tuple[int, int]]] = []
    for pattern in (_PERCENT_RANGE_WORD_RE, _PERCENT_RANGE_DASH_RE):
        for m in pattern.finditer(text):
            raw = m.group(1)
            if _RANGE_YEAR_RE.fullmatch(raw):
                continue
            out.append((raw, m.span(1)))
    return out

# #374 (recon-verified 2026-08-01, edge probe 2026-07-29): a standard or
# regulation identifier ("ISO 15189", "ISO/IEC 27001", "21 CFR Part 11") is
# NOT a quantified figure — it is a fact-level exclusion (ADR-062 clause 1),
# never a heuristic about meaning, so the list is narrow and literal.
# Uppercase-only and word-boundary-anchored: lowercase "en" is ordinary
# German prose (e.g. "kennen"), and only the exact uppercase token counts as
# the DIN/EN standard prefix. Both directions of #374 traced to the SAME
# root cause — the claim-side false "No vault evidence for figure(s): 15189"
# and the vault-side pollution where a certification like "ISO 9001 Lead
# Auditor" put ("number", "9001") into ``figure_map`` — because both the
# claim extractor and ``build_vault_index`` call this one shared function;
# the guard lives here, not in either caller, so both directions are fixed
# by construction.
_IDENTIFIER_PREFIX = r"(?:ISO|IEC|EN|DIN|CFR|GAMP|ASTM|ANSI|RFC|IEEE|VDE|VDI)"
# The common shape: prefix immediately followed (one space/"/"/"-") by the
# identifier's digits — "ISO 15189", "DIN EN 9100", "ISO/IEC 27001".
_IDENTIFIER_AFTER_RE = re.compile(
    rf"\b{_IDENTIFIER_PREFIX}(?:\s*/\s*{_IDENTIFIER_PREFIX})?[ /-]\s*(\d+)"
)
# "21 CFR" — the CFR shape uniquely puts the identifying number BEFORE the
# prefix (the US federal register title number), unlike every other prefix
# in the list, so this is scoped to CFR only, not generalized.
_IDENTIFIER_BEFORE_CFR_RE = re.compile(r"\b(\d+)\s+CFR\b")
# "21 CFR Part 11" — the part/section number follows "Part" within a few
# tokens of the CFR mention.
_CFR_PART_RE = re.compile(r"\bCFR\b(?:\s+\S+){0,3}?\s+[Pp]art\s+(\d+)")


def _identifier_spans(text: str) -> list[tuple[int, int]]:
    """Digit spans immediately owned by a standard/regulation identifier."""
    spans: list[tuple[int, int]] = []
    for pattern in (_IDENTIFIER_AFTER_RE, _IDENTIFIER_BEFORE_CFR_RE, _CFR_PART_RE):
        for m in pattern.finditer(text):
            spans.append(m.span(1))
    return spans


def _overlaps_identifier(span: tuple[int, int], identifier_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= a or start >= b) for a, b in identifier_spans)


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
    # #374: computed once — percent/currency extraction is deliberately NOT
    # guarded (a standard body never prefixes a percentage or a currency
    # amount), only the year- and number-kind matches consult it.
    identifier_spans = _identifier_spans(text)

    def _free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in consumed)

    # #412 pre-pass: the bare leading number of a percent range becomes a
    # percent figure (unit distributed) and its span is consumed, so the
    # number-kind pass below never re-reads it as a plain number — the claim
    # side must carry the percent reading ONLY, or 'von 61 auf 73 %' in a
    # document could never ground on a vault that wrote 'von 61 % auf 73 %'.
    # (The vault side re-adds the plain-number reading via
    # ``extract_range_bare_numbers`` — vault-side widening, same asymmetry as
    # ``extract_spelled_figures``.)
    for raw, span in _percent_range_bare_spans(text):
        if not _free(*span):
            continue
        figures.append(Figure("percent", _canonical_number(raw), raw))
        consumed.append(span)

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
        if _overlaps_identifier(m.span(), identifier_spans):
            # #374: "ISO 2015" must not become a year figure either — mark
            # the span consumed so the number-kind pass below does not pick
            # it up as a plain number instead.
            consumed.append(m.span())
            continue
        figures.append(Figure("year", m.group(1), m.group(0)))
        consumed.append(m.span())

    for m in _NUMBER_RE.finditer(text):
        if not _free(*m.span()):
            continue
        if _overlaps_identifier(m.span(), identifier_spans):
            consumed.append(m.span())
            continue
        figures.append(Figure("number", _canonical_number(m.group(0)), m.group(0)))
        consumed.append(m.span())

    for m in _PLUS_QUANTIFIER_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(Figure("number", m.group(1), m.group(0)))
        consumed.append(m.span())

    return figures


# ── #237 (run-4 residual) — spelled-out small numbers, VAULT SIDE ONLY ──────
# The digit-vs-word gap: a candidate's own vault prose spells small counts out
# ("a team of five tech leads"), while generated document prose (or the
# candidate's own quantifier phrasing, "5+") uses digits. Without bridging
# the two, a genuine vault fact never surfaces as figure evidence at all, and
# a fabricated-looking claim can only ever land ``unbacked`` (no evidence)
# rather than the more precise ``misattributed`` (real evidence, wrong
# owner) run-4's ground truth calls for.
#
# Independent, deliberately NOT reused from
# ``services/profile/reconcile/stance.py``'s own ``_spelled_figures`` — same
# rationale as ``oracle/extract.py``'s independent ``_LEGAL_FORM_RE`` copy:
# oracle depends on the reconcile write path's OUTPUT, never the reverse, and
# neither module wants a cross-package import for a few dozen lines of
# regex. "one"/"eins" stay excluded (ambiguous with the article — parsing
# them would manufacture a "1" out of almost any sentence, fail-closed,
# mirroring reconcile/stance.py's own exclusion).
#
# Called ONLY from ``matchers/vault.py`` when indexing the candidate's own
# profile text — never from the claim-extraction side (``oracle/extract.py``
# / ``verify_claim``'s ``extract_figures(claim.text)`` call), so a
# generated document's OWN prose is never granted the benefit of the doubt
# for a spelled-out figure it never actually wrote as a digit. It only
# widens what the VAULT is recognized to already support.
_EN_UNITS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9,
}
_EN_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_DE_UNITS = {
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9,
}
_DE_TEENS = {
    "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "dreizehn": 13,
    "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15, "sechzehn": 16,
    "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
}
_DE_TENS = {
    "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40,
    "fünfzig": 50, "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
    "achtzig": 80, "neunzig": 90,
}
_SMALL_WORDS = {**_EN_UNITS, **_EN_TEENS, **_EN_TENS, **_DE_UNITS, **_DE_TEENS, **_DE_TENS}
_DE_COMPOUND_UNITS = {"ein": 1, **_DE_UNITS}
_DE_UNIT_ALT = "|".join(_DE_COMPOUND_UNITS)
_DE_UND_RE = re.compile(
    rf"^({_DE_UNIT_ALT})und({'|'.join(_DE_TENS)})(hundert|tausend)?$"
)
_WORD_RE = re.compile(r"[a-zäöüß]+")


def extract_spelled_figures(text: str) -> list[Figure]:
    """Vault-side-only figures from EN/DE spelled-out small number words.

    Deliberately narrow: units (two-nine), teens (ten-nineteen), tens
    (twenty-ninety), and DE "-und-" compounds (funfundzwanzig). No scale
    words (hundred/thousand) — the reconcile-side grounding predicate widens
    much further because its job is "was this ever said", a much lower bar
    than becoming a citable figure. "one"/"eins" excluded (article
    ambiguity, fail-closed).
    """
    figures: list[Figure] = []
    lowered = text.lower()
    for m in _WORD_RE.finditer(lowered):
        tok = m.group(0)
        small = _SMALL_WORDS.get(tok)
        if small is not None:
            figures.append(Figure("number", str(small), tok))
            continue
        de = _DE_UND_RE.match(tok)
        if de:
            value = _DE_COMPOUND_UNITS[de.group(1)] + _DE_TENS[de.group(2)]
            figures.append(Figure("number", str(value), tok))
    return figures


def extract_range_bare_numbers(text: str) -> list[Figure]:
    """Vault-side-only plain-number readings for #412 range starts.

    ``extract_figures`` now reads the bare leading number of a percent range
    as a percent (unit distributed). On the VAULT side that number keeps its
    pre-#412 plain-number reading TOO, so a document citing the range start
    as a bare figure still finds evidence — widening what the vault is
    recognized to support, never what a document is allowed to claim (same
    one-sided contract as ``extract_spelled_figures``; called only from
    ``matchers/vault.py``). ``_NUMBER_RE.fullmatch`` keeps the pre-#412
    signal floor: a single-digit range start ("von 8 bis 3 %") was never a
    plain-number unit before and does not become one now.
    """
    figures: list[Figure] = []
    for raw, _span in _percent_range_bare_spans(text):
        if _NUMBER_RE.fullmatch(raw):
            figures.append(Figure("number", _canonical_number(raw), raw))
    return figures
