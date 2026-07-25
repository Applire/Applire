# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""#254 — deterministic figure-attribution guard for cover-letter prose.

Live bug (2026-07-24): a generated letter stated "mentoring teams of 5+" — a
headcount the candidate explicitly declined to give. The figure was not
invented from nothing: the ADR-021 review loop's CORRECTOR call, which sees
the WHOLE profile in its prompt, borrowed "5" from an entirely unrelated
vault fact ("Lead a team of five tech leads and system owners", a different
current role) and attached it to the mentoring claim. The writer's own draft
never contained it — only the corrector output does, so ANY guard that runs
once on the initial draft misses the bug. This module is called on the FINAL
settled output of every ``review_and_refine`` pass in
``services/cover_letter.py`` (writer loop AND the condense/refine loop),
never mid-loop, so a corrector-introduced figure can never slip through.

Same defect class as #196 (role-attribution) and #243-adjacent/#248 (letter
figure/claim ownership), now caught deterministically on the GENERATION path
rather than only detected post-hoc by the Oracle (``services/oracle/audit.py``
— belt and suspenders, both wanted). Reuses that package's OWN ownership
machinery end to end and invents no parallel notion of ownership:

* :func:`applire.services.oracle.matchers.build_vault_index` for
  ``EvidenceUnit.owner_ids`` (the US187/#237 nesting-aware ownership model).
* :func:`applire.services.oracle.extract._find_employer_anchor` /
  ``_match_ids`` / ``_employer_anchor_candidates`` /
  ``letter_named_experience_ids`` for the SAME per-clause / per-sentence /
  whole-letter attribution signals ``extract_claims_from_letter`` uses
  (#237/#248) — a clause anchored to exactly one named employer/project may
  only be substantiated by that position's own evidence (or role-agnostic
  evidence); an unanchored clause falls back to the sentence's loosely-named
  owners, then the letter-wide single-employer escape, exactly mirroring
  ``oracle.audit._unattributable_evidence_flag``'s escapes (a) and (b).

Scope deliberately narrower than a "is this figure real" check: a figure with
NO vault match anywhere is left untouched here — that is the Oracle's
"unbacked" verdict (``services/oracle/matchers/grounding.match_figures``),
not this guard's job, and treating "no literal digits found" as fabricated
would wrongly strip legitimate DERIVED claims a self-hoster never typed as a
literal number anywhere in the vault (e.g. "over 20 years of experience",
computed from date spans, not stored as the digit string "20" — regression-
tested below). This guard only fires on the#254 shape: a figure that DOES
match a vault fact, but every matching fact belongs to a position/story the
surrounding clause is not about.

Detection floor is DELIBERATELY narrower than the Oracle's own
``oracle.matchers.figures.extract_figures`` (which excludes single digits and
spelled-out numbers by design, US244 — see that module's docstring): the
pinned bug is a bare single-digit headcount ("5+"), so this guard also parses
"N+" growth-qualifier forms, single/multi-digit plain numbers, and spelled-out
EN/DE number words (prior art: ``services/profile/reconcile/stance.py``'s
``_spelled_figures`` — the SAME word tables are reused here, position-aware,
so a vault fact phrased as "five" can be recognised as backing (or NOT
backing) a letter clause that renders it as "5+"). Years are exempt entirely
(consumed but never emitted as a figure) — same rationale as #196: date spans
and "since 20XX" phrasing are tenure-ambient and legitimately repeat across
positions.

A figure that IS vault-grounded but only under a foreign owner is DROPPED
(blanked from the clause) rather than the whole claim rewritten by another
LLM pass — deterministic, no new LLM call, mirroring the #207/US261
word-boundary blanking idiom. Every drop is logged (house style forbids
silent truncation) with the offending figure, its foreign owner(s), and the
clause it was removed from.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from typing import Any

from applire.services.oracle.extract import (
    _CLAUSE_BOUNDARY_RE,
    _employer_anchor_candidates,
    _find_employer_anchor,
    _match_ids,
    letter_named_experience_ids,
    split_sentences,
)
from applire.services.oracle.matchers import EvidenceUnit, build_vault_index
from applire.services.profile.reconcile.stance import (
    _DE_COMPOUND_UNITS,
    _DE_SCALE_RE,
    _DE_TENS,
    _DE_UND_RE,
    _EN_TENS,
    _EN_UNITS,
    _SCALES,
    _SMALL_WORDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LetterFigure:
    kind: str  # "percent" | "number"
    value: str  # canonical numeric string, comparable across digit/spelled forms
    raw: str  # verbatim matched substring (for logging/replacement)
    start: int
    end: int


# ── figure extraction (digits, "N+", percent, years-exempt) ─────────────────
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_PERCENT_RE = re.compile(r"[~≈]?\s*(\d+(?:[.,]\d+)?)\s*%")
_PLUS_RE = re.compile(r"\b(\d+)\+")
_PLAIN_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Unicode punctuation normalized before matching (the U+2019 lesson) — the
# only punctuation that could otherwise sit between a spelled number and a
# following scale word or interfere with word-boundary matching.
_APOSTROPHE_CHARS = "’ʼ‘‛´`"


def _fold_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    return out


def _canon(raw: str) -> str:
    """Canonical numeric string — mirrors
    ``oracle.matchers.figures._canonical_number``: '2.000'/'2,000' -> '2000';
    '12,5' -> '12.5'."""
    s = raw.strip()
    if _THOUSANDS_RE.match(s):
        return re.sub(r"[.,]", "", s)
    s = s.replace(",", ".")
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _digit_figures(text: str) -> list[LetterFigure]:
    figures: list[LetterFigure] = []
    consumed: list[tuple[int, int]] = []

    def _free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in consumed)

    # Years are consumed (protected from the plain-number pass below) but
    # never emitted — exempt entirely, same rationale as #196.
    for m in _YEAR_RE.finditer(text):
        consumed.append(m.span())

    for m in _PERCENT_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("percent", _canon(m.group(1)), m.group(0), *m.span()))
        consumed.append(m.span())

    for m in _PLUS_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("number", _canon(m.group(1)), m.group(0), *m.span()))
        consumed.append(m.span())

    for m in _PLAIN_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("number", _canon(m.group(0)), m.group(0), *m.span()))
        consumed.append(m.span())

    return figures


def _spelled_figures(text: str) -> list[LetterFigure]:
    """Position-aware EN/DE spelled-number matches (#207 prior art, made
    span-aware here so a match can be blanked in place)."""
    matches: list[LetterFigure] = []
    toks = list(_WORD_RE.finditer(text))
    lowered = [m.group(0).lower() for m in toks]
    n = len(toks)
    i = 0
    while i < n:
        tok = lowered[i]
        start, end = toks[i].span()
        nxt = lowered[i + 1] if i + 1 < n else ""
        nxt2 = lowered[i + 2] if i + 2 < n else ""

        small = _SMALL_WORDS.get(tok)
        if small is not None:
            value = small
            end_pos = end
            consumed_to = i
            if tok in _EN_TENS and nxt in _EN_UNITS:
                value = _EN_TENS[tok] + _EN_UNITS[nxt]
                end_pos = toks[i + 1].end()
                consumed_to = i + 1
                if _SCALES.get(nxt2) is not None:
                    value *= _SCALES[nxt2]
                    end_pos = toks[i + 2].end()
                    consumed_to = i + 2
            elif _SCALES.get(nxt) is not None:
                value *= _SCALES[nxt]
                end_pos = toks[i + 1].end()
                consumed_to = i + 1
            matches.append(
                LetterFigure("number", str(value), text[start:end_pos], start, end_pos)
            )
            i = consumed_to + 1
            continue

        if tok in _SCALES:
            matches.append(
                LetterFigure("number", str(_SCALES[tok]), text[start:end], start, end)
            )
            i += 1
            continue

        m = _DE_UND_RE.match(tok)
        if m:  # "fünfundvierzig", "fünfundzwanzigtausend"
            value = _DE_COMPOUND_UNITS[m.group(1)] + _DE_TENS[m.group(2)]
            if m.group(3):
                value *= _SCALES[m.group(3)]
            matches.append(LetterFigure("number", str(value), text[start:end], start, end))
            i += 1
            continue

        m2 = _DE_SCALE_RE.match(tok)
        if m2:  # "zweihundert", "zweitausend"
            value = _DE_COMPOUND_UNITS.get(m2.group(1) or "", 1) * _SCALES[m2.group(2)]
            matches.append(LetterFigure("number", str(value), text[start:end], start, end))
            i += 1
            continue

        i += 1
    return matches


def _extract_letter_figures(text: str) -> list[LetterFigure]:
    """Every droppable figure in ``text``: digits, "N+" growth forms, percent,
    and spelled-out EN/DE numbers. Years are exempt (never returned)."""
    folded = _fold_punct(text)
    return _digit_figures(folded) + _spelled_figures(folded)


# ── the vault side: an independent (kind, value) -> owners index ────────────
# Deliberately NOT ``VaultIndex.figure_map`` (oracle.matchers.vault) — that
# index is built from ``oracle.matchers.figures.extract_figures``, which
# excludes single digits and spelled numbers by design (US244). The vault fact
# behind #254 ("team of five") is a SPELLED number, so it would never appear
# in the oracle figure_map at all; this guard needs its own symmetric
# extraction (the SAME ``_extract_letter_figures`` above) run over vault unit
# text too, so "five" in the vault and "5+" in the letter canonicalize to the
# same comparable value. Ownership itself (``EvidenceUnit.owner_ids``) is
# still the one shared instrument — only figure-parsing is duplicated, and
# only because the two callers need different recall floors (US244's own
# docstring explains why ITS floor is narrower).
def _vault_figure_map(units: list[EvidenceUnit]) -> dict[tuple[str, str], list[EvidenceUnit]]:
    fmap: dict[tuple[str, str], list[EvidenceUnit]] = {}
    for unit in units:
        for fig in _extract_letter_figures(unit.text):
            fmap.setdefault((fig.kind, fig.value), []).append(unit)
    return fmap


# ── per-clause attribution context (mirrors oracle.extract's escapes) ───────
def _sentence_spans(paragraph: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) for every sentence ``split_sentences`` finds,
    positioned within the ORIGINAL paragraph so the guard can blank a figure
    in place without disturbing the surrounding prose."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in split_sentences(paragraph):
        idx = paragraph.find(sentence, cursor)
        if idx == -1:
            # Cannot locate — fail open on this sentence (never corrupt the
            # paragraph over a text-location mismatch).
            continue
        spans.append((idx, idx + len(sentence), sentence))
        cursor = idx + len(sentence)
    return spans


def _clause_spans(sentence: str) -> list[tuple[int, int]]:
    """Position-aware twin of ``oracle.extract.split_clauses`` — same
    boundary regex, but keeps spans within ``sentence`` for in-place editing."""
    spans: list[tuple[int, int]] = []
    last = 0
    for m in _CLAUSE_BOUNDARY_RE.finditer(sentence):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(sentence)))
    return [(s, e) for s, e in spans if sentence[s:e].strip()]


def _allowed_owner_ids(
    clause_anchor: str | None,
    sentence_named: frozenset[str],
    letter_named_ids: frozenset[str],
) -> frozenset[str]:
    """Owners a figure in THIS clause may legitimately belong to.

    Mirrors ``oracle.audit._unattributable_evidence_flag``'s escapes: a
    strictly anchored clause (exactly one employer/project named in it, or in
    its sentence when the sentence itself is unambiguous) may only be
    substantiated by that position. An unanchored clause falls back to every
    owner loosely named anywhere in its own sentence, then — only when the
    WHOLE letter names exactly one employer/project — that one. Otherwise the
    allowed set stays empty and only role-agnostic evidence (no owners at
    all) can clear a figure here; per the issue's SAFE-action rule, genuinely
    undecidable context strips the figure rather than keeping it.
    """
    if clause_anchor is not None:
        return frozenset({clause_anchor})
    allowed = set(sentence_named)
    if len(letter_named_ids) == 1:
        allowed |= letter_named_ids
    return frozenset(allowed)


def _collapse_whitespace(text: str) -> str:
    cleaned = re.sub(r"[ \t]{2,}", " ", text)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned


def _strip_unattributable(
    text: str,
    allowed_ids: frozenset[str],
    vault_fig_map: dict[tuple[str, str], list[EvidenceUnit]],
) -> tuple[str, list[dict[str, Any]]]:
    """Blank every figure in ``text`` whose vault backing is EXCLUSIVELY
    foreign to ``allowed_ids`` (mirrors
    ``oracle.matchers.attribution.find_foreign_owner``'s "any role-agnostic or
    allowed unit clears it" rule, generalized to a set of allowed owners). A
    figure with no vault match anywhere is left untouched — not this guard's
    job (module docstring)."""
    figures = _extract_letter_figures(text)
    to_blank: list[tuple[int, int]] = []
    dropped: list[dict[str, Any]] = []
    for fig in figures:
        units = vault_fig_map.get((fig.kind, fig.value), [])
        if not units:
            continue
        compatible = any((not u.owner_ids) or (u.owner_ids & allowed_ids) for u in units)
        if compatible:
            continue
        to_blank.append((fig.start, fig.end))
        foreign_owners = sorted({o for u in units for o in u.owner_ids})
        dropped.append(
            {
                "raw": fig.raw,
                "kind": fig.kind,
                "clause": text.strip(),
                "foreign_owners": foreign_owners,
            }
        )
    if not to_blank:
        return text, []
    new_text = text
    for start, end in sorted(to_blank, reverse=True):
        new_text = new_text[:start] + new_text[end:]
    return _collapse_whitespace(new_text), dropped


def _guard_paragraph(
    paragraph: str,
    candidates: list[tuple[str, str]],
    loose_candidates: list[tuple[str, str]],
    letter_named_ids: frozenset[str],
    vault_fig_map: dict[tuple[str, str], list[EvidenceUnit]],
) -> tuple[str, list[dict[str, Any]]]:
    dropped: list[dict[str, Any]] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, sentence in _sentence_spans(paragraph):
        pieces.append(paragraph[cursor:start])
        sentence_anchor = _find_employer_anchor(sentence, candidates)
        sentence_named = _match_ids(sentence, loose_candidates)
        clause_spans = _clause_spans(sentence)
        multi = len(clause_spans) > 1

        sent_pieces: list[str] = []
        sc = 0
        for cs, ce in clause_spans:
            clause_text = sentence[cs:ce]
            clause_anchor = sentence_anchor
            if clause_anchor is None and multi:
                # #248 direction 1: the sentence was ambiguous or named no
                # employer — give this clause its own chance to anchor.
                clause_anchor = _find_employer_anchor(clause_text, candidates)
            allowed = _allowed_owner_ids(clause_anchor, sentence_named, letter_named_ids)
            new_clause, clause_dropped = _strip_unattributable(clause_text, allowed, vault_fig_map)
            sent_pieces.append(sentence[sc:cs])
            sent_pieces.append(new_clause)
            sc = ce
            dropped.extend(clause_dropped)
        sent_pieces.append(sentence[sc:])
        pieces.append("".join(sent_pieces))
        cursor = end
    pieces.append(paragraph[cursor:])
    return "".join(pieces), dropped


def guard_letter_figures(letter_data: dict[str, Any], profile: Any) -> dict[str, Any]:
    """The #254 prevention guard — run on the FINAL settled output of every
    generation attempt (writer draft AND every corrector/condense pass),
    never mid-loop.

    Returns ``letter_data`` unchanged (same object) when nothing was dropped;
    otherwise a deep copy with the offending figure(s) blanked from
    ``body.paragraphs`` and each drop logged (never silent).
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not paragraphs:
        return letter_data

    index = build_vault_index(profile)
    vault_fig_map = _vault_figure_map(index.units)
    letter_named_ids = letter_named_experience_ids(letter_data, profile)
    candidates = _employer_anchor_candidates(profile)
    loose_candidates = _employer_anchor_candidates(profile, loose=True)

    new_paragraphs: list[Any] = []
    all_dropped: list[dict[str, Any]] = []
    changed = False
    for pi, para in enumerate(paragraphs):
        if not isinstance(para, str) or not para.strip():
            new_paragraphs.append(para)
            continue
        new_para, para_dropped = _guard_paragraph(
            para, candidates, loose_candidates, letter_named_ids, vault_fig_map
        )
        if para_dropped:
            changed = True
            for d in para_dropped:
                d["paragraph_index"] = pi
            all_dropped.extend(para_dropped)
        new_paragraphs.append(new_para)

    if not changed:
        return letter_data

    for d in all_dropped:
        logger.warning(
            "letter_figure_guard (#254): dropped figure %r from cover-letter "
            "paragraph %d — backed only by evidence owned by %s, which this "
            "clause's context does not name (%r). Rewritten figure-free.",
            d["raw"], d["paragraph_index"], d["foreign_owners"], d["clause"],
        )

    result = copy.deepcopy(letter_data)
    result.setdefault("body", {})["paragraphs"] = new_paragraphs
    return result
