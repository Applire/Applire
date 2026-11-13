# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 1 (Sorbet WP-K) — the payer decision, the factory, the key at rest.

Each guard named in WORK-PACKAGES WP-K's evidence contract has a test here that
fails when the guard is removed (mutation kills recorded in K/report.md):

* admission refusal — the truth table (rows 1, 3, 4, 7);
* no silent fallback — ``strict_key`` on every keyed constructor, the factory's
  user path, ``AdmissionMissing`` for an owner without facts in ``user`` mode,
  an unreadable or unset-secret key never becoming an instance call;
* key decryption — the key purpose rooted in ``PROVIDER_KEY_SECRET``, never in
  the instance secret a dump carries;
* latches — a user-paid provider neither reads nor writes one;
* redaction — the decision's key is scrubbed.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest

from applire import ownership
from applire.auth import links
from applire.config import settings
from applire.providers.llm import latches
from applire.services import allowance, provider_keys

APPLIRE = Path(__file__).resolve().parents[2] / "applire"
USER_KEY = "sk-user-ABCDEFGHIJKLMNOP-0123456789"
INSTANCE_KEY = "sk-instance-ZYXWVUTSRQPONM-9876543210"
SECRET = "provider-key-secret-for-tests-0123456789abcdef"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "provider_key_secret", SECRET)
    return SECRET


@pytest.fixture
def user_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_key_mode", "user")


@pytest.fixture(autouse=True)
def _clean_context():
    allowance.reset_context_for_tests()
    allowance._RECENT_KEYS.clear()
    latches.reset_latches()
    yield
    allowance.reset_context_for_tests()
    allowance._RECENT_KEYS.clear()
    latches.reset_latches()


def _facts(owner, *, mode="user", granted=False, family=None, model=None, key=None):
    ct = provider_keys.encrypt(key) if key else None
    return allowance.PayerFacts(
        owner_id=owner, mode=mode, granted=granted, family=family, model=model, ciphertext=ct
    )


# ── 1. the truth table (cl. 5, policy none) ────────────────────────────────────


def test_row_1_instance_mode_pays_instance_whatever_the_person_has(secret):
    owner = uuid.uuid4()
    for facts in (
        allowance.PayerFacts(owner_id=owner, mode="instance"),
        _facts(owner, mode="instance", granted=True, family="mistral", model="m", key=USER_KEY),
    ):
        decision = allowance.decide(facts, "model_work")
        assert decision.payer == "instance" and decision.key is None


def test_row_3_a_grant_pays_instance_even_with_a_stored_key(secret):
    owner = uuid.uuid4()
    decision = allowance.decide(
        _facts(owner, granted=True, family="anthropic", model="m", key=USER_KEY), "model_work"
    )
    assert decision.payer == "instance" and decision.key is None


def test_row_4_a_stored_key_pays_user_with_that_key_and_model(secret):
    owner = uuid.uuid4()
    decision = allowance.decide(
        _facts(owner, family="requesty", model="openai/gpt-x", key=USER_KEY), "model_work"
    )
    assert (decision.payer, decision.family, decision.model, decision.key) == (
        "user", "requesty", "openai/gpt-x", USER_KEY
    )
    assert USER_KEY not in repr(decision)


def test_row_7_neither_key_nor_grant_is_refused_never_instance(secret):
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(_facts(uuid.uuid4()), "model_work")
    assert exc.value.code == "provider_key_required"
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "provider_key_required"


def test_a_key_that_no_longer_decrypts_reads_as_unset(monkeypatch, secret):
    owner = uuid.uuid4()
    facts = _facts(owner, family="mistral", model="m", key=USER_KEY)
    monkeypatch.setattr(settings, "provider_key_secret", "a-different-secret-entirely-000")
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "provider_key_required"


def test_an_unset_secret_refuses_unreadable_never_instance(monkeypatch, secret):
    facts = _facts(uuid.uuid4(), family="mistral", model="m", key=USER_KEY)
    monkeypatch.setattr(settings, "provider_key_secret", "")
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "provider_key_unreadable"


def test_a_stored_family_outside_the_fixed_endpoint_set_reads_as_unset(secret):
    facts = _facts(uuid.uuid4(), family="ollama", model="m", key=USER_KEY)
    with pytest.raises(allowance.AdmissionRefused):
        allowance.decide(facts, "model_work")


# ── 2. the factory (cl. 4) ─────────────────────────────────────────────────────


def _concrete(provider):
    from applire.providers.llm import unwrap_provider

    return unwrap_provider(provider)


def test_instance_mode_builds_todays_provider_and_marks_the_payer(monkeypatch):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    with ownership.owner_context(uuid.uuid4()):
        provider = _concrete(get_provider())
    assert type(provider).__name__ == "MockLLMProvider"
    assert provider._payer == "instance"


@pytest.mark.parametrize("family", ["mistral", "openrouter", "requesty", "anthropic", "openai"])
def test_a_user_decision_builds_the_users_family_key_and_model_never_settings(
    monkeypatch, family
):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, f"{family}_api_key", INSTANCE_KEY)
    monkeypatch.setattr(settings, "openai_base_url", "http://operator-local:1234/v1")
    monkeypatch.setattr(settings, "openrouter_base_url", "http://operator-local:1234/v1")
    monkeypatch.setattr(settings, "requesty_base_url", "http://operator-local:1234/v1")
    owner = uuid.uuid4()
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=None)))

    with ownership.owner_context(owner):
        allowance._decision.set(
            allowance.PayerDecision("user", "model_work", owner, family=family, model="u-model", key=USER_KEY)
        )
        import applire.providers.llm.anthropic  # noqa: F401 — bind the module before patching its name

        with patch("openai.AsyncOpenAI", side_effect=capture), patch(
            "applire.providers.llm.anthropic.AsyncAnthropic", side_effect=capture
        ), patch("applire.providers.llm.mistral.Mistral", side_effect=capture):
            provider = _concrete(get_provider())
    assert provider._payer == "user"
    assert provider._model == "u-model"
    assert captured["api_key"] == USER_KEY
    assert INSTANCE_KEY not in str(captured)
    # S0-11: never the operator's base URL for a user key.
    assert "operator-local" not in str(captured.get("base_url", ""))


@pytest.mark.parametrize(
    "module,cls",
    [
        ("mistral", "MistralProvider"),
        ("openrouter", "OpenRouterProvider"),
        ("requesty", "RequestyProvider"),
        ("anthropic", "AnthropicProvider"),
        ("openai", "OpenAIProvider"),
    ],
)
@pytest.mark.parametrize("empty", ["", "   ", None])
def test_every_keyed_constructor_with_strict_key_refuses_an_empty_key(monkeypatch, module, cls, empty):
    """The constructor enumeration (cl. 4): the `or settings.<family>_api_key`
    fallback is unreachable with strict_key, whatever the instance key holds."""
    import importlib

    monkeypatch.setattr(settings, f"{module}_api_key", INSTANCE_KEY)
    provider_cls = getattr(importlib.import_module(f"applire.providers.llm.{module}"), cls)
    with pytest.raises(ValueError, match="strict_key"):
        provider_cls(api_key=empty, model="m", strict_key=True)


def test_the_five_keyed_constructors_are_exactly_the_user_key_families():
    from applire.models.allowance import USER_KEY_FAMILIES
    from applire.providers.llm import USER_KEY_BASE_URLS

    assert set(USER_KEY_BASE_URLS) == set(USER_KEY_FAMILIES)
    keyed = set()
    for path in (APPLIRE / "providers" / "llm").glob("*.py"):
        if path.stem == "base":  # defines the helper
            continue
        if "_api_key" in path.read_text("utf-8") and "require_strict_key(" in path.read_text("utf-8"):
            keyed.add(path.stem)
    assert keyed == set(USER_KEY_FAMILIES)


def test_the_factory_never_imports_the_legacy_mistral_provider():
    tree = ast.parse((APPLIRE / "providers" / "llm" / "__init__.py").read_text("utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "applire.providers.mistral" not in modules


def test_build_user_provider_refuses_an_empty_key_before_any_constructor():
    from applire.providers.llm import build_user_provider

    with patch("applire.providers.llm.mistral.MistralProvider.__init__", side_effect=AssertionError):
        with pytest.raises(ValueError):
            build_user_provider(family="mistral", model="m", key="")


def test_build_user_provider_refuses_ollama_and_mock():
    from applire.providers.llm import build_user_provider

    for family in ("ollama", "mock", ""):
        with pytest.raises(ValueError):
            build_user_provider(family=family, model="m", key=USER_KEY)


@pytest.mark.no_owner_context
def test_no_owner_raises_owner_context_missing(monkeypatch):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    with pytest.raises(ownership.OwnerContextMissing):
        get_provider()


@pytest.mark.no_owner_context
@pytest.mark.parametrize("reason", ["ops-aggregate", "tooling"])
def test_llm_system_reasons_pay_as_system(monkeypatch, reason):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "llm_key_mode", "user")
    with ownership.unscoped(reason):
        provider = _concrete(get_provider())
    assert provider._payer == "system"


@pytest.mark.no_owner_context
@pytest.mark.parametrize("reason", ["retention", "admin-metadata", "startup-backfill", "migration"])
def test_other_unscoped_reasons_cannot_start_model_work(monkeypatch, reason):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    with ownership.unscoped(reason):
        with pytest.raises(ownership.OwnerContextMissing):
            get_provider()


def test_no_production_code_declares_the_tooling_reason():
    """`tooling` pays as system; it must stay out of every request path."""
    hits = []
    for path in APPLIRE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "unscoped"
                and any(isinstance(a, ast.Constant) and a.value == "tooling" for a in node.args)
            ):
                hits.append(f"{path.relative_to(APPLIRE)}:{node.lineno}")
    assert hits == []


def test_user_mode_owner_without_facts_is_refused_not_paid_by_the_instance(monkeypatch, user_mode):
    """No silent fallback: work that reached the factory without an auth door."""
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    with ownership.owner_context(uuid.uuid4()):
        with pytest.raises(allowance.AdmissionRefused) as exc:
            get_provider()
    assert exc.value.code == "provider_key_required"


def test_safety_net_decides_from_the_owners_facts(monkeypatch, user_mode, secret):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    owner = uuid.uuid4()
    with ownership.owner_context(owner):
        allowance._facts.set(_facts(owner))
        with pytest.raises(allowance.AdmissionRefused):
            get_provider()
        allowance._facts.set(_facts(owner, granted=True))
        assert _concrete(get_provider())._payer == "instance"


def test_another_owners_facts_are_never_used(monkeypatch, user_mode, secret):
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    with ownership.owner_context(uuid.uuid4()):
        allowance._facts.set(_facts(uuid.uuid4(), granted=True))
        with pytest.raises(allowance.AdmissionRefused):
            get_provider()


def test_a_decision_never_pays_for_another_owner(monkeypatch):
    """SF-ALLOW.2: A's decision in B's owner context raises."""
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    a, b = uuid.uuid4(), uuid.uuid4()
    allowance._decision.set(allowance.PayerDecision("instance", "model_work", a))
    with ownership.owner_context(b):
        with pytest.raises(allowance.DecisionOwnerMismatch):
            get_provider()


def test_a_started_action_keeps_its_decision_when_the_mode_changes(monkeypatch, secret):
    """cl. 4 / SF-ALLOW.13: never re-admitted."""
    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    owner = uuid.uuid4()
    with ownership.owner_context(owner):
        allowance._facts.set(allowance.PayerFacts(owner_id=owner, mode="instance"))
        allowance.admit_from_facts("model_work")
        monkeypatch.setattr(settings, "llm_key_mode", "user")
        assert _concrete(get_provider())._payer == "instance"


@pytest.mark.asyncio
async def test_the_decision_travels_into_tasks_created_by_the_action(monkeypatch, secret):
    import asyncio

    from applire.providers.llm import get_provider

    monkeypatch.setattr(settings, "llm_provider", "mock")
    owner = uuid.uuid4()
    with ownership.owner_context(owner):
        allowance._decision.set(
            allowance.PayerDecision("user", "model_work", owner, family="mistral", model="m", key=USER_KEY)
        )

        async def background():
            with patch("applire.providers.llm.mistral.Mistral"):
                return _concrete(get_provider())._payer

        assert await asyncio.create_task(background()) == "user"


# ── 3. the key at rest (cl. 3, SF-ALLOW.7) ─────────────────────────────────────


def test_round_trip_and_the_ciphertext_holds_no_key(secret):
    token = provider_keys.encrypt(USER_KEY)
    assert USER_KEY not in token
    assert provider_keys.decrypt(token) == USER_KEY


def test_the_user_key_purpose_is_rooted_in_the_env_secret_not_the_instance_secret(secret):
    links.set_instance_secret("instance-secret-from-the-dump")
    try:
        token = provider_keys.encrypt(USER_KEY)
        # A dump holds the instance secret. Every key derived from it fails.
        from cryptography.fernet import Fernet, InvalidToken
        import base64
        import hashlib
        import hmac as _hmac

        for purpose in ("user-provider-keys", "instance-settings", "doc-link"):
            raw = _hmac.new(
                b"instance-secret-from-the-dump", f"applire/{purpose}".encode(), hashlib.sha256
            ).digest()
            with pytest.raises(InvalidToken):
                Fernet(base64.urlsafe_b64encode(raw)).decrypt(token.encode())
    finally:
        links.set_instance_secret(None)


def test_a_dump_without_the_env_secret_decrypts_no_key(monkeypatch, secret):
    links.set_instance_secret("instance-secret-from-the-dump")
    try:
        token = provider_keys.encrypt(USER_KEY)
        monkeypatch.setattr(settings, "provider_key_secret", "")
        with pytest.raises(links.ProviderKeySecretMissing):
            provider_keys.decrypt(token)
        with pytest.raises(links.ProviderKeySecretMissing):
            provider_keys.encrypt(USER_KEY)  # never stored in plain text either
    finally:
        links.set_instance_secret(None)


def test_both_new_key_purposes_are_in_the_closed_set():
    assert {"user-provider-keys", "allowance-subject"} <= set(links.KEY_PURPOSES)
    assert links.ENV_ROOTED_PURPOSES == {"user-provider-keys"}


def test_backup_script_never_archives_the_env_file():
    """cl. 3: PROVIDER_KEY_SECRET lives in .env; no Core backup script packs it."""
    repo = APPLIRE.parents[1]
    scripts = [p for p in repo.rglob("backup*.sh") if "node_modules" not in p.parts]
    for script in scripts:
        text = script.read_text("utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "tar " in stripped or "restic backup" in stripped:
                assert ".env" not in stripped.replace(".env.example", ""), (script, line)


# ── 4. latches (cl. 12, SF-ALLOW.8) ────────────────────────────────────────────


def _bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.BadRequestError(message=message, response=httpx.Response(400, request=request), body=None)


def _ok():
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="ok"))],
        usage=None,
    )


async def _mandatory_reasoning_call(provider):
    async def create(**kwargs):
        if (kwargs.get("extra_body") or {}).get("reasoning", {}).get("enabled") is False:
            raise _bad_request("Reasoning is mandatory for this endpoint and cannot be disabled")
        return _ok()

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    await provider.acomplete("q")


def _openrouter(payer: str):
    from applire.providers.llm import _stamp_payer
    from applire.providers.llm.openrouter import OpenRouterProvider

    with patch("openai.AsyncOpenAI"):
        provider = OpenRouterProvider(api_key="k", model="z-ai/glm", disable_thinking=True)
    _stamp_payer(provider, payer)
    return provider


@pytest.mark.asyncio
async def test_a_failing_user_paid_call_leaves_the_latch_state_unchanged():
    before = set(latches._LATCHES)
    await _mandatory_reasoning_call(_openrouter("user"))
    assert latches._LATCHES == before


@pytest.mark.asyncio
async def test_the_same_failure_on_the_instance_key_latches():
    await _mandatory_reasoning_call(_openrouter("instance"))
    assert latches._LATCHES  # the control: instance payers keep #688's latch


def test_a_user_paid_provider_does_not_read_the_instance_latch():
    instance = _openrouter("instance")
    instance._json_schema_rejected = True
    assert _openrouter("instance")._json_schema_rejected is True
    assert _openrouter("user")._json_schema_rejected is False


# ── 5. redaction (cl. 3, SF-ALLOW.6) ───────────────────────────────────────────


def test_the_decisions_key_is_scrubbed_from_text_and_logs(caplog):
    from applire.redaction import SecretRedactionFilter, scrub_secrets

    owner = uuid.uuid4()
    allowance._decision.set(
        allowance.PayerDecision("user", "model_work", owner, family="mistral", model="m", key=USER_KEY)
    )
    text = f"401 Unauthorized: invalid key {USER_KEY!r} for header Authorization"
    assert USER_KEY not in scrub_secrets(text)
    record = logging.LogRecord("applire", logging.ERROR, __file__, 1, "%s", (text,), None)
    SecretRedactionFilter().filter(record)
    assert USER_KEY not in record.getMessage()


def test_a_recently_decrypted_key_is_scrubbed_outside_its_context(secret):
    from applire.redaction import scrub_secrets

    owner = uuid.uuid4()
    allowance.decide(_facts(owner, family="mistral", model="m", key=USER_KEY), "model_work")
    allowance.reset_context_for_tests()  # another request's context
    assert USER_KEY not in scrub_secrets(f"echo {USER_KEY}")


def test_a_key_under_test_is_scrubbed():
    from applire.redaction import scrub_secrets, user_key_in_scope

    key = "plain-looking-key-without-a-known-shape-123"
    assert key in scrub_secrets(f"x {key}")
    with user_key_in_scope(key):
        assert key not in scrub_secrets(f"x {key}")


# ── 6. subject and ledger (cl. 8–10) ───────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b",
    [
        ("Anna.Muster+jobs@GMail.com", "annamuster@gmail.com"),
        ("anna.muster@googlemail.com", "annamuster@gmail.com"),
        ("Max+x@Example.ORG", "max@example.org"),
        ("first.last@example.org", "first.last@example.org"),
    ],
)
def test_normalisation_follows_adr_097_cl_4(a, b):
    assert allowance.normalise_email(a) == b


def test_the_subject_is_an_hmac_under_the_instance_secret_and_holds_no_address():
    links.set_instance_secret("instance-secret-a")
    try:
        one = allowance.subject_hash("Anna.Muster+x@gmail.com")
        assert one == allowance.subject_hash("annamuster@googlemail.com")
        assert "anna" not in one and len(one) == 64
        links.set_instance_secret("instance-secret-b")
        assert allowance.subject_hash("annamuster@gmail.com") != one  # cl. 9: regenerated secret
    finally:
        links.set_instance_secret(None)


@pytest.mark.asyncio
async def test_grant_is_idempotent_per_ref_and_balance_drops_expired_grants(async_db):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    subject = "s" * 64
    with ownership.unscoped("tooling"):
        assert await allowance.grant(
            async_db, subject=subject, action="application", amount=5, source="subscription",
            expires_at=now + timedelta(days=30), ref="monthly:2026-12",
        )
        assert not await allowance.grant(
            async_db, subject=subject, action="application", amount=5, source="subscription",
            expires_at=now + timedelta(days=30), ref="monthly:2026-12",
        )
        await allowance.grant(
            async_db, subject=subject, action="application", amount=3, source="subscription",
            expires_at=now - timedelta(days=1), ref="monthly:2026-11",
        )
        await allowance.grant(
            async_db, subject=subject, action="application", amount=2, source="purchase", ref="pack:1"
        )
        await async_db.commit()
        assert await allowance.balance(async_db, subject=subject, action="application", now=now) == 7
        assert await allowance.balance(async_db, subject=subject, action="profile_import", now=now) == 0
    with pytest.raises(ValueError):
        await allowance.grant(async_db, subject=subject, action="model_work", amount=1, source="x")


# ── 7. startup and the panel (cl. 3) ──────────────────────────────────────────


def test_env_user_mode_without_secret_stops_the_boot(monkeypatch):
    from applire import main

    monkeypatch.setattr(settings, "provider_key_secret", "")
    monkeypatch.setattr(type(settings), "env_value", lambda self, name: "user" if name == "llm_key_mode" else None)
    with pytest.raises(main.KeyModeWithoutSecret):
        main._validate_key_mode_env()
    monkeypatch.setattr(settings, "provider_key_secret", SECRET)
    main._validate_key_mode_env()


def test_an_unknown_key_mode_value_stops_the_start_rather_than_paying_for_everyone():
    from applire.config import InvalidSettingError, Settings

    with pytest.raises(InvalidSettingError):
        Settings(database_url="sqlite://", llm_key_mode="users")
    assert Settings(database_url="sqlite://", llm_key_mode=" USER ").llm_key_mode == "user"
    assert Settings(database_url="sqlite://", llm_key_mode="").llm_key_mode == "instance"


def test_the_default_is_instance_and_the_secret_is_env_only():
    from applire import settings_registry as registry

    assert registry.get("LLM_KEY_MODE").default == "instance"
    assert "LLM_KEY_MODE" in registry.PANEL_KEYS
    assert "PROVIDER_KEY_SECRET" not in registry.PANEL_KEYS
    assert registry.get("PROVIDER_KEY_SECRET").secret is True


@pytest.mark.asyncio
async def test_the_output_cap_probe_never_uses_the_instance_model_for_a_user_paid_action(monkeypatch):
    from applire.providers.llm import capabilities

    monkeypatch.setattr(settings, "llm_max_output_tokens", 0)
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    calls: list = []

    async def probe(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(max_output_tokens=4096)

    monkeypatch.setattr(capabilities, "probe_model_capabilities", probe)
    owner = uuid.uuid4()
    allowance._decision.set(
        allowance.PayerDecision("user", "model_work", owner, family="openrouter", model="m", key=USER_KEY)
    )
    assert await capabilities.resolve_effective_output_cap() == 0
    assert calls == []
    allowance._decision.set(allowance.PayerDecision("instance", "model_work", owner))
    assert await capabilities.resolve_effective_output_cap() == 4096
