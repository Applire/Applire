# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 1 — the adversarial review's findings F1–F7 (MD-10), each
pinned by a test built from the reviewer's own probe.

F1 debug log scrubs provider error text · F2 a process that never read the
panel overrides refuses model work · F3 an unknown ALLOWANCE_POLICY warns ·
F4 a short PROVIDER_KEY_SECRET counts as unset · F5 user-key clients ignore
the operator's SDK environment · F6 the key test is throttled · F7 a racing
first save answers 409.
"""

from __future__ import annotations

import logging
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.config import settings
from applire.exceptions import LLMResponseParseError
from applire.models.user import User
from applire.ownership import owner_context
from applire.services import allowance, provider_keys
from tests.unit.test_adr096_doors import (  # noqa: F401 — fixtures
    ORIGIN,
    SECRET,
    USER_KEY,
    _clean,
    _store_key,
    env,
    user_mode,
)


@pytest.fixture(autouse=True)
def _fresh_test_throttle():
    provider_keys.reset_test_throttle_for_tests()
    yield
    provider_keys.reset_test_throttle_for_tests()


# ── F1: the LLM debug log ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_f1_debug_log_scrubs_the_provider_error(tmp_path, monkeypatch):
    """Probe ``test_debug_log_unscrubbed_error``: a provider error echoing the
    person's key reached ``logs/llm/<day>.jsonl`` in plaintext."""
    from applire.providers.llm import debug_log

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))

    class Inner:
        _model = "m"
        _timeout = 5

        async def acomplete(self, prompt, **k):
            raise Exception(f"Incorrect API key provided: {USER_KEY}")

    allowance._RECENT_KEYS.append(USER_KEY)
    w = debug_log._LoggingProvider(Inner())
    with pytest.raises(Exception):
        await w.acomplete("hi")
    text = "".join(open(os.path.join(tmp_path, f)).read() for f in os.listdir(tmp_path))
    assert USER_KEY not in text
    assert "Incorrect API key provided" in text  # the record is still written


@pytest.mark.asyncio
async def test_f1_debug_log_scrubs_a_failed_reply(tmp_path, monkeypatch):
    """The failed call's raw reply (R2-13 keeps it) is scrubbed the same way."""
    from applire.providers.llm import debug_log

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))

    class Inner:
        _model = "m"
        _timeout = 5

        async def aparse_json(self, prompt, **k):
            raise LLMResponseParseError("not JSON", doc=f"gateway said: bad key {USER_KEY}")

    allowance._RECENT_KEYS.append(USER_KEY)
    w = debug_log._LoggingProvider(Inner())
    with pytest.raises(LLMResponseParseError):
        await w.aparse_json("hi")
    text = "".join(open(os.path.join(tmp_path, f)).read() for f in os.listdir(tmp_path))
    assert USER_KEY not in text
    assert "gateway said: bad key" in text


# ── F2: a process that never read the panel overrides ─────────────────────────


def _fresh_process(monkeypatch):
    """The probe's fresh process: empty overlay, no successful read yet."""
    from applire import config as _config
    from applire.services import instance_settings as ins

    monkeypatch.setattr(_config, "_overlay_latest", [_config._EMPTY])
    monkeypatch.setattr(ins, "_state", ins._ProcessState())

    def boom():
        raise RuntimeError("db blip")

    monkeypatch.setattr(ins, "_session_factory", boom)
    return ins


@pytest.mark.asyncio
async def test_f2_failed_first_refresh_refuses_model_work(monkeypatch):
    """Probe ``test_overlay_refresh_failure_fails_open``: the panel says ``user``,
    the environment ``instance``; the first refresh fails. Before: decisions
    ran as ``instance`` (row 1, a person with no key and no grant paid by the
    instance). Now every decision refuses with ``settings_unavailable``."""
    monkeypatch.setattr(settings, "llm_key_mode", "instance")
    ins = _fresh_process(monkeypatch)
    await ins.refresh()
    assert ins.overlay_unknown() is True
    assert allowance.decision_mode() == "unknown"
    assert allowance.key_mode() == "instance"  # the reported mode is unchanged

    person = MagicMock(spec=User)
    person.id = uuid.uuid4()
    facts = await allowance.load_facts(MagicMock(), person)
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "settings_unavailable" and exc.value.status_code == 503

    allowance.reset_context_for_tests()
    with owner_context(person.id):  # the factory's safety net, no door ran
        with pytest.raises(allowance.AdmissionRefused) as exc:
            allowance.resolve_for_factory()
        assert exc.value.code == "settings_unavailable"
        with pytest.raises(allowance.AdmissionRefused):
            allowance.admit_from_facts("model_work")


@pytest.mark.asyncio
async def test_f2_rest_door_answers_503_not_instance(env, monkeypatch):
    db, client, acting, person, app = env
    monkeypatch.setattr(settings, "llm_key_mode", "instance")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    ins = _fresh_process(monkeypatch)
    await ins.refresh()
    r = await client.post("/door/cv")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error_code"] == "settings_unavailable"


@pytest.mark.asyncio
async def test_f2_mcp_session_with_a_failed_read_refuses(monkeypatch):
    """The finding's MCP path: ``pinned_call`` swallows the failed refresh, the
    identity revalidates; the tool must still refuse, before its body."""
    from mcp.shared.exceptions import McpError

    from applire.mcp import server

    monkeypatch.setattr(settings, "llm_key_mode", "instance")
    _fresh_process(monkeypatch)
    person = MagicMock(spec=User)
    person.id = uuid.uuid4()
    body = AsyncMock()
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server.job_svc, "analyze_jd", body), patch.object(
        server, "get_db", MagicMock()
    ), patch.object(server, "get_provider", MagicMock(name="factory")) as factory:
        with pytest.raises(McpError) as exc:
            await server.analyze_jd(text="Backend Engineer at Example GmbH")
    assert exc.value.error.message.startswith("settings_unavailable")
    factory.assert_not_called()
    body.assert_not_awaited()


@pytest.mark.asyncio
async def test_f2_a_loaded_process_keeps_its_overrides_and_recovers(monkeypatch):
    """Not over-broad: a process that read the overrides once keeps them through
    a later failure (ADR-093 cl. 6), and a fresh one recovers on its first good
    read. The environment ``user`` is never ``unknown``."""
    monkeypatch.setattr(settings, "llm_key_mode", "instance")
    ins = _fresh_process(monkeypatch)
    ins._state.ever_loaded = True
    await ins.refresh()
    assert allowance.decision_mode() == "instance"

    ins._state.ever_loaded = False
    await ins.refresh()
    assert allowance.decision_mode() == "unknown"

    async def empty_snapshot(db):
        return {}, set()

    class _Session:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ins, "_session_factory", lambda: (lambda: _Session()))
    monkeypatch.setattr(ins, "_build_snapshot", empty_snapshot)
    await ins.refresh()
    assert allowance.decision_mode() == "instance"

    ins._state.ever_loaded, ins._state.load_failed = False, True
    monkeypatch.setattr(settings, "llm_key_mode", "user")
    assert allowance.decision_mode() == "user"


# ── F3: ALLOWANCE_POLICY is not a setting yet ─────────────────────────────────


@pytest.mark.parametrize(
    ("value", "warns"),
    [("allowance", True), ("Trial", True), ("none", False), ("", False), (None, False)],
)
def test_f3_unknown_allowance_policy_warns_at_start(monkeypatch, caplog, value, warns):
    from applire import main

    if value is None:
        monkeypatch.delenv("ALLOWANCE_POLICY", raising=False)
    else:
        monkeypatch.setenv("ALLOWANCE_POLICY", value)
    caplog.set_level(logging.WARNING, logger="applire")
    main._warn_unknown_allowance_policy()
    hits = [r for r in caplog.records if "ALLOWANCE_POLICY" in r.getMessage()]
    assert bool(hits) is warns
    if warns:
        assert "not implemented" in hits[0].getMessage()


# ── F4: PROVIDER_KEY_SECRET strength ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("secret", "ok"),
    [("x", False), ("a" * 31, False), (" " + "a" * 31 + " ", False), ("a" * 32, True), (SECRET, True)],
)
def test_f4_short_secret_counts_as_unset(monkeypatch, secret, ok):
    from applire import main
    from applire.auth import links

    monkeypatch.setattr(settings, "provider_key_secret", secret)
    assert links.provider_key_secret_ok(secret) is ok
    assert provider_keys.secret_configured() is ok
    if ok:
        assert provider_keys.decrypt(provider_keys.encrypt(USER_KEY)) == USER_KEY
    else:
        with pytest.raises(links.ProviderKeySecretMissing):
            provider_keys.encrypt(USER_KEY)
    monkeypatch.setattr(type(settings), "env_value", lambda self, name: "user" if name == "llm_key_mode" else None)
    if ok:
        main._validate_key_mode_env()
    else:
        with pytest.raises(main.KeyModeWithoutSecret, match="32 bytes"):
            main._validate_key_mode_env()


@pytest.mark.asyncio
async def test_f4_short_secret_refuses_the_put_and_the_panel(env, user_mode, monkeypatch, async_db):
    from applire.services import instance_settings as ins

    db, client, *_ = env
    monkeypatch.setattr(settings, "provider_key_secret", "x")
    r = await client.put(
        "/api/me/provider-key", headers=ORIGIN, json={"family": "mistral", "model": "m", "key": USER_KEY}
    )
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "provider_key_secret_missing"
    assert ins._provider_key_secret_set() is False


# ── F5: the operator's SDK environment never redirects a person's key ─────────


def test_f5_anthropic_user_key_ignores_base_url_and_custom_headers(monkeypatch):
    from applire.providers.llm import build_user_provider

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://internal:9")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Operator: secret-routing")
    p = build_user_provider(family="anthropic", model="claude-x", key="sk-ant-user-0123456789")
    assert str(p._client.base_url).startswith("https://api.anthropic.com")
    assert "X-Operator" not in p._client.default_headers


def test_f5_mistral_user_key_names_the_public_endpoint():
    from applire.providers.llm import build_user_provider

    p = build_user_provider(family="mistral", model="mistral-small-latest", key="user-key-0123456789")
    assert p._client.sdk_configuration.get_server_details()[0] == "https://api.mistral.ai"


@pytest.mark.parametrize("family", ["openai", "openrouter", "requesty"])
def test_f5_openai_sdk_families_drop_the_org_and_project_env(monkeypatch, family):
    from applire.providers.llm import build_user_provider

    monkeypatch.setenv("OPENAI_ORG_ID", "org-operator")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-operator")
    monkeypatch.setattr(settings, "openai_base_url", "http://169.254.169.254:80/")
    p = build_user_provider(family=family, model="some/model", key="abcdefgh12345678")
    headers = p._client.default_headers
    assert not isinstance(headers.get("OpenAI-Organization"), str)
    assert not isinstance(headers.get("OpenAI-Project"), str)
    assert "169.254" not in str(p._client.base_url)


# ── F6: the test-before-save call is throttled per person ─────────────────────


class _Boom:
    async def acomplete(self, *a, **k):
        raise Exception("401 Incorrect API key provided")


@pytest.mark.asyncio
async def test_f6_test_calls_are_throttled_per_person(env, user_mode):
    """Probe shape ``test_error_echo_not_in_put_response``, repeated: before,
    every ``test:true`` PUT made a provider call; now the sixth in the window
    answers 429 without one."""
    db, client, acting, person, app = env
    built = MagicMock(return_value=_Boom())
    with patch("applire.providers.llm.build_user_provider", built):
        codes = []
        for _ in range(provider_keys.TEST_CALLS_PER_WINDOW + 1):
            r = await client.put(
                "/api/me/provider-key", headers=ORIGIN,
                json={"family": "mistral", "model": "m", "key": USER_KEY, "test": True},
            )
            codes.append(r.status_code)
    assert codes == [422] * provider_keys.TEST_CALLS_PER_WINDOW + [429]
    assert built.call_count == provider_keys.TEST_CALLS_PER_WINDOW
    assert r.json()["detail"]["error_code"] == "provider_key_test_throttled"
    assert int(r.headers["Retry-After"]) > 0
    assert USER_KEY not in r.text
    # A save without the test is not throttled.
    r = await client.put(
        "/api/me/provider-key", headers=ORIGIN, json={"family": "mistral", "model": "m", "key": USER_KEY}
    )
    assert r.status_code == 200


def test_f6_window_is_per_person_one_at_a_time_and_slides():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert provider_keys.reserve_test_call(a, now=0.0) is None
    assert provider_keys.reserve_test_call(a, now=1.0) is not None  # one in flight
    provider_keys.release_test_call(a)
    for t in range(1, provider_keys.TEST_CALLS_PER_WINDOW):
        assert provider_keys.reserve_test_call(a, now=float(t)) is None
        provider_keys.release_test_call(a)
    assert provider_keys.reserve_test_call(a, now=10.0) is not None
    assert provider_keys.reserve_test_call(b, now=10.0) is None  # another person
    provider_keys.release_test_call(b)
    later = float(provider_keys.TEST_WINDOW_SECONDS) + 0.5
    assert provider_keys.reserve_test_call(a, now=later) is None


@pytest.mark.asyncio
async def test_f6_a_failing_test_releases_its_slot(env, user_mode):
    db, client, *_ = env

    class Raises:
        async def acomplete(self, *a, **k):
            raise RuntimeError("boom")

    with patch("applire.providers.llm.build_user_provider", return_value=Raises()):
        for _ in range(2):
            r = await client.put(
                "/api/me/provider-key", headers=ORIGIN,
                json={"family": "mistral", "model": "m", "key": USER_KEY, "test": True},
            )
            assert r.status_code == 422, r.text


# ── F7: two tabs saving the first key at once ─────────────────────────────────


@pytest.mark.asyncio
async def test_f7_racing_first_save_answers_409_not_500(env, user_mode, monkeypatch):
    """``store`` reads, then inserts on the ``user_id`` PK. The race is staged
    exactly: the other tab's row exists, this request's read missed it."""
    db, client, acting, person, app = env
    person_id = person.id  # the 409 rolls the request's session back (expires it)
    await _store_key(db, person, family="anthropic", model="claude-first")
    monkeypatch.setattr(provider_keys, "load_row", AsyncMock(return_value=None))
    r = await client.put(
        "/api/me/provider-key", headers=ORIGIN,
        json={"family": "mistral", "model": "mistral-second", "key": USER_KEY},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_code"] == "provider_key_conflict"
    assert USER_KEY not in r.text
    monkeypatch.undo()
    monkeypatch.setattr(settings, "llm_key_mode", "user")
    monkeypatch.setattr(settings, "provider_key_secret", SECRET)
    from sqlalchemy import select

    from applire.models.allowance import UserProviderKey

    with owner_context(person_id):
        families = (
            await db.execute(select(UserProviderKey.family).where(UserProviderKey.user_id == person_id))
        ).scalars().all()
    assert families == ["anthropic"]  # the first tab's save stands; the session is usable
