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

"""Segmented CV/profile extraction (US195, ADR-047 / E036).

A single full-profile extraction is a *large-output* LLM call: on a capped model
(e.g. mistral-medium-3-5 stopping near ~8k regardless of a 16384 request) a dense CV
truncates mid-JSON → ``LLMTruncatedError`` → the CV was silently dropped behind an
optimistic "complete" UI. ``extract_with_fallback`` keeps the system cap-safe the same
way segmented CV *generation* does (US189):

  * known small cap  → segment upfront (skip the doomed single call)
  * unknown cap       → try the single call, fall back to segmenting on truncation/timeout

``extract_profile_segmented`` reads the source section by section so no single call
needs a large output: an outline of position headers, one bounded detail call per
position, and one bounded core call for everything else — then assembles the full
MasterProfileData dict (cv_extraction.py defines that schema).
"""

import logging
from typing import Any

from applire.constants import CV_EXTRACTION_MAX_TOKENS, SEGMENT_MAX_TOKENS
from applire.exceptions import LLMTimeoutError, LLMTruncatedError
from applire.prompts.cv_extraction_segmented import (
    EXTRACTION_CORE_SYSTEM_PROMPT,
    EXTRACTION_DETAIL_SYSTEM_PROMPT,
    EXTRACTION_OUTLINE_SYSTEM_PROMPT,
    build_extraction_core_prompt,
    build_extraction_detail_prompt,
    build_extraction_outline_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.capabilities import resolve_effective_output_cap

logger = logging.getLogger(__name__)

# Keys the core pass owns; work_experience is assembled from outline + per-role detail.
_DETAIL_KEYS = ("responsibilities", "achievements", "technologies")


async def extract_profile_segmented(raw_text: str, provider: LLMProvider) -> dict[str, Any]:
    """Extract a full master-profile dict from raw CV/LinkedIn text in bounded segments.

    No single ``aparse_json`` call requests more than ``SEGMENT_MAX_TOKENS`` of output,
    so a capped model never truncates mid-document. Returns a dict ready for
    ``MasterProfileData.model_validate``.
    """
    budget = SEGMENT_MAX_TOKENS

    outline = await provider.aparse_json(
        build_extraction_outline_prompt(raw_text),
        system=EXTRACTION_OUTLINE_SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=budget,
    )
    positions: list[dict] = list(outline.get("work_experience") or [])

    work_entries: list[dict] = []
    for position in positions:
        detail = await provider.aparse_json(
            build_extraction_detail_prompt(raw_text, position),
            system=EXTRACTION_DETAIL_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=budget,
        )
        entry = dict(position)
        for key in _DETAIL_KEYS:
            entry[key] = list(detail.get(key) or [])
        work_entries.append(entry)

    core = await provider.aparse_json(
        build_extraction_core_prompt(raw_text),
        system=EXTRACTION_CORE_SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=budget,
    )

    # Assemble: core owns everything except work_experience, which we overlay from the
    # outline + per-role detail. dict(core) preserves any extra keys the model emitted.
    data = dict(core)
    data["work_experience"] = work_entries
    return data


async def _should_segment_extraction_upfront() -> bool:
    """True when the model's known output cap sits below the single-call extraction
    ceiling — the full profile won't fit, so segment straight away (ADR-047). An unknown
    cap (0) keeps the single call as the happy path; the reactive fallback covers it."""
    cap = await resolve_effective_output_cap()
    return 0 < cap < CV_EXTRACTION_MAX_TOKENS


async def extract_with_fallback(
    raw_text: str,
    provider: LLMProvider,
    *,
    system: str,
    user_prompt: str,
) -> dict[str, Any]:
    """Produce the extracted profile dict: single call on the fast path, segmented as the
    fallback (ADR-047). On a known-small cap, segment upfront; otherwise try the single
    large call and switch to segmented on truncation/timeout — so a dense CV is never
    silently dropped. The returned dict is fed to the same review/refine layer as before.

    ``system`` / ``user_prompt`` are the monolithic extraction prompts the caller would
    otherwise use (JD-aware or generic); the segmented path is JD-agnostic by design —
    faithful section-by-section extraction does not depend on the JD (the JD only
    re-weights emphasis, which the later tailoring step applies anyway).
    """
    if await _should_segment_extraction_upfront():
        return await extract_profile_segmented(raw_text, provider)
    try:
        return await provider.aparse_json(
            user_prompt,
            system=system,
            temperature=0.1,
            max_tokens=CV_EXTRACTION_MAX_TOKENS,
        )
    except (LLMTruncatedError, LLMTimeoutError):
        logger.warning(
            "single-call extraction hit the output cap/timeout; switching to segmented "
            "extraction instead of dropping the CV (ADR-047)"
        )
        return await extract_profile_segmented(raw_text, provider)
