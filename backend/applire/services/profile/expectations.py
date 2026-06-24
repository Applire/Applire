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

"""Write-time LLM annotation of role-expected fields for Master Profile entries (US179).

This service is the async counterpart to the pure-Python completeness scorer
in ``services/profile/completeness.py``.  It mutates a profile dict in-place
and is deliberately free of any DB or router imports so it can be called from
both the import path and the merge path without circular dependencies.

Public API
----------
annotate_expected_fields(profile, provider) → profile
    For each un-annotated work_experience entry, query the LLM to decide which
    CONDITIONAL_FIELDS apply and store the result as ``entry["expected_fields"]``.
"""

from __future__ import annotations

import logging

from applire.prompts.profile_field_expectations import (
    FIELD_EXPECTATIONS_SYSTEM_PROMPT,
    build_field_expectations_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.services.profile.completeness import CONDITIONAL_FIELDS

logger = logging.getLogger(__name__)


async def annotate_expected_fields(profile: dict, provider: LLMProvider) -> dict:
    """Mutate & return ``profile``: annotate un-analysed work_experience entries.

    For each entry whose ``expected_fields`` is ``None`` (or absent — meaning it
    has never been analysed), ask the LLM which ``CONDITIONAL_FIELDS`` apply to
    that role and store the filtered result as ``entry["expected_fields"]``.

    Idempotent: entries whose ``expected_fields`` is already a non-None value
    (including an empty list ``[]``) are skipped — the LLM is not called again.

    Resilient: on ANY provider error the entry's ``expected_fields`` is left as
    ``None``, causing the completeness scorer's lean floor fallback to apply.
    The function never raises.

    Args:
        profile:  Master Profile dict (mutated in-place).
        provider: Any LLM provider implementing ``aparse_json``.

    Returns:
        The same ``profile`` dict (mutation convenience).
    """
    _cond_set: frozenset[str] = frozenset(CONDITIONAL_FIELDS)

    for entry in profile.get("work_experience") or []:
        if entry.get("expected_fields") is not None:
            continue
        try:
            data = await provider.aparse_json(
                build_field_expectations_prompt(entry),
                system=FIELD_EXPECTATIONS_SYSTEM_PROMPT,
                temperature=0.1,
            )
            picked = data.get("expected") if isinstance(data, dict) else None
            entry["expected_fields"] = [f for f in (picked or []) if f in _cond_set]
        except Exception:
            logger.warning(
                "annotate_expected_fields: provider error for role %r; leaving expected_fields=None",
                entry.get("role"), exc_info=True,
            )
            entry["expected_fields"] = None

    return profile
