# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work cut by a withdrawn consent answers as the refusal, on every door.

A request or tool call that is model work and meets the call-seam refusal
(the person withdrew the consent to the test instance's logs while it ran)
answers ``409 log_consent_required`` with the refusal's own message — the REST
envelope, or an MCP error whose text starts with the code — whatever the code
in between does with the exception: lets it through, turns it into an error
answer, or goes on without the model. A write that only layers model work on
top keeps its answer. Every 500 answer carries its error id.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth.deps import ModelWorkDoor, admit_model_work, require_user, try_admit_model_work
from applire.db.session import get_db
from applire.models.user import User
from applire.ownership import owner_context
from applire.providers.llm import debug_log
from applire.services import allowance, log_consent_live
from applire.services.allowance import REFUSAL_MESSAGES

UID = uuid.UUID("5e1f0c3a-7d2b-4c11-9a0e-3b6d8f2a1c44")
ENVELOPE = {"detail": {"error_code": "log_consent_required", "message": REFUSAL_MESSAGES["log_consent_required"]}}


@pytest.fixture(autouse=True)
def _clean():
    allowance.reset_context_for_tests()
    log_consent_live.reset_for_tests()
    yield
    allowance.reset_context_for_tests()
    log_consent_live.reset_for_tests()


def _user() -> User:
    user = User(email="person@example.org")
    user.id = UID
    user.log_consent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    return user


async def _cut_off():
    """What a model call meets once the person withdrew: the seam's refusal."""
    with owner_context(UID):
        log_consent_live.note_door(UID, True)
        log_consent_live.note_withdrawn(UID)  # DELETE /api/me/log-consent
        await debug_log._refuse_after_withdrawal()


def _through(*_a, **_k):
    async def run():
        await _cut_off()
    return run()


def _swallowed(fallback):
    """A body that catches the refusal and goes on without the model."""
    def make(*_a, **_k):
        async def run():
            try:
                await _cut_off()
            except Exception:  # noqa: BLE001 — the shape under test
                pass
            return fallback
        return run()
    return make


def _failing(*_a, **_k):
    async def run():
        raise RuntimeError("a fault that is not a refusal")
    return run()


async def _db():
    yield AsyncMock()


#: (kind, method, path, body, the service the route awaits)
KINDS = [
    ("interview turn", "post", f"/api/session/{uuid.uuid4()}/message", {"message": "Ich habe GCP genutzt."},
     "applire.routers.session.send_message"),
    ("gap analysis", "post", f"/api/session/{uuid.uuid4()}/analyze-gaps", None,
     "applire.routers.session.analyze_gaps_for_session"),
    ("job analysis", "post", "/api/job/analyze", {"text": "Wir suchen eine Entwicklerin."},
     "applire.routers.job.analyze_jd"),
    ("section assist", "post", f"/api/cv/{uuid.uuid4()}/sections/summary/assist", {"gap_id": "g1"},
     "applire.routers.cv.start_assist_session"),
    ("review action", "post", f"/api/cv/{uuid.uuid4()}/review/shorten", None,
     "applire.routers.review.ra.shorten_letter"),
]


async def _call(method, path, body, target, fake):
    from applire.main import app

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_user] = _user
    try:
        with patch("applire.services.allowance.admit", new=AsyncMock(return_value=None)), \
                patch(target, new=fake), \
                patch("applire.providers.llm.get_provider", return_value=object()):
            for mod in ("session", "job", "cv", "review"):
                patch(f"applire.routers.{mod}.get_provider", return_value=object()).start()
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                                   base_url="http://test") as c:
                kwargs = {"json": body} if body is not None else {}
                return await getattr(c, method)(path, **kwargs)
    finally:
        patch.stopall()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,method,path,body,target", KINDS, ids=[k[0] for k in KINDS])
async def test_a_cut_turn_answers_the_refusal_when_the_refusal_comes_through(kind, method, path, body, target):
    r = await _call(method, path, body, target, _through)
    assert (r.status_code, r.json()) == (409, ENVELOPE), kind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,method,path,body,target", KINDS, ids=[k[0] for k in KINDS])
async def test_a_cut_turn_answers_the_refusal_when_the_body_went_on_without_the_model(kind, method, path, body, target):
    r = await _call(method, path, body, target, _swallowed(fallback={}))
    assert (r.status_code, r.json()) == (409, ENVELOPE), kind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,method,path,body,target", KINDS, ids=[k[0] for k in KINDS])
async def test_any_other_fault_answers_500_with_its_error_id(kind, method, path, body, target):
    r = await _call(method, path, body, target, _failing)
    assert r.status_code == 500, kind
    detail = r.json()["detail"]
    assert detail["error_code"] == "internal_error" and len(detail["error_id"]) >= 8


# --- the mechanism, on a small app: which work is watched ----------------------


def _small_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(log_consent_live.RefusalBoundary)

    async def strict_door():
        log_consent_live.arm()

    @app.post("/strict-swallowed")
    async def strict_swallowed(_d=Depends(strict_door)):
        try:
            await _cut_off()
        except Exception:  # noqa: BLE001
            return {"done": "without the model"}

    @app.post("/strict-raises-other")
    async def strict_raises_other(_d=Depends(strict_door)):
        try:
            await _cut_off()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("wrapped") from exc

    @app.post("/layered")
    async def layered():
        try:
            await _cut_off()
        except Exception:  # noqa: BLE001 — the write stands, its model step is left out
            return {"saved": True}

    @app.post("/layered-branch")
    async def layered_branch():
        door = ModelWorkDoor()
        door.require()  # the branch that runs now IS model work
        try:
            await _cut_off()
        except Exception:  # noqa: BLE001
            return {"preview": None}

    @app.post("/strict-untouched")
    async def strict_untouched(_d=Depends(strict_door)):
        return {"ok": True}

    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("path,expected", [
    ("/strict-swallowed", (409, ENVELOPE)),
    ("/strict-raises-other", (409, ENVELOPE)),
    ("/layered", (200, {"saved": True})),
    ("/layered-branch", (409, ENVELOPE)),
    ("/strict-untouched", (200, {"ok": True})),
])
async def test_only_work_that_is_model_work_answers_the_refusal(path, expected):
    async with AsyncClient(transport=ASGITransport(app=_small_app(), raise_app_exceptions=False),
                           base_url="http://test") as c:
        r = await c.post(path)
    assert (r.status_code, r.json()) == expected


@pytest.mark.asyncio
async def test_the_strict_door_watches_the_request_and_the_optional_door_does_not():
    with log_consent_live.watching() as watch, \
            patch("applire.services.allowance.admit", new=AsyncMock(return_value=None)):
        await admit_model_work(user=_user(), db=AsyncMock())
        assert watch.strict
    with log_consent_live.watching() as watch, \
            patch("applire.services.allowance.admit", new=AsyncMock(return_value=None)):
        door = await try_admit_model_work(user=_user(), db=AsyncMock())
        assert door.admitted and not watch.strict


def test_the_app_answers_through_the_boundary():
    from applire.main import app

    assert any(m.cls is log_consent_live.RefusalBoundary for m in app.user_middleware)


def test_every_500_a_route_raises_carries_its_error_id():
    """A hand-written 500 names ``error_id`` in its detail; the catch-alls use
    ``internal_server_error``, which does."""
    routers = Path(__file__).resolve().parents[2] / "applire" / "routers"
    missing = []
    for path in sorted(routers.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            code = ast.unparse(kw.get("status_code", ast.Constant(None)))
            if "500" not in code:
                continue
            if "error_id" not in ast.unparse(kw.get("detail", ast.Constant(None))):
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


# --- MCP: every tool that is model work ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["through", "swallowed", "wrapped"])
async def test_an_mcp_tool_cut_by_the_withdrawal_answers_the_code_first(shape):
    from applire.mcp import server
    from applire.mcp import identity as mcp_identity

    async def body():
        if shape == "through":
            await _cut_off()
        try:
            await _cut_off()
        except Exception as exc:  # noqa: BLE001
            if shape == "wrapped":
                raise RuntimeError("wrapped") from exc
        return {"status": "done"}

    body.__applire_admits__ = "model_work"

    class _Db:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return False

    with patch.object(mcp_identity, "bound", return_value=None), \
            patch.object(mcp_identity, "revalidate", new=AsyncMock(return_value=_user())), \
            patch.object(allowance, "load_facts", new=AsyncMock(return_value=None)), \
            patch.object(allowance, "admit_from_facts", return_value=None), \
            patch.object(server, "get_db", new=lambda: _Db()):
        with pytest.raises(Exception) as exc:
            await server._agent_call_body(body, (), {})
    text = str(exc.value)
    assert text.startswith("log_consent_required"), text
    assert REFUSAL_MESSAGES["log_consent_required"] in text


@pytest.mark.asyncio
async def test_an_mcp_tool_that_only_layers_model_work_keeps_its_answer():
    from applire.mcp import server
    from applire.mcp import identity as mcp_identity

    async def body():
        try:
            await _cut_off()
        except Exception:  # noqa: BLE001
            pass
        return {"saved": True}

    class _Db:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *a):
            return False

    with patch.object(mcp_identity, "bound", return_value=None), \
            patch.object(mcp_identity, "revalidate", new=AsyncMock(return_value=_user())), \
            patch.object(allowance, "load_facts", new=AsyncMock(return_value=None)), \
            patch.object(server, "get_db", new=lambda: _Db()):
        assert await server._agent_call_body(body, (), {}) == {"saved": True}


def test_a_failed_job_s_status_names_the_refusal():
    """The CV and letter status doors (REST and the agent's get_*_status)."""
    from applire.services.cv import generation_error_message
    from applire.services.generation_errors import is_retryable, letter_error_code, letter_failure_message

    refusal = allowance.AdmissionRefused("log_consent_required")
    stored = letter_failure_message(refusal)
    assert letter_error_code(stored) == "log_consent_required"
    assert stored == REFUSAL_MESSAGES["log_consent_required"]
    assert generation_error_message("log_consent_required") == REFUSAL_MESSAGES["log_consent_required"]
    assert not is_retryable("log_consent_required")
    json.dumps(stored)  # a plain sentence


def test_the_catch_all_helper_hands_a_refusal_back_as_it_is():
    from applire.internal_errors import internal_server_error

    refusal = allowance.AdmissionRefused("log_consent_required")
    assert internal_server_error(refusal, where="test") is refusal
    other = internal_server_error(RuntimeError("x"), where="test")
    assert other.status_code == 500 and "error_id" in other.detail
