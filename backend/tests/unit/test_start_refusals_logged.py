# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every configuration the start refuses names its setting in the log
(adv/plans re-probe N4; the allowance refusal's fix e85e1f46 for its siblings).

The log boundary (``applire.log_boundary``) keeps only the static head of a
record that code outside ``applire`` writes, so uvicorn's startup traceback
reaches the container log as "Traceback (most recent call last)" and an error
id. A refusal whose text lives only in the exception therefore stopped the
start without saying why. These tests run the real lifespan through the real
boundary and read what an operator reads.
"""

from __future__ import annotations

import io
import logging

import pytest

from applire.config import settings


@pytest.fixture
def operator_log():
    from applire import log_boundary

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log_boundary.install(handler)
    logger = logging.getLogger("applire")
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


async def _start(monkeypatch) -> None:
    from applire import main

    def no_migration(*a, **k):  # pragma: no cover - reached only if a refusal is missing
        raise AssertionError("the start went past its configuration checks")

    monkeypatch.setattr(main.subprocess, "run", no_migration)
    async with main.lifespan(main.app):
        pass  # pragma: no cover


@pytest.mark.asyncio
async def test_key_mode_user_without_a_secret_names_the_secret_in_the_log(monkeypatch, operator_log):
    monkeypatch.setattr(settings, "oidc_issuer", "")
    monkeypatch.setattr(settings, "provider_key_secret", "")
    real_env_value = type(settings).env_value
    monkeypatch.setattr(
        type(settings), "env_value",
        lambda self, name: "user" if name == "llm_key_mode" else real_env_value(self, name),
    )
    from applire import main

    with pytest.raises(main.KeyModeWithoutSecret):
        await _start(monkeypatch)
    out = operator_log.getvalue()
    assert "Start refused" in out and "PROVIDER_KEY_SECRET" in out and "LLM_KEY_MODE=user" in out, out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "names"),
    [
        ("oidc_issuer", "http://idp.example.org", "OIDC_ISSUER"),
        ("oidc_client_id", "", "OIDC_CLIENT_ID"),
        ("oidc_client_secret", "", "OIDC_CLIENT_SECRET"),
    ],
)
async def test_an_unusable_oidc_setup_names_the_setting_in_the_log(monkeypatch, operator_log, field, value, names):
    from applire.auth.oidc import OidcConfigError

    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.org")
    monkeypatch.setattr(settings, "oidc_client_id", "applire")
    monkeypatch.setattr(settings, "oidc_client_secret", "s3cret-not-logged")
    monkeypatch.setattr(settings, "applire_base_url", "https://applire.example.org")
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(OidcConfigError):
        await _start(monkeypatch)
    out = operator_log.getvalue()
    assert "Start refused" in out and names in out, out
    assert "s3cret-not-logged" not in out


@pytest.mark.asyncio
async def test_the_allowance_refusal_still_names_its_fix_through_the_lifespan(monkeypatch, operator_log):
    from applire.services.ops import config as ops_config

    monkeypatch.setattr(settings, "oidc_issuer", "")
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 1000)
    monkeypatch.setattr(settings, "embedding_provider", "noop")
    monkeypatch.setattr(ops_config, "LLM_USAGE_TRACKING", "off")
    with pytest.raises(RuntimeError):
        await _start(monkeypatch)
    out = operator_log.getvalue()
    assert out.count("Start refused") == 1, out
    assert "LLM_USAGE_TRACKING" in out, out


@pytest.mark.asyncio
async def test_an_unknown_auth_provider_names_the_setting_in_the_log(monkeypatch, operator_log):
    """adv/plans re-probe 2, S2: ``AUTH_PROVIDER=bogus`` stopped the start with
    only the traceback head."""
    monkeypatch.setattr(settings, "oidc_issuer", "")
    monkeypatch.setattr(settings, "auth_provider", "bogus")
    with pytest.raises(ValueError):
        await _start(monkeypatch)
    out = operator_log.getvalue()
    assert "Start refused" in out and "AUTH_PROVIDER" in out and "bogus" in out, out
    assert out.count("Start refused") == 1
