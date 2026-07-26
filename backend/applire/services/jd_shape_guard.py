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

"""Deterministic JD-skill shape guard (Wave-6 Task 3).

The extraction/reviewer/corrector prompts (prompts/job_analysis.py,
prompts/review_job_analysis.py) now state that required_skills /
nice_to_have_skills / keywords are a controlled vocabulary of short CONCEPT
TERMS (typically 1-4 words), never sentences or verbatim quotations of the
posting. Prompt wording is necessary but not sufficient — a model under review
pressure will still occasionally emit a sentence-shaped entry. This module is
the deterministic belt-and-braces backstop, applied once the JD-analysis
review loop has settled (see services/job.py::analyze_jd).

Design decision — FLAG vs REPAIR (read before changing the thresholds):

A sentence-shaped entry is REPAIRED (dropped) ONLY when a concept-shaped entry
ALREADY PRESENT elsewhere in the same list fully covers it — i.e. every
meaningful token of the concept term appears in the sentence. In that case the
sentence is provably redundant (the concept it names is already represented in
the correct shape), so dropping it loses no information and cleans up the
ledger input.

Every other sentence-shaped entry is left exactly as-is and only logged. This
is deliberately conservative for two reasons documented in the wave-6 task:

  1. Dropping a sentence with no concept equivalent would silently
     UNDER-EXTRACT a requirement the posting actually states — the guard has
     no way to know whether the sentence is redundant or the only place that
     requirement appears.
  2. Splitting or rewriting a sentence into guessed concept terms would
     FABRICATE requirements the JD never singled out as distinct concepts —
     worse than the bug being fixed, since it manufactures new claimable
     ledger entries out of nothing (the ledger is the arbiter of downstream
     truthfulness; over-extraction poisons it in the opposite direction from
     under-extraction).

So: repair only the provably-redundant case; flag-and-log everything else.
Nothing is ever invented — the guard can only ever remove list entries, never
add or rewrite one.
"""

import logging
import re

logger = logging.getLogger(__name__)

# The three JD-analysis fields this guard governs — all three feed
# build_keyword_ledger() identically (services/keyword_ledger.py).
SHAPE_GUARDED_FIELDS = ("required_skills", "nice_to_have_skills", "keywords")

# Concept terms are "typically 1-4 words" per the prompt contract; allow some
# slack before treating an entry as sentence-shaped, to avoid false positives
# on legitimate longer technical concepts (e.g. "Distributed systems design
# experience"). Entries at or below this word count are never touched.
_CONCEPT_WORD_LIMIT = 6

# Sentence-ending punctuation is a strong signal on its own regardless of
# length — a concept term is never itself punctuated as a full sentence.
_SENTENCE_ENDINGS = (".", ";")

_STOPWORDS = {
    "a", "an", "and", "the", "with", "in", "of", "to", "for", "or", "on",
    "is", "are", "this", "that", "by", "from", "at", "as", "using",
}


def _word_count(text: str) -> int:
    return len(text.split())


def _is_concept_shaped(text: str) -> bool:
    """True when `text` looks like a short concept term rather than a sentence."""
    stripped = text.strip()
    if stripped.endswith(_SENTENCE_ENDINGS):
        return False
    return _word_count(stripped) <= _CONCEPT_WORD_LIMIT


def _meaningful_tokens(text: str) -> list[str]:
    """Lowercased word tokens with stopwords removed."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOPWORDS]


def _concept_fully_covered_by(concept_tokens: list[str], sentence_tokens: set[str]) -> bool:
    """True when every meaningful token of a concept term is present in the sentence.

    This is a subset test, not a similarity score — it deliberately requires
    ALL of the concept's tokens to appear, so a partial/coincidental overlap
    (e.g. sharing one generic word) never triggers a drop.
    """
    if not concept_tokens:
        return False
    return all(tok in sentence_tokens for tok in concept_tokens)


def normalize_skill_shape(entries: list | None) -> tuple[list | None, list[str]]:
    """Normalise one required_skills/nice_to_have_skills/keywords list.

    Returns (normalized_entries, notes). `notes` are human-readable log lines
    for every entry that was either dropped (repaired) or left in place because
    it was ambiguous (flagged) — never for entries that were already
    concept-shaped.

    Malformed elements (None, non-string, blank) are passed through untouched;
    shape judgement only applies to non-empty strings. This function never
    grows the input list — it can only drop entries, never add or rewrite one.
    """
    if entries is None:
        return None, []
    if not entries:
        return list(entries), []

    notes: list[str] = []

    # Concept-shaped entries already in the list are the only thing a
    # sentence-shaped entry can be judged redundant against.
    concept_shaped_tokens = [
        _meaningful_tokens(e)
        for e in entries
        if isinstance(e, str) and e.strip() and _is_concept_shaped(e)
    ]

    kept: list = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            # Not this guard's job — malformed/empty entries pass through.
            kept.append(entry)
            continue
        if _is_concept_shaped(entry):
            kept.append(entry)
            continue

        sentence_tokens = set(_meaningful_tokens(entry))
        covered = any(
            _concept_fully_covered_by(ctoks, sentence_tokens)
            for ctoks in concept_shaped_tokens
        )
        if covered:
            notes.append(f"dropped sentence-shaped duplicate entry (concept already present): {entry!r}")
            continue

        notes.append(f"sentence-shaped entry left in place (ambiguous, no concept equivalent found): {entry!r}")
        kept.append(entry)

    return kept, notes


def apply_jd_shape_guard(data: dict) -> dict:
    """Apply the shape guard to the settled JD-analysis output, in place.

    Only touches keys that are present AND hold a list value — a missing key
    stays missing, and a None value stays None (services/job.py distinguishes
    "field absent" from "field explicitly empty" downstream). Returns `data`
    for convenient chaining; non-dict input is returned unchanged.
    """
    if not isinstance(data, dict):
        return data

    for field in SHAPE_GUARDED_FIELDS:
        if field not in data:
            continue
        entries = data[field]
        if entries is None:
            continue
        if not isinstance(entries, list):
            logger.warning(
                "jd_shape_guard[%s]: expected a list, got %s — left untouched",
                field,
                type(entries).__name__,
            )
            continue

        normalized, notes = normalize_skill_shape(entries)
        for note in notes:
            logger.warning("jd_shape_guard[%s]: %s", field, note)
        data[field] = normalized

    return data
