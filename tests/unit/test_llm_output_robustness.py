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

"""LLM output robustness (ADR-047 / E036 US188).

Cap-aware budgeting replaces blind budget-doubling: when an operator declares a
real output ceiling for their model (LLM_MAX_OUTPUT_TOKENS), every requested
budget is clamped to it, so we never ask a capped model for more than it can
emit (which only trades truncation for timeout — the PQ-run-4 vise). The segment
budget (SEGMENT_MAX_TOKENS) is the conservative per-call output target that keeps
each segmented call comfortably under a typical ~8k hard cap.

Hermetic: no get_provider(), no real LLM — pure functions and injected stubs.
"""
import pytest

from applire.exceptions import LLMTruncatedError


# ---------------------------------------------------------------------------
# Cap-aware budgeting primitives (ADR-047 layer 2)
# ---------------------------------------------------------------------------


def test_segment_max_tokens_sits_under_a_typical_hard_cap():
    """A segmented per-call output must fit comfortably under the ~8k hard cap of
    output-capped models (e.g. mistral-medium-3-5 stops near ~8k) — ADR-047 §1."""
    from applire.constants import SEGMENT_MAX_TOKENS

    assert 0 < SEGMENT_MAX_TOKENS <= 4096


def test_clamp_output_budget_caps_request_to_operator_ceiling():
    """When the operator declares LLM_MAX_OUTPUT_TOKENS, a larger request is
    clamped down to it — we never ask a capped model for more than it can emit."""
    from applire.providers.llm.base import clamp_output_budget

    assert clamp_output_budget(16384, ceiling=8000) == 8000


def test_clamp_output_budget_is_a_noop_when_no_ceiling_declared():
    """No declared ceiling (0 = unset) → the request passes through unchanged;
    cap-awareness must not shrink budgets on models with no known cap."""
    from applire.providers.llm.base import clamp_output_budget

    assert clamp_output_budget(16384, ceiling=0) == 16384


def test_clamp_output_budget_leaves_a_request_below_the_ceiling_untouched():
    """A request already under the declared ceiling is not raised to it — the
    ceiling is a cap, not a target."""
    from applire.providers.llm.base import clamp_output_budget

    assert clamp_output_budget(2048, ceiling=8000) == 2048


# ---------------------------------------------------------------------------
# Cap-aware budgeting is threaded through the shared retry path (all providers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_truncation_clamps_initial_budget_to_operator_cap(monkeypatch):
    """When the operator declares a hard cap, the very first attempt already uses the
    clamped budget — we never request more than the model can emit (ADR-047 §2)."""
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_max_output_tokens", 8000)
    from applire.providers.llm.base import retry_on_truncation

    seen: list[int] = []

    async def attempt(budget: int) -> str:
        seen.append(budget)
        return "ok"

    await retry_on_truncation(attempt, max_tokens=16384)
    assert seen == [8000]  # clamped down from 16384 to the operator cap


@pytest.mark.asyncio
async def test_retry_on_truncation_does_not_double_past_operator_cap(monkeypatch):
    """At the operator cap, a truncation must NOT double into a slower oversized call
    (the truncation→timeout vise, ADR-047 §2). It re-raises after the single clamped
    attempt so the large-generation paths can switch to segmented mode instead."""
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_max_output_tokens", 8000)
    from applire.providers.llm.base import retry_on_truncation

    seen: list[int] = []

    async def attempt(budget: int) -> str:
        seen.append(budget)
        raise LLMTruncatedError("hit the cap")

    with pytest.raises(LLMTruncatedError):
        await retry_on_truncation(attempt, max_tokens=16384)
    assert seen == [8000]  # one attempt at the cap, never doubled past it


@pytest.mark.asyncio
async def test_retry_on_truncation_unset_cap_keeps_existing_doubling(monkeypatch):
    """No operator cap (0 = unset) → cap-awareness is a no-op and the existing one-off
    doubling safety net is preserved unchanged."""
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_max_output_tokens", 0)
    from applire.providers.llm.base import retry_on_truncation

    seen: list[int] = []

    async def attempt(budget: int) -> str:
        seen.append(budget)
        if len(seen) == 1:
            raise LLMTruncatedError("freak one-off")
        return "ok"

    result = await retry_on_truncation(attempt, max_tokens=4096, ceiling=16384)
    assert result == "ok"
    assert seen == [4096, 8192]  # unchanged doubling when no cap is declared


# ---------------------------------------------------------------------------
# Honest failure UX (ADR-047 §4 / PQ F6): internal LLM exceptions classify to a
# STABLE machine code; the raw text never reaches the user (the frontend maps the
# code to a localized message). Backend never localizes — error UI is "chrome".
# ---------------------------------------------------------------------------


def test_truncation_classifies_to_a_stable_truncated_code():
    from applire.exceptions import LLMTruncatedError
    from applire.services.cv import classify_generation_error

    assert classify_generation_error(LLMTruncatedError("Raise max_tokens")) == "llm_truncated"


def test_timeout_classifies_to_a_stable_timeout_code():
    from applire.exceptions import LLMTimeoutError
    from applire.services.cv import classify_generation_error

    assert classify_generation_error(LLMTimeoutError("timed out")) == "llm_timeout"


def test_unknown_error_classifies_to_a_generic_failure_code():
    """Any non-LLM failure maps to a generic code — never a raw stack/exception string."""
    from applire.services.cv import classify_generation_error

    assert classify_generation_error(ValueError("template blew up")) == "generation_failed"


def test_classified_code_is_never_the_raw_exception_text():
    """The whole point of F6: a code is returned, not the leaky 'Raise max_tokens or
    reduce reasoning' guidance that was being shown to users verbatim."""
    from applire.exceptions import LLMTruncatedError
    from applire.services.cv import classify_generation_error

    raw = "Model X hit the token budget; output is truncated. Raise max_tokens or reduce reasoning."
    code = classify_generation_error(LLMTruncatedError(raw))
    assert code == "llm_truncated"
    assert "max_tokens" not in code and " " not in code


def test_record_generation_failure_keeps_raw_text_internal_and_sets_code():
    """The catch-site seam: a failure stores the raw exception text in error_message
    (internal, for ops/logs) AND a classified error_code (surfaced to the UI). The two
    are separate so the raw 'Raise max_tokens' guidance never has to be shown."""
    from types import SimpleNamespace

    from applire.exceptions import LLMTruncatedError
    from applire.models.cv import CVGenerationStatus
    from applire.services.cv import _record_generation_failure

    record = SimpleNamespace(status=None, error_message=None, error_code=None)
    raw = "Model X hit the token budget. Raise max_tokens or reduce reasoning."
    _record_generation_failure(record, LLMTruncatedError(raw))

    assert record.status == CVGenerationStatus.failed.value
    assert record.error_code == "llm_truncated"
    assert record.error_message == raw  # raw kept internal, not localized here


def test_cv_status_response_carries_error_code():
    """The status contract gains a machine-readable error_code the frontend localizes."""
    import uuid
    from datetime import datetime, timezone

    from applire.models.cv import CVGenerationStatus
    from applire.schemas.cv import CVStatusResponse

    resp = CVStatusResponse(
        cv_id=uuid.uuid4(),
        status=CVGenerationStatus.failed,
        error_code="llm_truncated",
        expires_at=datetime.now(timezone.utc),
    )
    assert resp.error_code == "llm_truncated"
