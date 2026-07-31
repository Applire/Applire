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

"""ADR-060 outcome-critic judgement prompts — one engine, two mounts (#322,
third amendment 2026-07-31).

**The model reads the assembled document(s).** The 2026-07-30 version of this
module sent the model ONLY a pre-computed list of candidate concepts, never
the documents, documenting it as "a stricter reading of clause 7". That
narrowing was retired on evidence (SF-CRITIC.9): the blind hiring panel found
true asymmetries — achievement figures, scope qualifiers — whose shape no
candidate enumeration anticipated, because an enumeration of "what might be
incoherent" is a judgement wearing a fact's clothing (ADR-062 clause 1).
Clause 7's own words always specified "the drafted document(s)" as inputs.

**What bounds the widened judgement is the citation check** (services/
outcome_critic.py): every finding must quote the span(s) it rests on,
verbatim, and code verifies each quote against the named document under
normalisation before an advisory is built. A finding is only ever surfaced on
spans that are provably in the documents — the model does the semantic work;
code checks the citation (the ADR-061 clause 2 discipline).

The deterministic presence facts (claimable ledger concepts that are
letter-only or letter-richer) ride along as ANCHORS — known-suspicious spots
the model must not miss — no longer as the input boundary.
"""

from typing import Any

SYSTEM_PROMPT = (
    "You are Applire's outcome critic (ADR-060). You read the candidate's "
    "FINISHED, assembled application document(s) exactly as a recruiter will, "
    "and judge coherence — nothing else.\n\n"
    "PASS A (CV only): does the CV tell one story? Flag ONLY "
    "internal_inconsistency: a summary/profile claim that is broader than "
    "what its own detail bullets substantiate, or two statements in the same "
    "document that contradict each other.\n\n"
    "PASS B (CV + cover letter): do the two documents tell one story to "
    "someone who cross-reads them? Flag ONLY these kinds:\n"
    "- letter_only: the letter asserts a fact about the candidate's history "
    "the CV never mentions (to a cross-reader it looks invented, even when "
    "true).\n"
    "- letter_richer: both documents mention a concept, but only the letter "
    "carries the depth (a duration, a figure, a scope) — the CV does not "
    "substantiate it.\n"
    "- numeric_inconsistency: the documents state different figures for the "
    "same quantity (years of experience, team size, a percentage).\n\n"
    "STRICT RULES:\n"
    "1. EVERY finding must include the exact, verbatim span(s) from the "
    "document(s) it rests on — copied character-for-character, no "
    "paraphrase, no added or removed words. A finding whose quote is not "
    "literally in the document will be discarded by the system.\n"
    "2. Judge coherence only. Wording, tone, style, paragraph order and "
    "rephrasing at the same depth are NOT findings. Whether a claim is TRUE "
    "is not your question (a separate audit answers it).\n"
    "3. Never propose new content, never soften anything, never rewrite. "
    "You return findings, not fixes.\n"
    "4. When genuinely unsure, do not surface the finding: a missed advisory "
    "costs little; a wrong one costs the candidate's trust in every other "
    "advisory.\n"
    "5. Return JSON only, exactly the shape requested."
)

_RESPONSE_SHAPE = (
    'Return JSON only, exactly this shape: {"findings": [{"kind": '
    '"letter_only"|"letter_richer"|"numeric_inconsistency"|'
    '"internal_inconsistency", "concept": "<2-5 word neutral topic label>", '
    '"cv_quote": "<verbatim span from the CV, or null>", '
    '"cv_detail_quote": "<second verbatim CV span for '
    'internal_inconsistency, or null>", '
    '"letter_quote": "<verbatim span from the cover letter, or null>", '
    '"worth_surfacing": true|false}, ...]}. '
    "An empty findings list is a valid answer."
)


def _document_block(label: str, units: list[str]) -> str:
    """Render one document's units (bullets/paragraphs/entries) as a plain
    numbered block. Numbering is presentation only — quotes must come from
    the unit TEXT, not the numbers."""
    lines = [f"=== {label} ==="]
    for i, unit in enumerate(units, 1):
        lines.append(f"[{i}] {unit}")
    return "\n".join(lines)


def build_pass_a_prompt(
    cv_units: list[str],
    job_role_title: str | None,
    jd_excerpt: str | None,
) -> str:
    """Pass A — the assembled CV, judged alone (single-document coherence)."""
    return "\n\n".join(
        [
            "PASS A — single-document coherence of the assembled CV.",
            f"Target role: {job_role_title or 'unspecified'}",
            "Job description excerpt (context only — do not judge JD "
            "coverage here, a separate check owns that):\n"
            + (jd_excerpt or "(none)"),
            _document_block("CV (assembled, as delivered)", cv_units),
            "Only internal_inconsistency findings are valid on this pass; "
            "cv_quote holds the broader/contradicting span and "
            "cv_detail_quote the span it overreaches. letter_quote must be "
            "null.",
            _RESPONSE_SHAPE,
        ]
    )


def build_pass_b_prompt(
    cv_units: list[str],
    letter_units: list[str],
    anchors: list[dict[str, Any]],
    job_role_title: str | None,
    jd_excerpt: str | None,
) -> str:
    """Pass B — the assembled CV + cover letter pair (cross-document
    coherence). ``anchors`` are the deterministic presence facts: claimable
    ledger concepts the fact layer already knows are letter-only or
    letter-richer. They are known-suspicious spots, not the finding universe.
    """
    anchor_lines: list[str] = []
    if anchors:
        anchor_lines.append(
            "Deterministically pre-computed suspicious concepts (verify each "
            "— and still read the full documents; real findings routinely "
            "fall OUTSIDE this list):"
        )
        for a in anchors:
            anchor_lines.append(
                f"- concept: {a['concept']!r}\n"
                f"  CV state: {a['cv_state']!r}\n"
                f"  Letter state: {a['letter_state']!r}"
            )
    return "\n\n".join(
        [
            "PASS B — cross-document coherence of the assembled CV and "
            "cover letter.",
            f"Target role: {job_role_title or 'unspecified'}",
            "Job description excerpt (context only — do not judge JD "
            "coverage here, a separate check owns that):\n"
            + (jd_excerpt or "(none)"),
            _document_block("CV (assembled, as delivered)", cv_units),
            _document_block("COVER LETTER (as delivered)", letter_units),
            *(["\n".join(anchor_lines)] if anchor_lines else []),
            "internal_inconsistency is not valid on this pass — judge the "
            "pair, not one document against itself.",
            _RESPONSE_SHAPE,
        ]
    )
