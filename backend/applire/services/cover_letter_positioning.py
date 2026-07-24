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

"""Deterministic, no-LLM positioning inputs for the cover-letter prompt (E048/US264,
ADR-057 amended 2026-07-24).

A blind hiring panel rejected an otherwise-honest application because the letter
never engaged the employer concretely, never argued the candidate's own transfer
story for the one true gap in the JD (even though the argument sat in the vault as
interview testimony), and never addressed an obvious concurrent-roles/availability
question. ADR-058 exception (a) scopes the fix to PROMPT-INPUT THREADING only —
no new LLM chain, no new pass. This module supplies the deterministic (keyword/
count-based) inputs the cover-letter prompt builder threads in; it invents nothing
and makes no LLM call itself. Mirrors the no-LLM style of
:mod:`applire.services.cv_gap_mapper`.
"""

import re

_TOKEN_RE = re.compile(r"\b[a-zA-ZÀ-ÿ0-9.#+\-]{2,}\b")

# Keyword vocabulary for the availability/concurrent-commitment slot (DE + EN).
# Matched the same way a gap label is matched against a story (token overlap) —
# no invented testimony: a story only counts as availability testimony when its
# OWN text uses one of these terms.
_AVAILABILITY_KEYWORDS = {
    "availability", "available", "notice", "noticeperiod", "kundigungsfrist",
    "kündigungsfrist", "verfugbar", "verfügbar", "eintrittstermin", "concurrent",
    "parallel", "gleichzeitig", "nebenbei", "commitment", "commitments",
    "juggle", "juggling", "moonlight", "moonlighting", "freelance", "nebenjob",
}


def _tokenise(text: str) -> set[str]:
    """Lowercase word tokens, 2+ chars — mirrors cv_gap_mapper._tokenise."""
    return {w.lower() for w in _TOKEN_RE.findall(text or "")}


def _story_text(story: dict) -> str:
    """Concatenate a signature story's own prose fields (never the JD/gap label —
    only the candidate's own testimony can ground a claim)."""
    parts = [
        story.get("title") or "",
        story.get("challenge") or "",
        story.get("mechanism") or "",
        story.get("outcome") or "",
        story.get("benchmark") or "",
    ]
    return " ".join(p for p in parts if p)


def detect_concurrent_roles(work_experience: list[dict]) -> bool:
    """True iff >=2 entries have an OPEN end date (a current/ongoing role).

    An entry counts as open-ended when ``is_current`` is explicitly True, or
    when ``is_current`` is unset (None) AND ``end_date`` is empty/absent — the
    tri-state convention #155 already uses elsewhere (``is_current`` False
    always means "known ended", regardless of a blank end_date).
    """
    open_count = 0
    for entry in work_experience or []:
        if not isinstance(entry, dict):
            continue
        is_current = entry.get("is_current")
        end_date = entry.get("end_date")
        if is_current is True or (is_current is None and not end_date):
            open_count += 1
    return open_count >= 2


def find_gap_testimony(
    category_c_gaps: list[str], signature_stories: list[dict]
) -> dict | None:
    """Return the first category-C gap with a matching signature story, or None.

    Deterministic keyword-overlap match (mirrors
    :func:`applire.services.cv_gap_mapper.map_gaps_to_sections`): for each gap
    label IN ORDER (category_c is already severity-ordered by the gap analysis),
    score every story by token overlap between the gap label and the story's OWN
    prose; the first gap with a positive-scoring story wins (deterministic
    first-match, same tie-break philosophy as the CV gap mapper). Returns
    ``{"gap": <gap label>, "story": <story dict>}`` — the caller threads the
    story's own text VERBATIM into the prompt; nothing is invented here.
    """
    stories = [s for s in (signature_stories or []) if isinstance(s, dict)]
    if not stories:
        return None
    for gap in category_c_gaps or []:
        gap_tokens = _tokenise(gap)
        if not gap_tokens:
            continue
        best_story: dict | None = None
        best_score = 0
        for story in stories:
            score = len(gap_tokens & _tokenise(_story_text(story)))
            if score > best_score:
                best_score = score
                best_story = story
        if best_story is not None:
            return {"gap": gap, "story": best_story}
    return None


def find_availability_testimony(
    signature_stories: list[dict], enrichment_history: list[dict]
) -> str | None:
    """Search the vault for the candidate's OWN testimony about availability /
    concurrent commitments. Returns the matched text VERBATIM, or None.

    Two sources, both deterministic keyword matches against the vocabulary in
    ``_AVAILABILITY_KEYWORDS`` — never a guess:
    1. Signature stories (ADR-055) whose own prose mentions one of the terms.
    2. Enrichment-history field changes (interview/agent_interview turns) whose
       rationale or recorded value mentions one of the terms.
    First match wins (stories checked first — they are the richer, full-prose
    unit); returns None (no claim made) when nothing matches.
    """
    for story in signature_stories or []:
        if not isinstance(story, dict):
            continue
        text = _story_text(story)
        if _tokenise(text) & _AVAILABILITY_KEYWORDS:
            return text

    for record in enrichment_history or []:
        if not isinstance(record, dict):
            continue
        for change in record.get("changes") or []:
            if not isinstance(change, dict):
                continue
            rationale = change.get("rationale") or ""
            new_value = change.get("new_value")
            value_text = new_value if isinstance(new_value, str) else ""
            text = f"{rationale} {value_text}".strip()
            if text and (_tokenise(text) & _AVAILABILITY_KEYWORDS):
                return text
    return None
