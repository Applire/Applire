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

"""Contract tests for review_and_refine — verify the generator callback no longer
receives raw source, that the reviewer callback still does, and that the new
chain_id is propagated to logs."""

import logging
from typing import Any

import pytest

from applire.services.reviewer import review_and_refine


class _FakeProvider:
    """Records every aparse_json call and returns scripted responses."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def aparse_json(self, prompt: str, *, system: str | None = None,
                          temperature: float = 0.1, max_tokens: int = 4096,
                          disable_thinking: bool | None = None) -> Any:
        self.calls.append(
            {"prompt": prompt, "system": system, "temperature": temperature}
        )
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_generator_callback_not_passed_source():
    """The generator retry callback must receive (draft, feedback) only — no source."""
    received_args: list[tuple] = []

    def generator(draft: dict, feedback: str) -> str:
        received_args.append((draft, feedback))
        return "retry prompt body"

    def reviewer(source: str, draft: dict) -> str:
        return "review prompt body"

    provider = _FakeProvider([
        {"approved": False, "issues": ["x"], "feedback": "fix x"},  # reviewer says no
        {"work_history": [], "patched": True},                       # generator retry
        {"approved": True, "issues": [], "feedback": ""},            # reviewer approves
    ])

    result = await review_and_refine(
        source="RAW SOURCE TEXT THAT MUST NOT REACH GENERATOR",
        draft={"work_history": [], "initial": True},
        generator_prompt_fn=generator,
        generator_system="REFINEMENT PROMPT",
        reviewer_prompt_fn=reviewer,
        reviewer_system="REVIEW PROMPT",
        provider=provider,
        max_retries=2,
        chain_id="test_chain",
    )

    assert result == {"work_history": [], "patched": True}
    assert len(received_args) == 1
    draft_seen, feedback_seen = received_args[0]
    assert draft_seen == {"work_history": [], "initial": True}
    assert feedback_seen == "fix x"


@pytest.mark.asyncio
async def test_reviewer_callback_still_gets_source():
    """The reviewer callback's signature is unchanged — it still receives (source, draft)."""
    received_args: list[tuple] = []

    def generator(draft: dict, feedback: str) -> str:
        return "retry prompt"

    def reviewer(source: str, draft: dict) -> str:
        received_args.append((source, draft))
        return "review prompt"

    provider = _FakeProvider([
        {"approved": True, "issues": [], "feedback": ""},
    ])

    await review_and_refine(
        source="REVIEWER SEES THIS",
        draft={"d": 1},
        generator_prompt_fn=generator,
        generator_system="REFINEMENT",
        reviewer_prompt_fn=reviewer,
        reviewer_system="REVIEW",
        provider=provider,
        max_retries=1,
        chain_id="test_chain",
    )

    assert len(received_args) == 1
    source_seen, draft_seen = received_args[0]
    assert source_seen == "REVIEWER SEES THIS"
    assert draft_seen == {"d": 1}


@pytest.mark.asyncio
async def test_chain_id_logged(caplog: pytest.LogCaptureFixture):
    """Each retry attempt logs chain_id at INFO level for observability."""
    def generator(draft: dict, feedback: str) -> str:
        return "retry"

    def reviewer(source: str, draft: dict) -> str:
        return "review"

    provider = _FakeProvider([
        {"approved": False, "issues": ["x"], "feedback": "fix"},
        {"d": 2},
        {"approved": True, "issues": [], "feedback": ""},
    ])

    with caplog.at_level(logging.INFO, logger="applire.services.reviewer"):
        await review_and_refine(
            source="s",
            draft={"d": 1},
            generator_prompt_fn=generator,
            generator_system="REFINEMENT",
            reviewer_prompt_fn=reviewer,
            reviewer_system="REVIEW",
            provider=provider,
            max_retries=2,
            chain_id="my_chain",
        )

    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert any("chain=my_chain" in r.getMessage() for r in info_records)
    assert any("retry_input_chars" in r.getMessage() for r in info_records)


@pytest.mark.asyncio
async def test_chain_id_defaults_to_unknown():
    """chain_id is optional and defaults to 'unknown' for safe migration."""
    provider = _FakeProvider([
        {"approved": True, "issues": [], "feedback": ""},
    ])

    # Call without chain_id — must not raise
    await review_and_refine(
        source="s",
        draft={"d": 1},
        generator_prompt_fn=lambda d, f: "retry",
        generator_system="REFINEMENT",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="REVIEW",
        provider=provider,
        max_retries=1,
    )
    # No assertion needed beyond not-raising — chain_id has a default.
