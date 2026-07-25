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

"""#261 — the letter's evidence-selection half of "prefer measured outcomes
over targets for the same initiative".

Sibling to ``letter_figure_guard.py`` (#254): same run-once-on-the-FINAL-
settled-output contract (writer draft AND every corrector/condense pass,
never mid-loop), same reuse of ``oracle.extract``'s employer-anchor
machinery, same "never touch prompts, never invent a parallel ownership
concept" posture. Kept as an INDEPENDENT module rather than folded into
``letter_figure_guard.py`` — that file is #254's own generation-path guard,
actively owned this wave; this is a different rule (selection preference, not
figure attribution) with a different trigger (marker phrases, not numbers).

Deliberately narrower than the CV path (``services.outcome_preference`` /
``services.cv._prefer_measured_outcomes``) in two ways:

* Anchoring is STRICT-only (``oracle.extract._find_employer_anchor``,
  ``loose=False`` default): a target sentence must name exactly ONE known
  employer/project to be eligible at all. The CV path can always fall back to
  the bullet's own ``TailoredWorkEntry.id`` (a hard slot, always well-owned);
  a letter sentence has no such structural anchor, so the ambiguity-tolerant
  loose/whole-letter escapes ``letter_figure_guard`` uses for DROPPING a
  figure are deliberately NOT reused here for INSERTING context — a wrong
  reframe would attach a result to a sentence about a position it may not
  even concern (fabrication-class risk), so an unanchored or multiply-
  anchored sentence is left alone.
* A qualifying sentence is only ever REFRAMED (the target folded in as
  explicit context, via the SAME ``outcome_preference.reframe_with_outcome``
  the CV path uses), never DROPPED — removing a whole sentence from flowing
  letter prose risks an ungrammatical hole in a way dropping a bullet-list
  item does not.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from applire.services.oracle.extract import (
    _employer_anchor_candidates,
    _find_employer_anchor,
    split_sentences,
)
from applire.services.oracle.matchers import build_vault_index
from applire.services.outcome_preference import (
    find_paired_outcome,
    is_already_framed,
    is_target_phrase,
    reframe_with_outcome,
)

logger = logging.getLogger(__name__)


def _sentence_spans(paragraph: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) for every sentence, positioned within the
    ORIGINAL paragraph so a sentence can be replaced in place without
    disturbing surrounding prose. Independent copy of
    ``letter_figure_guard._sentence_spans`` — same small technique, kept
    local rather than importing a sibling guard's private helper."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in split_sentences(paragraph):
        idx = paragraph.find(sentence, cursor)
        if idx == -1:
            continue  # cannot locate -- fail open on this sentence
        spans.append((idx, idx + len(sentence), sentence))
        cursor = idx + len(sentence)
    return spans


def _guard_paragraph(
    paragraph: str,
    candidates: list[tuple[str, str]],
    units: list[Any],
    lang: str,
) -> tuple[str, list[dict[str, Any]]]:
    # Idempotency at PARAGRAPH granularity, not per-sentence: once reframed,
    # re-splitting the paragraph on a second pass turns "target. — measured:
    # outcome." into TWO fresh sentences at that inserted period, and the
    # first ("...target.") no longer carries the separator substring itself
    # -- a per-sentence check would then re-fire and double-append. A
    # paragraph that already carries one reframe is left alone entirely
    # (a documented, minor scope trade: a second, unrelated target sentence
    # in the SAME paragraph as an already-reframed one won't be caught on a
    # later pass -- rare in practice, and safe by construction).
    if is_already_framed(paragraph):
        return paragraph, []

    pieces: list[str] = []
    cursor = 0
    reframed: list[dict[str, Any]] = []
    for start, end, sentence in _sentence_spans(paragraph):
        pieces.append(paragraph[cursor:start])
        new_sentence = sentence
        if not is_already_framed(sentence) and is_target_phrase(sentence):
            anchor = _find_employer_anchor(sentence, candidates)
            if anchor is not None:
                paired = find_paired_outcome(sentence, frozenset({anchor}), units)
                if paired is not None:
                    new_sentence = reframe_with_outcome(sentence, paired.text, lang)
                    reframed.append(
                        {"sentence": sentence, "owner": anchor, "outcome": paired.text}
                    )
        pieces.append(new_sentence)
        cursor = end
    pieces.append(paragraph[cursor:])
    return "".join(pieces), reframed


def guard_letter_outcome_preference(
    letter_data: dict[str, Any], profile: Any, lang: str = "de"
) -> dict[str, Any]:
    """#261 — surface a measured outcome over a bare target/projection for
    the same initiative, in the letter's rendered prose.

    Run on the FINAL settled output of every generation attempt (writer
    draft AND every corrector/condense pass), never mid-loop — same contract
    as :func:`applire.services.letter_figure_guard.guard_letter_figures`.

    ``lang`` (ADR-038) — the framing word follows the LETTER's own output
    language (``detected_language`` at the call site), never the UI language.

    Returns ``letter_data`` unchanged (same object) when nothing qualifies;
    otherwise a deep copy with the qualifying sentence(s) reframed in
    ``body.paragraphs``. Every reframe is logged (house style forbids silent
    rewrites).
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not paragraphs:
        return letter_data

    index = build_vault_index(profile)
    candidates = _employer_anchor_candidates(profile)
    if not candidates:
        return letter_data

    new_paragraphs: list[Any] = []
    all_reframed: list[dict[str, Any]] = []
    changed = False
    for pi, para in enumerate(paragraphs):
        if not isinstance(para, str) or not para.strip():
            new_paragraphs.append(para)
            continue
        new_para, para_reframed = _guard_paragraph(para, candidates, index.units, lang)
        if para_reframed:
            changed = True
            for r in para_reframed:
                r["paragraph_index"] = pi
            all_reframed.extend(para_reframed)
        new_paragraphs.append(new_para)

    if not changed:
        return letter_data

    for r in all_reframed:
        logger.info(
            "letter_outcome_guard (#261): surfaced measured outcome over target "
            "phrasing in paragraph %d, owner %s: %r paired with %r",
            r["paragraph_index"], r["owner"], r["sentence"], r["outcome"],
        )

    result = copy.deepcopy(letter_data)
    result.setdefault("body", {})["paragraphs"] = new_paragraphs
    return result
