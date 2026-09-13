# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Truthfulness Oracle prompts moved out of ``services/oracle/`` (M5.7.1
inline-prompt move) — placement changes only; every rendered string stays
byte-identical to what shipped before the move (pinned by the corresponding
unit tests).

  * :data:`SEGMENT_PROSE_SYSTEM_PROMPT` / :func:`build_segment_prose_prompt`
    — ``services/oracle/extract.py``'s ``_segment_prose_llm`` (ADR-047
    bounded-output-by-contract prose-segmentation fallback, US248
    audit-any-document). Previously the standing instruction and the payload
    both travelled as ONE ``prompt=`` argument
    (``_SEGMENT_PROMPT.format(text=text)``) — the same shape the #404
    retrofit fixed for the entailment call in ``services/oracle/audit.py``
    (see that call's own constants below), fixed here too: the instruction
    is now ``system=`` and only the text to segment is ``prompt=``. Text
    unchanged, placement only.
"""

SEGMENT_PROSE_SYSTEM_PROMPT = (
    "Split the following resume/cover-letter prose into its individual factual "
    "claims (one short statement each). Return STRICT JSON: "
    '{"claims": ["...", "..."]}. Do not rephrase, do not add or drop content — '
    "segment only."
)


def build_segment_prose_prompt(text: str) -> str:
    """Payload only — the standing instruction travels as
    :data:`SEGMENT_PROSE_SYSTEM_PROMPT` (``system=``)."""
    return f"TEXT:\n{text}"
