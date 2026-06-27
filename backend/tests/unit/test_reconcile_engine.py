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

"""Unit tests for the ADR-046 single-call reconciler (US181)."""
from __future__ import annotations

from typing import Any

import pytest

from applire.exceptions import LLMError, LLMTruncatedError
from applire.prompts.reconcile import (
    RECONCILE_SYSTEM_PROMPT,
    build_reconcile_prompt,
)
from applire.providers.llm.mock import MockLLMProvider
from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    ReconcileResult,
    RequestConfirmation,
    UpsertSkill,
    UpsertWork,
)


class _StubProvider:
    """Minimal provider stub returning a canned dict.

    MUST accept **kwargs to absorb the full provider-ABC signature
    (disable_thinking, temperature, max_tokens, system, …).
    """

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.last_prompt: str | None = None
        self.last_kwargs: dict[str, Any] = {}

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.last_prompt = prompt
        self.last_kwargs = kwargs
        return self.payload


@pytest.mark.asyncio
async def test_reconcile_parses_single_upsert_work() -> None:
    provider = _StubProvider(
        {
            "ops": [
                {"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y"}
            ],
            "ambiguities": [],
        }
    )
    result = await reconcile(
        MasterProfileData(), "I joined X as Y.", "interview", provider
    )

    assert isinstance(result, ReconcileResult)
    assert len(result.ops) == 1
    op = result.ops[0]
    assert isinstance(op, UpsertWork)
    assert op.ref == "w1"
    assert op.company == "X"
    assert op.role == "Y"
    assert result.ambiguities == []


@pytest.mark.asyncio
async def test_reconcile_one_call_with_thinking_enabled() -> None:
    """Exactly one aparse_json call; reasoning is NOT disabled (content task)."""
    provider = _StubProvider({"ops": [], "ambiguities": []})
    await reconcile(MasterProfileData(), "new info", "cv", provider)

    # disable_thinking must not be forced True (contrast chrome chains).
    assert provider.last_kwargs.get("disable_thinking") in (None, False)
    assert provider.last_kwargs.get("system") == RECONCILE_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_reconcile_drops_malformed_op_keeps_valid() -> None:
    provider = _StubProvider(
        {
            "ops": [
                {"op": "upsert_work", "ref": "w1", "company": "ACME", "role": "Dev"},
                {"op": "upsert_work"},  # missing required ref/company/role
                {"op": "not_a_real_op", "foo": "bar"},  # unknown discriminator
            ],
            "ambiguities": [],
        }
    )
    result = await reconcile(MasterProfileData(), "info", "manual", provider)

    assert len(result.ops) == 1
    assert isinstance(result.ops[0], UpsertWork)
    assert result.ops[0].company == "ACME"


@pytest.mark.asyncio
async def test_reconcile_garbage_payload_yields_empty_result() -> None:
    for payload in ([], "totally garbage", {"unexpected": "shape"}, None, 42):
        provider = _StubProvider(payload)
        result = await reconcile(MasterProfileData(), "info", "manual", provider)
        assert isinstance(result, ReconcileResult)
        assert result.ops == []
        assert result.ambiguities == []


class _RaisingProvider:
    """Provider stub whose aparse_json always raises ``exc``.

    Absorbs the full provider-ABC signature via **kwargs (disable_thinking,
    temperature, max_tokens, system, …) so it is a faithful drop-in.
    """

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        raise self.exc


@pytest.mark.asyncio
async def test_reconcile_propagates_truncation_not_empty() -> None:
    """Truncation = data loss; it MUST surface, never become a silent empty merge.

    Regression for the one-CV-wins bug: a blanket ``except Exception`` swallowed
    ``LLMTruncatedError`` into an empty ``ReconcileResult`` (no ops → the second
    CV's content dropped). The engine must now re-raise truncation.
    """
    provider = _RaisingProvider(LLMTruncatedError("hit the token budget"))
    with pytest.raises(LLMTruncatedError):
        await reconcile(MasterProfileData(), "lots of new info", "cv_upload", provider)


@pytest.mark.asyncio
async def test_reconcile_generic_error_still_degrades_to_empty() -> None:
    """Non-truncation provider/transport errors keep the old behaviour:
    degrade to an empty result, never escape (unchanged intent)."""
    for exc in (RuntimeError("transport boom"), LLMError("vendor noise"), ValueError("x")):
        provider = _RaisingProvider(exc)
        result = await reconcile(MasterProfileData(), "info", "cv_upload", provider)
        assert isinstance(result, ReconcileResult)
        assert result.ops == []
        assert result.ambiguities == []


@pytest.mark.asyncio
async def test_reconcile_folds_ambiguities() -> None:
    provider = _StubProvider(
        {
            "ops": [],
            "ambiguities": [
                {
                    "op": "request_confirmation",
                    "question": "Is the Berlin project part of your ACME role?",
                    "options": ["Yes", "No"],
                }
            ],
        }
    )
    result = await reconcile(MasterProfileData(), "info", "interview", provider)

    assert len(result.ambiguities) == 1
    assert isinstance(result.ambiguities[0], RequestConfirmation)
    assert "Berlin" in result.ambiguities[0].question


@pytest.mark.asyncio
async def test_reconcile_never_raises_on_provider_noise() -> None:
    """A bad op inside ambiguities must not crash the batch."""
    provider = _StubProvider(
        {
            "ops": [{"op": "upsert_work", "ref": "w1", "company": "X", "role": "Y"}],
            "ambiguities": [{"op": "request_confirmation"}],  # missing question
        }
    )
    result = await reconcile(MasterProfileData(), "info", "cv", provider)
    assert len(result.ops) == 1
    # The malformed ambiguity is dropped, not raised.
    assert result.ambiguities == []


def test_build_prompt_contains_entity_ids_and_new_info() -> None:
    profile = MasterProfileData(
        work_experience=[WorkEntry(company="ACME GmbH", role="Engineer")]
    )
    existing_id = profile.work_experience[0].id
    new_info = "I led the Phoenix migration project at ACME."

    prompt = build_reconcile_prompt(profile, new_info, "interview")

    assert existing_id in prompt  # model can target the existing entity
    assert "Phoenix migration" in prompt
    assert "interview" in prompt


def test_build_prompt_serialises_dict_new_info() -> None:
    profile = MasterProfileData()
    new_info = {"skill": "Kubernetes", "level": "advanced"}

    prompt = build_reconcile_prompt(profile, new_info, "linkedin_import")

    assert "Kubernetes" in prompt
    assert "linkedin_import" in prompt


@pytest.mark.asyncio
async def test_mock_recognises_reconcile_chain() -> None:
    """Mock-recognition regression: the reconcile chain must NOT hit the
    {"mock": ...} fallback under MockLLMProvider."""
    data = await MockLLMProvider().aparse_json(
        "any prompt", system=RECONCILE_SYSTEM_PROMPT
    )
    assert "ops" in data
    assert "ambiguities" in data
    assert "mock" not in data


@pytest.mark.asyncio
async def test_reconcile_with_mock_provider_parses_cleanly() -> None:
    """End-to-end through the mock: a recognised envelope parses cleanly."""
    result = await reconcile(
        MasterProfileData(), "info", "interview", MockLLMProvider()
    )
    assert isinstance(result, ReconcileResult)
    assert result.ambiguities == []
    # The ops payload is covered by test_mock_reconciler_emits_representative_ops.


@pytest.mark.asyncio
async def test_mock_reconciler_emits_representative_ops() -> None:
    result = await reconcile(
        MasterProfileData(), "I use Python daily.", "interview", MockLLMProvider()
    )
    assert result.ops, "mock reconciler must emit representative ops for interview tests"
    assert any(isinstance(o, UpsertSkill) for o in result.ops)


@pytest.mark.asyncio
async def test_mock_reconciler_flags_ambiguity_for_synonym_role() -> None:
    """US185 — the synonym-fold UAT answer surfaces a RequestConfirmation through the engine."""
    result = await reconcile(
        MasterProfileData(),
        {"gap": "roles", "question": "current role?", "answer": "I'm the Owner at applire."},
        "interview",
        MockLLMProvider(),
    )
    assert result.ambiguities, "mock must surface a confirmation for the synonym-fold fixture"
    assert "Founder & Lead Developer" in result.ambiguities[0].question
    assert result.ambiguities[0].options
