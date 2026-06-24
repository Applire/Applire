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

"""Unit tests for write-time LLM annotation of role-expected fields (US179).

Tests cover:
- annotate_expected_fields service (filtering, idempotency, error resilience)
- MockLLMProvider routing for management vs IC roles
"""

import pytest
from applire.services.profile.expectations import annotate_expected_fields
from applire.providers.llm.mock import MockLLMProvider


class StubProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def aparse_json(
        self, prompt, *, system=None, temperature=0.1, max_tokens=4096
    ):
        self.calls += 1
        return self.payload


@pytest.mark.asyncio
async def test_filters_to_conditional_fields():
    """LLM response fields outside CONDITIONAL_FIELDS are silently dropped."""
    p = {"work_experience": [{"role": "Lead", "company": "A"}]}
    await annotate_expected_fields(
        p, StubProvider({"expected": ["team_size", "bogus", "budget_managed"]})
    )
    assert p["work_experience"][0]["expected_fields"] == ["team_size", "budget_managed"]


@pytest.mark.asyncio
async def test_idempotent_skips_annotated():
    """Entries with an existing non-None expected_fields are not re-queried."""
    stub = StubProvider({"expected": ["team_size"]})
    p = {"work_experience": [{"role": "Lead", "expected_fields": []}]}
    await annotate_expected_fields(p, stub)
    assert stub.calls == 0 and p["work_experience"][0]["expected_fields"] == []


@pytest.mark.asyncio
async def test_error_falls_back_to_none():
    """Provider errors leave expected_fields as None (lean floor fallback)."""

    class Boom:
        async def aparse_json(self, *a, **k):
            raise RuntimeError("down")

    p = {"work_experience": [{"role": "Lead"}]}
    await annotate_expected_fields(p, Boom())
    assert p["work_experience"][0]["expected_fields"] is None


@pytest.mark.asyncio
async def test_mock_provider_routes_management_role():
    """MockLLMProvider returns all three conditional fields for management roles."""
    p = {"work_experience": [{"role": "Team Lead", "responsibilities": ["Led a team of 8"]}]}
    await annotate_expected_fields(p, MockLLMProvider())
    assert set(p["work_experience"][0]["expected_fields"]) == {
        "team_size",
        "budget_managed",
        "industry_context",
    }


@pytest.mark.asyncio
async def test_mock_provider_routes_ic_role():
    """MockLLMProvider returns empty list for individual-contributor roles."""
    p = {"work_experience": [{"role": "Junior Developer", "responsibilities": ["Wrote unit tests"]}]}
    await annotate_expected_fields(p, MockLLMProvider())
    assert p["work_experience"][0]["expected_fields"] == []


@pytest.mark.asyncio
async def test_multiple_entries_partial_annotated():
    """Only un-annotated entries are queried; annotated ones are skipped."""
    stub = StubProvider({"expected": ["industry_context"]})
    p = {
        "work_experience": [
            {"role": "Manager", "expected_fields": ["team_size"]},  # already annotated
            {"role": "Analyst"},  # not annotated → should be queried
        ]
    }
    await annotate_expected_fields(p, stub)
    # First entry unchanged
    assert p["work_experience"][0]["expected_fields"] == ["team_size"]
    # Second entry annotated (filtered to CONDITIONAL_FIELDS)
    assert p["work_experience"][1]["expected_fields"] == ["industry_context"]
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_empty_work_experience():
    """Profile with no work_experience entries returns unchanged."""
    stub = StubProvider({"expected": ["team_size"]})
    p = {"work_experience": []}
    result = await annotate_expected_fields(p, stub)
    assert result == {"work_experience": []}
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_missing_work_experience_key():
    """Profile with no work_experience key returns unchanged without error."""
    stub = StubProvider({"expected": ["team_size"]})
    p = {"personal_info": {"name": "Test"}}
    result = await annotate_expected_fields(p, stub)
    assert result == {"personal_info": {"name": "Test"}}
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_llm_returns_empty_list():
    """LLM returning empty expected list is stored correctly (not treated as None)."""
    stub = StubProvider({"expected": []})
    p = {"work_experience": [{"role": "Developer"}]}
    await annotate_expected_fields(p, stub)
    # [] is a valid annotation (IC role), distinct from None (not analysed)
    assert p["work_experience"][0]["expected_fields"] == []


@pytest.mark.asyncio
async def test_llm_returns_non_dict():
    """Non-dict LLM response is handled gracefully (treated as error path)."""

    class WeirdProvider:
        async def aparse_json(self, *a, **k):
            return ["team_size"]  # list, not dict

    p = {"work_experience": [{"role": "Lead"}]}
    await annotate_expected_fields(p, WeirdProvider())
    # picked = data.get("expected") fails on list → picks None → stored as []
    assert p["work_experience"][0]["expected_fields"] == []
