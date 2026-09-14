# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#675 line 32 / ruling M5.5.1 — MODE C has its own system prompt.

MODE C fills ONE missing field on ONE position the vault already holds. It ran
under `GUIDED_QUESTION_SYSTEM_PROMPT` — MODE B's — which tells the model the
candidate is building a profile "from scratch", "has no existing CV to fall back
on", and is being asked about a "section". The user prompt on the same call says
position, field and what they already did there. The model resolved the
contradiction toward the system prompt.

Measured before shipping (M5.5.1's "ship only on a gain"): 5 field gaps x n=2 per
arm, blind-scored by a Sonnet reviewer on precision / length / altitude —
4.00/2.60/4.30 (MODE B's prompt) vs 5.00/4.50/5.00 (MODE C's own), and
"asks the candidate to restate what the profile already holds" 3/10 -> 0/10.
Numbers and per-field table: the prompt's own docstring in `prompts/interview.py`.

These tests pin the SEAM (which system prompt reaches which branch) and the two
facts the new prompt exists to state. They cannot pin question quality — that is
what the measurement above is for, and it is not a CI-affordable check.
"""
from unittest.mock import AsyncMock, patch

import pytest


def _profile() -> dict:
    return {
        "personal_info": {"name": "Marcus Schmidt"},
        "work_experience": [
            {
                "company": "Kaltenbach Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2017-03",
                "responsibilities": ["Leitung der Spritzgussfertigung"],
            }
        ],
    }


def _state(gap: str) -> dict:
    return {
        "mode": "profile_enrich",
        "critical_gaps": [gap],
        "current_gap_index": 0,
        "messages": [],
    }


async def _generate(gap: str, profile: dict | None = None):
    """Drive the real question node, capturing the provider call it makes."""
    from applire.services import interview_graph as g

    provider = AsyncMock()
    provider.acomplete.return_value = "Wie viele Mitarbeitende waren Ihnen unterstellt?"
    provider.aparse_json.return_value = {"question": "Und sonst?", "choices": None}
    with patch.object(
        g, "_review_question_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)
    ):
        draft = await g.question_generator_with_profile(
            _state(gap), profile if profile is not None else _profile(), provider
        )
    return draft, provider


@pytest.mark.asyncio
async def test_mode_c_uses_its_own_system_prompt_not_mode_bs():
    from applire.prompts.interview import (
        FIELD_GAP_QUESTION_SYSTEM_PROMPT,
        GUIDED_QUESTION_SYSTEM_PROMPT,
    )

    _, provider = await _generate("team_size: Produktionsleiter @ Kaltenbach Kunststofftechnik GmbH")

    assert provider.acomplete.await_count == 1, "MODE C must take the field-gap branch"
    system = provider.acomplete.await_args.kwargs["system"]
    assert FIELD_GAP_QUESTION_SYSTEM_PROMPT in system
    assert GUIDED_QUESTION_SYSTEM_PROMPT not in system


@pytest.mark.asyncio
async def test_mode_b_still_uses_mode_bs_prompt():
    """The seam cuts BOTH ways: MODE B is a from-scratch build and keeps the
    from-scratch prompt. A change that swapped both would pass the test above."""
    from applire.prompts.interview import (
        FIELD_GAP_QUESTION_SYSTEM_PROMPT,
        GUIDED_QUESTION_SYSTEM_PROMPT,
    )
    from applire.services import interview_graph as g

    provider = AsyncMock()
    provider.acomplete.return_value = "Erzählen Sie von Ihrer Berufserfahrung."
    state = {"mode": "guided", "critical_gaps": ["work_experience"], "current_gap_index": 0,
             "messages": []}
    with patch.object(
        g, "_review_question_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)
    ):
        await g.question_generator_with_profile(state, _profile(), provider)

    system = provider.acomplete.await_args.kwargs["system"]
    assert GUIDED_QUESTION_SYSTEM_PROMPT in system
    assert FIELD_GAP_QUESTION_SYSTEM_PROMPT not in system


def test_the_new_prompt_states_the_three_facts_mode_bs_denied():
    """Not a wording test — the three claims are the reason the prompt exists,
    and each is the negation of a sentence MODE B's prompt makes."""
    from applire.prompts.interview import (
        FIELD_GAP_QUESTION_SYSTEM_PROMPT as P,
        GUIDED_QUESTION_SYSTEM_PROMPT as B,
    )

    lowered = P.lower()
    # 1. the profile already exists (MODE B: "from scratch")
    assert "already exists" in lowered
    assert "from scratch" not in lowered and "from scratch" in B.lower()
    # 2. one FIELD of one POSITION (MODE B: "exactly ONE profile section")
    assert "one detail" in lowered
    assert "profile section" not in lowered and "profile section" in B.lower()
    # 3. the CV was imported (MODE B: "no existing CV to fall back on")
    assert "imported" in lowered
    assert "no existing cv" not in lowered and "no existing cv" in B.lower()
    # And the rule the blind rubric found both arms breaking on budgets, so it
    # is stated rather than assumed.
    assert "nil answer" in lowered


@pytest.mark.asyncio
async def test_a_professional_summary_gap_still_produces_a_question():
    """#675 line 42 made `professional_summary` reachable, and MODE C's
    field-gap branch deliberately excludes it (it is about no single position).
    It therefore falls through to the cluster path — which must still ASK, not
    crash. What that question is like is unmeasured; collector line."""
    draft, provider = await _generate("professional_summary")

    assert provider.acomplete.await_count == 0, "the field-gap branch must not claim it"
    assert provider.aparse_json.await_count == 1
    assert draft["question"], "a reachable gap that produces no question is not reachable"
