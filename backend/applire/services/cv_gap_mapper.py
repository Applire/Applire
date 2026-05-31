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

"""Keyword-based gap-to-section mapper (no LLM, ~5ms).

For each gap label, tokenise it and count how many of its tokens appear in each
section's content. A gap is assigned to the SINGLE section with the strongest
token overlap; ties are broken by section order (first wins), which is
deterministic because the input dict preserves insertion order. Assigning each
gap to exactly one section keeps the CV refinement panel from counting and
rendering the same gap multiple times. Gaps with zero matches in any section
fall into the __general__ bucket.
"""
import re


def _tokenise(text: str) -> set[str]:
    """Lowercase word tokens, 2+ chars."""
    return {w for w in re.findall(r"\b[a-zA-ZÀ-ÿ0-9.#+\-]{2,}\b", text.lower())}


def map_gaps_to_sections(
    gaps: list[str],
    sections: dict[str, str],  # section_id -> section content
) -> dict[str, list[str]]:
    """Return a dict mapping section_id -> [gap_labels] assigned to that section.

    Unmatched gaps are placed under the key "__general__".
    """
    if not gaps:
        return {}

    # Pre-tokenise section contents once
    section_tokens: dict[str, set[str]] = {
        sid: _tokenise(content) for sid, content in sections.items()
    }

    result: dict[str, list[str]] = {}

    for gap in gaps:
        gap_tokens = _tokenise(gap)
        if not gap_tokens:
            result.setdefault("__general__", []).append(gap)
            continue

        # Pick the single best-matching section. Strictly-greater comparison
        # means the first section wins on a tie (dict preserves insertion order).
        best_sid: str | None = None
        best_score = 0
        for sid, tokens in section_tokens.items():
            score = len(gap_tokens & tokens)
            if score > best_score:
                best_score = score
                best_sid = sid

        target = best_sid if best_sid is not None else "__general__"
        result.setdefault(target, []).append(gap)

    return result
