"""
Iteration 6 — LLM Provider Choice (unit tests)
Mock-based tests for the provider factory and OpenAI / Ollama implementations.
No Docker or real LLM calls required.

Run:
    pytest tests/unit/ -v
"""
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_factory_returns_mistral_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.mistral import MistralProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "mistral")
    monkeypatch.setattr(cfg.settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "mistral_model", "mistral-small-latest")
    with patch("applire.providers.llm.mistral.Mistral"):
        provider = get_provider()
    assert isinstance(provider, MistralProvider)


def test_factory_returns_openai_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.openai import OpenAIProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "openai")
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")
    with patch("openai.AsyncOpenAI"):
        provider = get_provider()
    assert isinstance(provider, OpenAIProvider)


def test_factory_returns_ollama_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.ollama import OllamaProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "ollama")
    monkeypatch.setattr(cfg.settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(cfg.settings, "ollama_model", "llama3.2")
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)


def test_factory_raises_on_unknown_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider

    monkeypatch.setattr(cfg.settings, "llm_provider", "unknownprovider")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_provider()


def test_factory_is_case_insensitive(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.mistral import MistralProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "Mistral")
    monkeypatch.setattr(cfg.settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "mistral_model", "mistral-small-latest")
    with patch("applire.providers.llm.mistral.Mistral"):
        provider = get_provider()
    assert isinstance(provider, MistralProvider)


# ---------------------------------------------------------------------------
# OpenAI provider tests
# ---------------------------------------------------------------------------


def _make_openai_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_openai_acomplete_sends_messages(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    mock_create = AsyncMock(return_value=_make_openai_response("hello world"))
    provider._client.chat.completions.create = mock_create

    result = await provider.acomplete("Say hello", system="You are helpful.")

    assert result == "hello world"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "Say hello"}


@pytest.mark.asyncio
async def test_openai_acomplete_without_system(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    mock_create = AsyncMock(return_value=_make_openai_response("pong"))
    provider._client.chat.completions.create = mock_create

    await provider.acomplete("ping")

    call_kwargs = mock_create.call_args.kwargs
    assert len(call_kwargs["messages"]) == 1
    assert call_kwargs["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_openai_aparse_json_returns_dict(monkeypatch):
    payload = {"role_title": "Backend Engineer", "skills": ["Python"]}

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    mock_create = AsyncMock(return_value=_make_openai_response(json.dumps(payload)))
    provider._client.chat.completions.create = mock_create

    result = await provider.aparse_json("Analyse this JD")

    assert result == payload


@pytest.mark.asyncio
async def test_openai_aparse_json_requests_json_format(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    mock_create = AsyncMock(return_value=_make_openai_response('{"ok": true}'))
    provider._client.chat.completions.create = mock_create

    await provider.aparse_json("Go")

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Ollama provider tests
# ---------------------------------------------------------------------------


def _make_httpx_response(body: dict[str, Any]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=body)
    return mock_resp


def _patch_httpx_post(return_value: MagicMock):
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=return_value)
    mock_async_cm = MagicMock()
    mock_async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_cm.__aexit__ = AsyncMock(return_value=False)
    return patch("applire.providers.llm.ollama.httpx.AsyncClient", return_value=mock_async_cm), mock_client


@pytest.mark.asyncio
async def test_ollama_acomplete_returns_content():
    ollama_body = {"message": {"role": "assistant", "content": "Hallo Welt"}}
    patcher, mock_client = _patch_httpx_post(_make_httpx_response(ollama_body))

    with patcher:
        from applire.providers.llm.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
        result = await provider.acomplete("Sag Hallo")

    assert result == "Hallo Welt"


@pytest.mark.asyncio
async def test_ollama_acomplete_posts_to_correct_endpoint():
    ollama_body = {"message": {"content": "ok"}}
    patcher, mock_client = _patch_httpx_post(_make_httpx_response(ollama_body))

    with patcher:
        from applire.providers.llm.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
        await provider.acomplete("test")

    url = mock_client.post.call_args.args[0]
    assert url == "http://localhost:11434/api/chat"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["stream"] is False
    assert payload["model"] == "llama3.2"


@pytest.mark.asyncio
async def test_ollama_aparse_json_returns_dict():
    inner = {"match_score": 82, "critical_gaps": ["Kubernetes"]}
    ollama_body = {"message": {"content": json.dumps(inner)}}
    patcher, mock_client = _patch_httpx_post(_make_httpx_response(ollama_body))

    with patcher:
        from applire.providers.llm.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
        result = await provider.aparse_json("Analyse gaps")

    assert result == inner


@pytest.mark.asyncio
async def test_ollama_aparse_json_requests_json_format():
    inner = {"ok": True}
    ollama_body = {"message": {"content": json.dumps(inner)}}
    patcher, mock_client = _patch_httpx_post(_make_httpx_response(ollama_body))

    with patcher:
        from applire.providers.llm.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
        await provider.aparse_json("Go")

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload.get("format") == "json"


@pytest.mark.asyncio
async def test_ollama_strips_trailing_slash_from_base_url():
    ollama_body = {"message": {"content": "hi"}}
    patcher, mock_client = _patch_httpx_post(_make_httpx_response(ollama_body))

    with patcher:
        from applire.providers.llm.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434/", model="llama3.2")
        await provider.acomplete("hi")

    url = mock_client.post.call_args.args[0]
    assert url == "http://localhost:11434/api/chat"


# ---------------------------------------------------------------------------
# MockLLMProvider — cover letter fingerprint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_cover_letter_fingerprint():
    """MockLLMProvider must return a schema-valid cover letter dict."""
    from applire.providers.llm.mock import MockLLMProvider

    provider = MockLLMProvider()
    result = await provider.aparse_json(
        "Generate a cover letter.",
        system="You are an expert DACH career coach writing a professional Bewerbungsschreiben (German cover letter).",
    )

    assert isinstance(result, dict), "Expected a dict"
    for key in ("header", "recipient", "body", "signature"):
        assert key in result, f"Missing key: {key}"

    header = result["header"]
    assert "name" in header and "address" in header

    body = result["body"]
    assert "paragraphs" in body
    assert isinstance(body["paragraphs"], list)
    assert len(body["paragraphs"]) >= 3

    signature = result["signature"]
    assert "closing" in signature and "name" in signature


@pytest.mark.asyncio
async def test_mock_cover_letter_grounding_reviewer_approves():
    """US170 — the cover-letter grounding reviewer (review_cover_letter) must be a
    recognised chain so CI's mock approves it. An unrecognised reviewer falls through to
    the {mock:...} fallback (approved=None), which fails the review_and_refine loop and
    ships a corrupt letter under the mock provider — the cause of the PQ timeout."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT

    result = await MockLLMProvider().aparse_json("review this", system=REVIEW_SYSTEM_PROMPT)
    assert result.get("approved") is True
    assert "issues" in result and "feedback" in result


@pytest.mark.asyncio
async def test_mock_cv_extraction_reviewer_approves():
    """US171 — the CV-extraction grounding reviewer (review_cv_extraction) must be a
    recognised chain. Unrecognised → {mock:...} fallback (approved=None) → review_and_refine
    treats it as a rejection and retries to exhaustion on every mock CV upload (the PQ
    timeout class). The mock must approve so the extraction path completes cleanly."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT

    result = await MockLLMProvider().aparse_json("review this", system=CV_EXTRACTION_REVIEW_SYSTEM_PROMPT)
    assert result.get("approved") is True
    assert "issues" in result and "feedback" in result


@pytest.mark.asyncio
async def test_mock_profile_extraction_reviewer_approves():
    """US171 — the LinkedIn-side profile-extraction reviewer (review_profile_extraction)
    shares the 'CV data quality auditor' opener and was equally unrecognised; recognising
    the auditor family fixes both extraction reviewers."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.review_profile_extraction import REVIEW_SYSTEM_PROMPT

    result = await MockLLMProvider().aparse_json("review this", system=REVIEW_SYSTEM_PROMPT)
    assert result.get("approved") is True
    assert "issues" in result and "feedback" in result


@pytest.mark.asyncio
async def test_mock_segmented_extraction_chains_recognised():
    """US195 / ADR-047 — the three segmented-extraction chains (outline → per-role detail →
    core) must be recognised so a capped-mock extraction run assembles a valid profile
    instead of corrupting on the {mock:...} fallback. The assembled dict must validate as a
    master profile with the mock's work positions and name."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.cv_extraction_segmented import (
        EXTRACTION_OUTLINE_SYSTEM_PROMPT,
        EXTRACTION_DETAIL_SYSTEM_PROMPT,
        EXTRACTION_CORE_SYSTEM_PROMPT,
    )
    from applire.services.profile.extract_segmented import extract_profile_segmented

    p = MockLLMProvider()
    outline = await p.aparse_json("x", system=EXTRACTION_OUTLINE_SYSTEM_PROMPT)
    detail = await p.aparse_json("x", system=EXTRACTION_DETAIL_SYSTEM_PROMPT)
    core = await p.aparse_json("x", system=EXTRACTION_CORE_SYSTEM_PROMPT)
    assert "work_experience" in outline
    assert {"responsibilities", "achievements", "technologies"} <= detail.keys()
    assert "work_experience" not in core  # core never re-emits work

    data = await extract_profile_segmented("Anna Bauer CV text", p)
    from applire.schemas.profile import MasterProfileData
    profile = MasterProfileData.model_validate(data)
    assert profile.personal_info.name == "Anna Bauer"
    assert len(data["work_experience"]) >= 1


@pytest.mark.asyncio
async def test_mock_cv_tailoring_reviewer_approves():
    """US171 (sibling gap) — the CV-tailoring grounding reviewer (review_cv_tailoring,
    'CV quality auditor') was also unrecognised by the mock, the same retry-to-exhaustion
    class on the generation path. Recognise it too."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    result = await MockLLMProvider().aparse_json("review this", system=REVIEW_SYSTEM_PROMPT)
    assert result.get("approved") is True
    assert "issues" in result and "feedback" in result


@pytest.mark.asyncio
async def test_mock_cover_letter_refinement_returns_valid_letter():
    """US170 — the cover-letter corrector (COVER_LETTER_REFINEMENT_PROMPT) must also be
    recognised, so a retry returns a schema-valid letter, not the {mock:...} fallback."""
    from applire.providers.llm.mock import MockLLMProvider
    from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT

    result = await MockLLMProvider().aparse_json("fix it", system=COVER_LETTER_REFINEMENT_PROMPT)
    for key in ("header", "recipient", "body", "signature"):
        assert key in result, f"Missing key: {key}"
    assert isinstance(result["body"].get("paragraphs"), list)
