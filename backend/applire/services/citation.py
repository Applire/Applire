# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared citation-verification instrument (ADR-068 clause 4, ADR-066 one-
logical-operation-one-implementation).

Extracted from ``services/outcome_critic.py`` (SF-CRITIC.11): every module
that lets an LLM ground a finding/judgement in a verbatim vault or document
span must verify that span the SAME way — punctuation-folded, ``ats_norm``-
folded, whitespace-collapsed — or two panels could disagree about whether an
identical quote "is" in a document. ``services/oracle/audit.py`` (ADR-068's
cross-language/restatement judgement seams) is the second consumer; adding a
third must import from here, never re-implement the fold.
"""
from __future__ import annotations

from typing import Any

from applire.services.ats_audit import _norm as ats_norm

# A finding is only surfaced on spans provably in the documents. Verification
# runs under normalisation, NEVER a raw ``in`` check: a model quotes prose
# with typographic punctuation (U+2019 apostrophes, curly quotes — the
# documented class that defeated an ASCII marker list once already) and may
# reflow whitespace; a naive substring check would silently drop true
# findings, quietly re-narrowing the very control the widened judgement is
# (an invisible recall cliff, not a fail-open bug).
_CITATION_PUNCT_FOLD = str.maketrans(
    {
        "’": "'",  # right single quotation mark (the U+2019 class)
        "‘": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "–": "-",  # en dash
        "—": "-",  # em dash
        " ": " ",  # no-break space
    }
)


def normalize_citation(text: str) -> str:
    """Punctuation-fold + the shared ``ats_norm`` fold + whitespace collapse.

    Layered ON TOP of ``ats_norm`` (the module's shared instrument), never
    instead of it — the two folds answer different questions and only their
    composition survives both a typographic quote and a case difference.
    """
    return " ".join(ats_norm(text.translate(_CITATION_PUNCT_FOLD)).split())


def citation_present(quote: str | None, units: list[str]) -> bool:
    """Is *quote* literally present (under normalisation) in any ONE unit?

    Per unit only: a span stitched from two units is not present (ADR-100
    cl. 3 corrected this docstring, which used to promise a "unit-joined"
    match the code never did). No minimum length and no word boundary — a
    finding's citation check; :func:`quote_in_vault` is the stricter Fact a
    vault quote that BACKS a claim must pass. Empty/None quotes are NOT
    present — a finding must cite."""
    if not quote or not quote.strip():
        return False
    q = normalize_citation(quote)
    if not q:
        return False
    for unit in units:
        if q in normalize_citation(unit):
            return True
    return False


# ── ADR-100 cl. 3 — "this span exists verbatim in one vault unit" ────────────

#: Units that state where or when, never what the candidate can do: a city or a
#: date line is not evidence for a requirement, however verbatim the quote is.
_NON_EVIDENCE_PATH_MARKERS = ("personal_info", ".dates")
#: Typed-value units: a skill, a language, a technology, a certification, a role
#: or an organisation name. Their text IS the field value (a language unit reads
#: "Deutsch Muttersprache"), so a quote naming the whole value is complete even
#: when it is one short word.
_TYPED_PATH_PREFIXES = ("skills", "languages", "certifications")
_TYPED_PATH_SUFFIXES = (".role", ".org")
_TYPED_PATH_INFIXES = (".technologies[",)
_MIN_WORDS = 3
_MIN_CJK_CHARS = 6


def _is_cjk(ch: str) -> bool:
    # CJK radicals/ideographs, kana, hangul and their punctuation/fullwidth
    # blocks: scripts written without spaces, where a word count is meaningless.
    return ord(ch) >= 0x2E80


def _latin_alnum(ch: str) -> bool:
    # A letter or digit of a script written WITH spaces ("Ü", "é", "ж" too): a
    # quote may not start or end inside such a word ("bersetzung" ⊄ "Übersetzung").
    return ch.isalnum() and not _is_cjk(ch)


def _occurrences(text: str, needle: str) -> list[int]:
    out, i = [], text.find(needle)
    while i >= 0:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def _typed(path: str) -> bool:
    return (
        path.startswith(_TYPED_PATH_PREFIXES)
        or path.endswith(_TYPED_PATH_SUFFIXES)
        or any(m in path for m in _TYPED_PATH_INFIXES)
    )


def quote_in_vault(quote: str | None, units: Any, *, exclude: Any = ()) -> str | None:
    """THE Fact a vault quote must pass to BACK a claim (ADR-100 cl. 3; ADR-066:
    one predicate for this question). Returns the path of the unit the quote
    resolves in, or ``None``.

    Language-neutral by construction — a verbatim comparison under the shared
    :func:`normalize_citation` fold, never a translation or a synonym list:

    (a) the quote resolves inside ONE unit (``units``: objects with ``.path``
        and ``.text`` — the Oracle's ``EvidenceUnit``s of the CLAIMABLE vault,
        i.e. built from ``exclude_unconfirmed``; denial statements and
        ``metadata`` are not units, so neither can back anything);
    (b) the match is token-bounded at a Latin-script edge, so "SQ" never
        resolves inside "MySQL" (scripts without spaces have no such edge);
    (c) the quote is substantial: the whole unit, a whole word or more of a
        typed-value unit (skill, language, technology, certification, role,
        organisation), or at least three words / six CJK characters. A
        one-word fragment of a sentence shows nothing on its own.

    What it does NOT decide, by design: whether the span SUPPORTS the claim.
    That is a judgement (ADR-062 cl. 1) and belongs to the classifier that
    cited it and to the reviewers, who see the quote (ADR-100 cl. 1/5).
    Units that only say where or when (``personal_info``, date lines) never
    back a requirement, and no match may overlap an ``exclude`` span (the
    candidate's own "not reached yet" lines, D-12: an aim is not a holding,
    wherever else the profile repeats its words).
    """
    if not isinstance(quote, str) or not quote.strip():
        return None
    q = normalize_citation(quote)
    if not q:
        return None
    long_enough = len(q.split()) >= _MIN_WORDS or sum(map(_is_cjk, q)) >= _MIN_CJK_CHARS
    excluded = [x for x in (normalize_citation(e) for e in exclude or () if isinstance(e, str)) if x]
    for unit in units or ():
        path = getattr(unit, "path", "") or ""
        if any(m in path for m in _NON_EVIDENCE_PATH_MARKERS):
            continue
        u = normalize_citation(getattr(unit, "text", "") or "")
        blocked = [(i, i + len(x)) for x in excluded for i in _occurrences(u, x)]
        if q == u and not blocked:
            return path
        start = u.find(q)
        while start >= 0:
            end = start + len(q)
            if any(a < end and start < b for a, b in blocked):
                start = u.find(q, start + 1)
                continue
            bounded = not (start > 0 and _latin_alnum(u[start - 1]) and _latin_alnum(q[0])) and not (
                end < len(u) and _latin_alnum(u[end]) and _latin_alnum(q[-1])
            )
            if bounded and (long_enough or _typed(path)):
                return path
            start = u.find(q, start + 1)
    return None

