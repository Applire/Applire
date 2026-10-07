# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Error detail hardening — unexpected errors never echo internal exception text.

Seams, one class each:

* settings — a provider API key outside printable ASCII is refused at start,
  naming the setting, never the value;
* provider layer — an unclassified SDK / HTTP-client exception leaves a
  provider as ``LLMProviderError`` with a static message and no chained
  original (LLM providers, embeddings, OCR);
* REST — every route whose catch-all answered ``500 detail=str(exc)`` answers
  ``{error_code, message, error_id}`` (one row per call site);
* MCP — every tool catch-all answers a static message plus an error id (one
  row per call site), the identity wrapper catches what a tool body lets
  escape, and every MCP error message is scrubbed;
* logs and stored job errors — scrubbed of configured secret values.

Zero provider calls, zero outbound network: the one end-to-end case points the
Mistral SDK at a socket on 127.0.0.1 that never answers.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from mcp.shared.exceptions import McpError

from applire.config import (
    PROVIDER_API_KEY_FIELDS,
    InvalidSettingError,
    Settings,
    settings,
)
from applire.exceptions import (
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from applire.internal_errors import INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE
from applire.redaction import REDACTED, SecretRedactionFilter, scrub_detail, scrub_secrets
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse
from tests.support.owners_1b import add_user, client_for

#: A configured secret value for the duration of a test (never a real key).
#: Deliberately NOT credential-shaped (no ``sk-``/``Bearer``), so only the
#: configured-value scrub can remove it.
SENTINEL = "Mx7HARDENSENTINELq9Zt4uV0"
#: The same marker inside a value no header can carry.
CTRL_SENTINEL = "Mx7HARDENSENTINEL\x0bq9Zt"


@pytest.fixture
def configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", SENTINEL)
    return SENTINEL


def _assert_clean(text: str) -> None:
    assert "HARDENSENTINEL" not in text, text[:300]


# =====================================================================================
# Settings — provider API keys are printable ASCII
# =====================================================================================


@pytest.mark.parametrize("field", PROVIDER_API_KEY_FIELDS)
@pytest.mark.parametrize("bad", ["\x0b", "\x1b", "\x00", "\x7f", " ", "ä", "​"])
def test_settings_refuse_a_provider_key_outside_printable_ascii(field, bad):
    value = f"sk-test-HARDENSENTINEL{bad}0123"
    with pytest.raises(InvalidSettingError) as exc:
        Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", **{field: value})
    message = str(exc.value)
    assert field.upper() in message
    _assert_clean(message)
    assert not isinstance(exc.value, ValueError)  # pydantic would quote the input


def test_settings_refuse_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", CTRL_SENTINEL)
    with pytest.raises(InvalidSettingError) as exc:
        Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    assert "OPENROUTER_API_KEY" in str(exc.value)
    _assert_clean(str(exc.value))


def test_settings_keep_a_printable_key_and_strip_surrounding_whitespace():
    s = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        mistral_api_key="  sk-ok_ABC-123.xyz~  \n",
        anthropic_api_key="",
    )
    assert s.mistral_api_key == "sk-ok_ABC-123.xyz~"
    assert s.anthropic_api_key == ""


# =====================================================================================
# Scrubbing
# =====================================================================================


def test_scrub_removes_configured_values_in_plain_and_escaped_forms(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", CTRL_SENTINEL)
    raw = f"plain {CTRL_SENTINEL} repr {CTRL_SENTINEL!r} bytes {CTRL_SENTINEL.encode()!r}"
    _assert_clean(scrub_secrets(raw))


@pytest.mark.parametrize(
    "text",
    [
        "Illegal header value b'Bearer abc\\x0bdefHARDENSENTINEL'",
        "Authorization: Bearer HARDENSENTINELtoken123",
        "key sk-or-v1-HARDENSENTINEL0000 rejected",
    ],
)
def test_scrub_removes_credential_shaped_tokens_without_configuration(text):
    out = scrub_secrets(text)
    _assert_clean(out)
    assert REDACTED in out


def test_scrub_leaves_ordinary_text_and_non_strings_alone():
    assert scrub_secrets("Mistral call timed out after 30s") == "Mistral call timed out after 30s"
    assert scrub_secrets(None) is None
    assert scrub_detail({"n": 3, "s": "plain"}) == {"n": 3, "s": "plain"}


def test_scrub_detail_walks_nested_structures(configured_secret):
    detail = {"error_code": "x", "message": f"m {SENTINEL}", "list": [f"{SENTINEL}", 1]}
    _assert_clean(json.dumps(scrub_detail(detail)))


def test_log_filter_scrubs_message_args_and_traceback(configured_secret):
    record_logger = logging.getLogger("applire.test_error_detail_hardening")
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(SecretRedactionFilter())
    record_logger.addHandler(handler)
    record_logger.propagate = False
    try:
        record_logger.warning("value %s", SENTINEL)
        try:
            raise RuntimeError(f"boom {SENTINEL}")
        except RuntimeError:
            record_logger.exception("failed")
    finally:
        record_logger.removeHandler(handler)
        record_logger.propagate = True
    assert len(captured) == 2
    for line in captured:
        _assert_clean(line)
    assert "RuntimeError" in captured[1]


def test_main_installs_the_log_filter_and_the_http_detail_scrub():
    import applire.main as main

    applire_handlers = logging.getLogger("applire").handlers
    assert any(
        isinstance(f, SecretRedactionFilter) for h in applire_handlers for f in h.filters
    )
    assert any(isinstance(f, SecretRedactionFilter) for f in logging.getLogger("uvicorn.error").filters)
    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert main.app.exception_handlers[StarletteHTTPException] is main._scrubbed_http_exception_handler


@pytest.mark.asyncio
async def test_http_detail_scrub_handler(configured_secret):
    import applire.main as main
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = await main._scrubbed_http_exception_handler(
        request, HTTPException(status_code=422, detail={"message": f"bad {SENTINEL}"})
    )
    assert response.status_code == 422
    _assert_clean(response.body.decode())


# =====================================================================================
# Provider layer
# =====================================================================================

_HTTPX_ERR = httpx.LocalProtocolError(f"Illegal header value b'Bearer {SENTINEL}'")


def _provider_cases():
    from applire.providers.llm.anthropic import AnthropicProvider
    from applire.providers.llm.mistral import MistralProvider
    from applire.providers.llm.ollama import OllamaProvider
    from applire.providers.llm.openai import OpenAIProvider
    from applire.providers.llm.openrouter import OpenRouterProvider
    from applire.providers.llm.requesty import RequestyProvider

    return [
        ("mistral", lambda: MistralProvider(api_key="k" * 12)),
        ("openai", lambda: OpenAIProvider(api_key="k" * 12)),
        ("openrouter", lambda: OpenRouterProvider(api_key="k" * 12)),
        ("requesty", lambda: RequestyProvider(api_key="k" * 12)),
        ("anthropic", lambda: AnthropicProvider(api_key="k" * 12)),
        ("ollama", lambda: OllamaProvider()),
    ]


def _inner_methods(provider):
    """The provider's own SDK-call seams (``acomplete``/``aparse_json`` wrap them)."""
    names = [n for n in ("_complete", "_parse_json", "_create") if hasattr(provider, n)]
    assert names, type(provider).__name__
    return names


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["acomplete", "aparse_json"])
@pytest.mark.parametrize("name,factory", _provider_cases())
async def test_provider_wraps_an_unclassified_sdk_error(name, factory, method, configured_secret, monkeypatch):
    provider = factory()
    for inner in _inner_methods(provider):
        monkeypatch.setattr(provider, inner, AsyncMock(side_effect=_HTTPX_ERR))
    with pytest.raises(LLMProviderError) as exc:
        await getattr(provider, method)("hello")
    err = exc.value
    _assert_clean(str(err))
    _assert_clean(err.detail)
    assert err.sdk_type == "LocalProtocolError"
    assert err.__cause__ is None and err.__suppress_context__ is True
    import traceback

    _assert_clean("".join(traceback.format_exception(type(err), err, err.__traceback__)))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,factory", _provider_cases())
@pytest.mark.parametrize(
    "ours",
    [
        LLMTruncatedError("ours"),
        json.JSONDecodeError("ours", "x", 0),
        ValueError("ours"),
    ],
    ids=["truncated", "jsondecode", "valueerror"],
)
async def test_provider_passes_our_own_and_classified_errors_unchanged(name, factory, ours, monkeypatch):
    provider = factory()
    for inner in _inner_methods(provider):
        monkeypatch.setattr(provider, inner, AsyncMock(side_effect=ours))
    with pytest.raises(type(ours)) as exc:
        await provider.acomplete("hello")
    assert exc.value is ours or type(exc.value) is type(ours)


def test_wrapped_error_keeps_the_status_the_ops_probe_classifies_on():
    from applire.providers.llm.base import unclassified_provider_error
    from applire.services.ops.probes import _classify_unavailable

    response = httpx.Response(401, request=httpx.Request("POST", "http://127.0.0.1/"))
    import openai

    sdk = openai.AuthenticationError(f"Incorrect key {SENTINEL}", response=response, body=None)
    wrapped = unclassified_provider_error(sdk, "OpenRouter")
    assert wrapped is not None and wrapped.status_code == 401
    assert "HTTP 401" in str(wrapped)
    assert _classify_unavailable(f"{type(wrapped).__name__}: {wrapped}")[0] == "unauthorised"


def test_a_non_sdk_exception_carrying_a_secret_is_wrapped_too(configured_secret):
    from applire.providers.llm.base import unclassified_provider_error

    assert unclassified_provider_error(RuntimeError("plain"), "X") is None
    wrapped = unclassified_provider_error(RuntimeError(f"has {SENTINEL}"), "X")
    assert isinstance(wrapped, LLMProviderError)
    _assert_clean(str(wrapped) + wrapped.detail)


@pytest.mark.asyncio
async def test_embedding_and_ocr_providers_wrap_sdk_errors(configured_secret, monkeypatch):
    from applire.ocr import mistral_vision
    from applire.providers.embedding.mistral import MistralEmbeddingProvider
    from applire.providers.embedding.openai import OpenAIEmbeddingProvider

    m = MistralEmbeddingProvider(api_key="k" * 12)
    monkeypatch.setattr(m._client.embeddings, "create", AsyncMock(side_effect=_HTTPX_ERR))
    o = OpenAIEmbeddingProvider(api_key="k" * 12)
    monkeypatch.setattr(o._client.embeddings, "create", AsyncMock(side_effect=_HTTPX_ERR))
    for provider in (m, o):
        with pytest.raises(LLMProviderError) as exc:
            await provider.embed("x")
        _assert_clean(str(exc.value))

    class _Client:
        def __init__(self, **_):
            self.chat = SimpleNamespace(complete_async=AsyncMock(side_effect=_HTTPX_ERR))

    import mistralai

    monkeypatch.setattr(mistralai, "Mistral", _Client)
    with pytest.raises(LLMProviderError) as exc:
        await mistral_vision.MistralVisionExtractor(api_key="k" * 12).extract(b"x", "image/png")
    _assert_clean(str(exc.value))


# =====================================================================================
# REST — one row per catch-all call site
# =====================================================================================

U = uuid.uuid4()
POSTING = (
    "Senior Backend Engineer (m/w/d) at Example GmbH, Berlin. You build Python services "
    "with FastAPI and PostgreSQL. Requirements: 5 years Python, SQL, Docker. "
) * 3

#: (router module, name patched on it, method, path, json body)
REST_SITES = [
    ("application", "create_application", "POST", "/api/applications", {"job_analysis_id": str(U)}),
    ("application", "patch_application", "PATCH", f"/api/applications/{U}", {"notes": "n"}),
    ("application", "delete_application", "DELETE", f"/api/applications/{U}", None),
    ("application", "start_application_workflow", "POST", f"/api/applications/{U}/start", None),
    ("cv", "generate_cv", "POST", "/api/cv/generate", {"job_id": str(U)}),
    ("cv", "get_cv_status", "GET", f"/api/cv/{U}/status", None),
    ("cv", "get_cv_html", "GET", f"/api/cv/{U}/html", None),
    ("cv", "get_cv_pdf", "GET", f"/api/cv/{U}/pdf", None),
    ("cv", "get_cv_docx", "GET", f"/api/cv/{U}/docx", None),
    ("cv", "list_cvs_for_job", "GET", f"/api/cv?job_id={U}", None),
    ("cv", "get_cv_sections", "GET", f"/api/cv/{U}/sections", None),
    ("cv", "start_assist_session", "POST", f"/api/cv/{U}/sections/summary/assist", {"gap_id": "g"}),
    ("cv", "submit_assist_answer", "PATCH", f"/api/cv/{U}/sections/summary/assist",
     {"session_id": "s", "answer": "a"}),
    ("cv", "rewrite_section", "POST", f"/api/cv/{U}/sections/summary/rewrite", {}),
    ("cv", "patch_cv_section", "PATCH", f"/api/cv/{U}/sections/summary", {"content": "x"}),
    ("jobs", "rank_jobs", "GET", "/api/jobs/match", None),
    ("cover_letter", "generate_cover_letter", "POST", "/api/cover-letter/generate", {"job_id": str(U)}),
    ("session", "get_session_state", "GET", f"/api/session/{U}", None),
    ("job", "analyze_jd", "POST", "/api/job/analyze", {"text": POSTING}),
    ("job", "analyze_gaps", "POST", f"/api/job/{U}/gaps/refresh", None),
]


def _without_log_filter(monkeypatch) -> None:
    """Detach the process-wide log filter for one test (restored afterwards)."""
    for handler in logging.getLogger("applire").handlers:
        kept = [f for f in handler.filters if not isinstance(f, SecretRedactionFilter)]
        monkeypatch.setattr(handler, "filters", kept)


class _AsUser:
    def __init__(self, user):
        self.user = user

    async def get_current_user(self, request, _db):
        request.state.auth_via = "session"
        return self.user


def _rest_app(user, db=None) -> FastAPI:
    import importlib

    from applire.auth import get_auth_provider
    from applire.db.session import get_db

    app = FastAPI()
    for mod in ("application", "cv", "jobs", "cover_letter", "session", "job"):
        router_mod = importlib.import_module(f"applire.routers.{mod}")
        app.include_router(router_mod.router)
        if hasattr(router_mod, "_get_provider"):
            app.dependency_overrides[router_mod._get_provider] = lambda: MagicMock()
    from applire.routers import job as job_router

    app.dependency_overrides[job_router._linked_job] = lambda: MagicMock()

    async def _db():
        yield db if db is not None else AsyncMock()

    async def _prov():
        return _AsUser(user)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _prov
    return app


def _plain_user():
    from applire.models.user import User

    return User(id=uuid.uuid4(), email="plain@example.org", role="user")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,target,method,path,body", REST_SITES, ids=[f"{m}.{t}" for m, t, *_ in REST_SITES]
)
async def test_rest_catch_all_answers_a_static_body_with_an_error_id(
    module, target, method, path, body, configured_secret, monkeypatch, caplog
):
    import importlib

    router_mod = importlib.import_module(f"applire.routers.{module}")
    monkeypatch.setattr(router_mod, target, AsyncMock(side_effect=RuntimeError(f"db said {SENTINEL}")))
    if module == "jobs":
        monkeypatch.setattr(router_mod, "get_profile_for_user", AsyncMock(return_value=SimpleNamespace(id=U)))
    app = _rest_app(_plain_user())
    caplog.set_level(logging.ERROR)
    _without_log_filter(monkeypatch)  # prove the helper's own scrub, not the filter's
    async with client_for(app) as client:
        res = await client.request(method, path, json=body, headers={"Origin": "http://applire.test"})
    assert res.status_code == 500, res.text
    detail = res.json()["detail"]
    assert set(detail) == {"error_code", "message", "error_id"}
    assert detail["error_code"] == INTERNAL_ERROR_CODE
    assert detail["message"] == INTERNAL_ERROR_MESSAGE
    assert len(detail["error_id"]) == 12
    _assert_clean(res.text)
    # The exception is in the log under the same id — scrubbed.
    assert detail["error_id"] in caplog.text and "RuntimeError" in caplog.text
    _assert_clean(caplog.text)


@pytest.mark.asyncio
async def test_rest_classified_messages_are_unchanged(monkeypatch):
    """The 4xx/503/504 branches keep the text we wrote."""
    from applire.routers import job as job_router

    app = _rest_app(_plain_user())
    cases = [
        (LLMTimeoutError("Mistral call timed out after 30s"), 504, "Mistral call timed out after 30s"),
        (LLMRateLimitError("Mistral rate limit after 3 attempts"), 503, "Mistral rate limit after 3 attempts"),
        (ValueError("This does not look like a job posting."), 422, "This does not look like a job posting."),
    ]
    for exc, code, text in cases:
        monkeypatch.setattr(job_router, "analyze_jd", AsyncMock(side_effect=exc))
        async with client_for(app) as client:
            res = await client.post("/api/job/analyze", json={"text": POSTING}, headers={"Origin": "http://applire.test"})
        assert res.status_code == code and res.json()["detail"] == text


# =====================================================================================
# End to end: a plain user's request never receives a configured key
# =====================================================================================


@pytest.mark.asyncio
async def test_a_plain_user_never_receives_a_configured_key_in_any_response(async_db, monkeypatch):
    """The provider key holds a value the HTTP client refuses as a header; the
    SDK's error text quotes it. A plain (non-admin) account analyses a posting
    through REST and through the agent door: neither answer carries the key.
    Hermetic — the SDK points at a socket on 127.0.0.1 that never answers."""
    import applire.providers.llm.mistral as mistral_mod
    from applire.mcp import server as mcp_server
    from applire.routers import job as job_router

    monkeypatch.setattr(settings, "llm_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", CTRL_SENTINEL)  # past the start-up check

    srv = await asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        mistral_mod, "Mistral", functools.partial(mistral_mod.Mistral, server_url=f"http://127.0.0.1:{port}")
    )
    user = await add_user(async_db, email="plain-user@example.org", role="user")
    app = _rest_app(user, db=async_db)
    app.dependency_overrides.pop(job_router._get_provider, None)  # the real provider
    try:
        async with client_for(app) as client:
            res = await client.post("/api/job/analyze", json={"text": POSTING}, headers={"Origin": "http://applire.test"})

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=async_db)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(mcp_server, "get_db", lambda: cm)
        monkeypatch.setattr(mcp_server, "_current_user_id", AsyncMock(return_value=user.id))
        with pytest.raises(McpError) as mcp_exc:
            await mcp_server.analyze_jd(text=POSTING)
    finally:
        srv.close()
        await srv.wait_closed()

    assert res.status_code == 500, res.text
    _assert_clean(res.text)
    assert res.json()["detail"]["error_code"] == INTERNAL_ERROR_CODE
    message = mcp_exc.value.error.message
    _assert_clean(message)
    assert "Mistral call failed (LocalProtocolError)" in message


# =====================================================================================
# MCP — one row per catch-all call site, the wrapper, the scrubbed helpers
# =====================================================================================


def _mcp_db():
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


#: (tool, patched dotted attribute on applire.mcp.server, kwargs)
MCP_SITES = [
    ("import_cv", "profile_svc.import_from_text", {"text": "Jane Doe, engineer"}),
    ("analyze_jd", "job_svc.analyze_jd", {"text": POSTING}),
    ("analyze_gaps", "gap_svc.analyze_gaps", {"job_id": str(U)}),
    ("run_interview", "session_svc.create_session", {"job_id": str(U)}),
    ("send_message", "session_svc.send_message", {"session_id": str(U), "message": "hi"}),
    ("generate_cv", "cv_svc.generate_cv", {"job_id": str(U)}),
    ("generate_cover_letter", "cover_letter_svc.generate_cover_letter", {"job_id": str(U)}),
    ("start_flow", "flow_svc.create_flow", {"job_id": str(U)}),
    ("advance_flow", "flow_svc.advance_flow", {"flow_id": str(U), "step": "gaps"}),
    ("list_applications", "app_svc.list_applications", {}),
    ("get_application", "app_svc.get_application", {"application_id": str(U)}),
    ("create_application", "app_svc.create_application", {"job_id": str(U)}),
    ("update_application", "app_svc.patch_application", {"application_id": str(U), "notes": "n"}),
    ("add_role", "add_role_to_profile", {"title": "Engineer", "company": "Example GmbH", "start_date": "2020-01-01"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,target,kwargs", MCP_SITES, ids=[t for t, *_ in MCP_SITES])
async def test_mcp_catch_all_answers_a_static_message_with_an_error_id(
    tool, target, kwargs, configured_secret, monkeypatch, caplog
):
    from applire.mcp import server

    monkeypatch.setattr(server, "get_db", lambda: _mcp_db())
    monkeypatch.setattr(server, "get_provider", MagicMock())
    monkeypatch.setattr(server, "_current_user_id", AsyncMock(return_value=U))
    monkeypatch.setattr(server, "_owned_job", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(server, "_owned", AsyncMock(return_value=MagicMock()))
    owner_name, _, attr = target.rpartition(".")
    owner = getattr(server, owner_name) if owner_name else server
    monkeypatch.setattr(owner, attr, AsyncMock(side_effect=RuntimeError(f"db said {SENTINEL}")))
    caplog.set_level(logging.ERROR)
    _without_log_filter(monkeypatch)
    with pytest.raises(McpError) as exc:
        await getattr(server, tool)(**kwargs)
    message = exc.value.error.message
    assert message.startswith(INTERNAL_ERROR_MESSAGE), message
    assert "error id " in message
    _assert_clean(message)
    error_id = message.rsplit("error id ", 1)[1].rstrip(")")
    assert error_id in caplog.text
    _assert_clean(caplog.text)


def test_mcp_every_catch_all_uses_the_helper():
    """Inventory: no ``except Exception`` branch in the MCP door hands ``str(exc)``
    (or an f-string of ``exc``) to ``internal(...)``. (``invalid_input`` of a
    validation error on the agent's own arguments is a classified answer.)"""
    source = Path(__file__).resolve().parents[2] / "applire" / "mcp" / "server.py"
    tree = ast.parse(source.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id == "Exception")
        if not broad:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "internal"
                and any(isinstance(n, ast.Name) and n.id == node.name for a in sub.args for n in ast.walk(a))
            ):
                offenders.append(sub.lineno)
    assert offenders == [], offenders


@pytest.mark.asyncio
async def test_mcp_wrapper_catches_what_a_tool_body_lets_escape(configured_secret):
    from applire.mcp import server

    @server._agent_call
    async def _tool():
        raise KeyError(f"escaped {SENTINEL}")

    with pytest.raises(McpError) as exc:
        await _tool()
    assert exc.value.error.message.startswith(INTERNAL_ERROR_MESSAGE)
    _assert_clean(exc.value.error.message)


@pytest.mark.asyncio
async def test_mcp_keeps_our_own_llm_messages():
    from applire.mcp import server

    @server._agent_call
    async def _tool():
        raise LLMRateLimitError("Mistral rate limit after 3 attempts")

    with pytest.raises(McpError) as exc:
        await _tool()
    assert exc.value.error.message == "Mistral rate limit after 3 attempts"


def test_mcp_error_helpers_scrub_their_message(configured_secret):
    from applire.mcp.errors import internal, invalid_input, not_found, unauthorized

    for helper in (internal, invalid_input, not_found, unauthorized):
        _assert_clean(helper(f"m {SENTINEL}").error.message)


# =====================================================================================
# Stored job errors
# =====================================================================================


def test_generation_failure_stores_scrubbed_text(configured_secret):
    from applire.services.cv import _record_generation_failure

    record = SimpleNamespace(status=None, error_message=None, error_code=None)
    _record_generation_failure(record, RuntimeError(f"x {SENTINEL}"))
    _assert_clean(record.error_message)


@pytest.mark.parametrize(
    "path",
    [
        "applire/services/profile/import_jobs.py",
        "applire/services/gap_jobs.py",
        "applire/services/cover_letter.py",
        "applire/services/cv.py",
    ],
)
def test_every_stored_error_message_is_scrubbed(path):
    """Inventory: an ``error_message`` assigned from exception text is scrubbed."""
    source = Path(__file__).resolve().parents[2] / path
    tree = ast.parse(source.read_text())
    found = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "error_message" for t in node.targets)
            and "exc" in ast.unparse(node.value)
        ):
            found += 1
            assert "scrub_secrets(" in ast.unparse(node.value), ast.unparse(node)
    assert found == 1
