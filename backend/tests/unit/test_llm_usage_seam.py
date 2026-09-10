# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The token-accounting seam (US313, ADR-086 clause 7).

Sits beside ``test_llm_debug_log.py`` on purpose: the two artefacts are
neighbours at the same seam and the boundary between them is what
``test_llm_usage_has_no_text_column`` pins.

One fixture per provider usage shape, because the vendors disagree and a
normaliser that only knows one of them silently reports zero for the others.
The three shapes are read from the real code, not invented:
``usage.prompt_tokens``/``completion_tokens`` (Mistral, OpenAI, OpenRouter,
Requesty), ``usage.input_tokens``/``output_tokens`` (Anthropic) and
``prompt_eval_count``/``eval_count`` (Ollama).
"""

import uuid

import pytest

from applire.providers.llm.base import LLMProvider
from applire.providers.llm.usage import (
    Usage,
    UsageRecordingProvider,
    extract_usage,
    llm_usage_context,
    note_usage,
    reset_sink_state,
)
from applire.providers.llm import usage as usage_module


# ── the vendor fixtures ───────────────────────────────────────────────────────


def _openai_shape(prompt: int, completion: int):
    """Mistral, OpenAI, OpenRouter and Requesty all use the openai SDK's shape."""
    return type(
        "ChatCompletion",
        (),
        {"usage": type("U", (), {"prompt_tokens": prompt, "completion_tokens": completion})()},
    )()


def _anthropic_shape(prompt: int, completion: int):
    return type(
        "Message",
        (),
        {"usage": type("U", (), {"input_tokens": prompt, "output_tokens": completion})()},
    )()


def _ollama_shape(prompt: int, completion: int):
    """Ollama's /api/chat body, as `response.json()` hands it back."""
    return {
        "message": {"content": "hi"},
        "done_reason": "stop",
        "prompt_eval_count": prompt,
        "eval_count": completion,
    }


@pytest.mark.parametrize(
    "provider_name,response",
    [
        ("mistral", _openai_shape(11, 22)),
        ("openai", _openai_shape(11, 22)),
        ("openrouter", _openai_shape(11, 22)),
        ("requesty", _openai_shape(11, 22)),
        ("anthropic", _anthropic_shape(11, 22)),
        ("ollama", _ollama_shape(11, 22)),
    ],
)
def test_every_provider_usage_shape_is_read(provider_name, response):
    usage = extract_usage(response)
    assert usage == Usage(11, 22, estimated=False), provider_name
    assert usage.total_tokens == 33


def test_a_response_without_usage_reads_as_none_and_costs_nothing():
    """US313 AC — 'zero overhead when a provider omits usage'.

    No exception, no network, no second attribute walk: the mock provider and
    every vendor that omits the field land here, and the caller estimates.
    """
    assert extract_usage({"message": {"content": "hi"}, "done_reason": "stop"}) is None
    assert extract_usage(type("R", (), {})()) is None
    assert extract_usage(None) is None
    assert extract_usage("a plain string") is None


def test_a_boolean_is_never_read_as_a_token_count():
    assert extract_usage({"prompt_eval_count": True, "eval_count": False}) is None


def test_a_partial_usage_object_still_yields_what_it_has():
    response = type("R", (), {"usage": type("U", (), {"prompt_tokens": 7})()})()
    assert extract_usage(response) == Usage(7, 0)


# ── the wrapper ───────────────────────────────────────────────────────────────


class _StubProvider(LLMProvider):
    def __init__(self, response=None, raises: Exception | None = None) -> None:
        super().__init__(timeout=1)
        self._model = "stub-model-1"
        self._response = response
        self._raises = raises

    async def acomplete(self, prompt: str, **kwargs):
        if self._raises:
            raise self._raises
        if self._response is not None:
            note_usage(self._response)
        return "an answer"

    async def aparse_json(self, prompt: str, **kwargs):
        if self._response is not None:
            note_usage(self._response)
        return {"ok": True}


@pytest.fixture
def rows(monkeypatch):
    captured: list[dict] = []

    async def _sink(row):
        captured.append(row)

    monkeypatch.setattr(usage_module, "_sink", _sink)
    reset_sink_state()
    yield captured


@pytest.mark.asyncio
async def test_one_row_per_call_with_the_providers_own_numbers(rows):
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(30, 12)))
    await provider.acomplete("hello", system="sys")
    assert len(rows) == 1
    row = rows[0]
    assert (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (30, 12, 42)
    assert row["estimated"] is False
    assert row["provider"] == "stub"  # _StubProvider -> "stub" (leading _ stripped)
    assert row["model"] == "stub-model-1"
    assert row["method"] == "acomplete"
    assert row["duration_ms"] >= 0
    assert row["ok"] is True


@pytest.mark.asyncio
async def test_a_provider_without_usage_yields_a_flagged_estimate(rows):
    """SF-OPS.8 — an estimate that does not say it is one is worse than nothing."""
    provider = UsageRecordingProvider(_StubProvider(response=None))
    await provider.acomplete("x" * 400, system="y" * 400)
    assert rows[0]["estimated"] is True
    assert rows[0]["prompt_tokens"] == 200  # 800 chars / 4
    assert rows[0]["completion_tokens"] > 0


@pytest.mark.asyncio
async def test_a_failed_call_still_records_a_row(rows):
    """A burst of failures is exactly what a runaway loop looks like."""
    provider = UsageRecordingProvider(_StubProvider(raises=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await provider.acomplete("hello")
    assert len(rows) == 1
    assert rows[0]["ok"] is False


@pytest.mark.asyncio
async def test_usage_from_one_call_never_leaks_into_the_next(rows):
    """The contextvar is reset per call; a failed call must not inherit."""
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(30, 12)))
    await provider.acomplete("first")
    provider._inner._response = None
    provider._inner._raises = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await provider.acomplete("second")
    assert rows[0]["estimated"] is False
    assert rows[1]["estimated"] is True


@pytest.mark.asyncio
async def test_the_context_attributes_a_call_to_its_document(rows):
    document = uuid.uuid4()
    application = uuid.uuid4()
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
    with llm_usage_context(
        stage="cv_tailoring",
        document_kind="cv",
        document_id=document,
        application_id=application,
    ):
        await provider.acomplete("hello")
    assert rows[0]["stage"] == "cv_tailoring"
    assert rows[0]["document_kind"] == "cv"
    assert rows[0]["document_id"] == document
    assert rows[0]["application_id"] == application


@pytest.mark.asyncio
async def test_the_context_is_restored_on_exit(rows):
    """Set-and-restore, unlike debug_log.set_stage — services/cv.py:4073's trap.

    An audit tail running after a generation must not inherit the generation's
    document id.
    """
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
    with llm_usage_context(stage="cv_tailoring", document_id=uuid.uuid4()):
        await provider.acomplete("inside")
    await provider.acomplete("outside")
    assert rows[0]["document_id"] is not None
    assert rows[1]["document_id"] is None
    assert rows[1]["stage"] == ""


@pytest.mark.asyncio
async def test_an_unattributed_call_still_lands(rows):
    """SF-OPS.7 — a visibly unattributed row, never a wrongly attributed one."""
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(5, 5)))
    await provider.acomplete("hello")
    assert rows[0]["document_id"] is None
    assert rows[0]["total_tokens"] == 10


@pytest.mark.asyncio
async def test_the_stage_falls_back_to_the_debug_logs_label(rows):
    from applire.providers.llm.debug_log import set_stage

    set_stage("cv_extraction")
    try:
        provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
        await provider.acomplete("hello")
        assert rows[0]["stage"] == "cv_extraction"
    finally:
        set_stage("")


@pytest.mark.asyncio
async def test_a_broken_sink_never_breaks_a_generation(monkeypatch):
    async def _explode(_row):
        raise RuntimeError("no such table: llm_usage")

    monkeypatch.setattr(usage_module, "_sink", _explode)
    reset_sink_state()
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
    assert await provider.acomplete("hello") == "an answer"


@pytest.mark.asyncio
async def test_the_wrapper_is_transparent_to_the_debug_logger():
    """The debug log reads `_inner._model`; the wrapper must not hide it."""
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
    assert provider._model == "stub-model-1"
    assert getattr(provider, "_timeout") == 1


def test_the_factory_installs_the_recorder_under_the_debug_logger(monkeypatch):
    import applire.config as cfg
    from applire.providers.llm import get_provider, unwrap_provider
    from applire.providers.llm.debug_log import _LoggingProvider
    from applire.providers.llm.ollama import OllamaProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "ollama")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", True)
    provider = get_provider()
    assert isinstance(provider, _LoggingProvider)
    assert isinstance(provider._inner, UsageRecordingProvider)
    assert isinstance(unwrap_provider(provider), OllamaProvider)


def test_usage_tracking_can_be_switched_off(monkeypatch):
    from applire.services.ops import config as ops_config
    from applire.providers.llm.usage import wrap_usage

    inner = _StubProvider()
    monkeypatch.setattr(ops_config, "LLM_USAGE_TRACKING", "off")
    assert wrap_usage(inner) is inner


# ── the PII boundary (SF-OPS.9) ───────────────────────────────────────────────


def test_the_family_label_matches_the_provider_names():
    """The label the operator matches against their invoice — pin all six.

    The same family vocabulary ADR-085 clause 3 permits the PDF mark to carry;
    a class rename must not silently change what the cost table says.
    """
    from applire.providers.llm.usage import _provider_family

    expected = {
        "applire.providers.llm.mistral": ("MistralProvider", "mistral"),
        "applire.providers.llm.openai": ("OpenAIProvider", "openai"),
        "applire.providers.llm.openrouter": ("OpenRouterProvider", "openrouter"),
        "applire.providers.llm.requesty": ("RequestyProvider", "requesty"),
        "applire.providers.llm.anthropic": ("AnthropicProvider", "anthropic"),
        "applire.providers.llm.ollama": ("OllamaProvider", "ollama"),
        "applire.providers.llm.mock": ("MockLLMProvider", "mock"),
    }
    import importlib

    for module_name, (class_name, family) in expected.items():
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        instance = cls.__new__(cls)  # no network, no key — only the type matters
        assert _provider_family(instance) == family, class_name


def test_llm_usage_has_no_text_column():
    """The structural half of the boundary with the debug log.

    Adding a column to carry "just the first 200 characters for debugging" fails
    HERE, which is the point: the table's 365-day clock is not an ADR-005 PII
    clock, and its aggregates are reachable through an unauthenticated endpoint.
    """
    from sqlalchemy import Text

    from applire.models.llm_usage import LlmUsage

    allowed = {
        "id",
        "created_at",
        "provider",
        "model",
        "stage",
        "method",
        "prompt_tokens",
        "completion_tokens",
        # M-4: a SUBSET of completion_tokens, still a number, still no text.
        "reasoning_tokens",
        "total_tokens",
        "estimated",
        "document_kind",
        "document_id",
        "application_id",
        "duration_ms",
        "ok",
    }
    columns = {c.name for c in LlmUsage.__table__.columns}
    assert columns == allowed, f"unexpected column set: {columns ^ allowed}"
    assert not [c.name for c in LlmUsage.__table__.columns if isinstance(c.type, Text)]


@pytest.mark.asyncio
async def test_no_prompt_or_completion_text_ever_reaches_a_row(rows):
    secret_prompt = "Marcus Weber, born 1984, lives at Hauptstrasse 3"
    provider = UsageRecordingProvider(_StubProvider(_openai_shape(1, 1)))
    await provider.acomplete(secret_prompt, system="you are a CV writer")
    serialised = repr(rows[0])
    assert "Marcus" not in serialised
    assert "Hauptstrasse" not in serialised
    assert "CV writer" not in serialised
