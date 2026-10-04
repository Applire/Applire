# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OIDC configuration, discovery, transport and ID-token claim checks
(ADR-091 cl. 6; RD-4; adversarial-security §2a; US323). Fake IdP, no network."""

from __future__ import annotations

import time

import httpx
import pytest

from applire.auth import oidc
from applire.config import settings
from tests.support.fake_idp import CLIENT_ID, ISSUER, FakeIdP, configure


@pytest.fixture
def fake(monkeypatch):
    idp = FakeIdP()
    configure(monkeypatch, idp)
    yield idp
    oidc.clear_caches()


# --- configuration (startup refusal) ------------------------------------------------

@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://idp.example.org", True),
        ("http://localhost:8080/realms/x", True),
        ("http://127.0.0.1:9000", True),
        ("http://[::1]:9000", True),
        ("http://idp.example.org", False),
        ("http://localhost.evil.com", False),   # startswith("http://localhost") would pass
        ("http://localhost@evil.com", False),   # userinfo trick: hostname is evil.com
        ("http://127.0.0.1.evil.com", False),
        ("ftp://localhost", False),
        ("https://", False),
        ("", False),
    ],
)
def test_secure_url_is_matched_on_the_parsed_hostname(url, ok):
    assert oidc.is_secure_url(url) is ok


def test_validate_config_accepts_a_complete_https_setup(fake):
    oidc.validate_config()


def test_validate_config_off_when_issuer_empty(fake, monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", "")
    monkeypatch.setattr(settings, "oidc_client_secret", "")
    oidc.validate_config()
    assert oidc.oidc_enabled() is False


@pytest.mark.parametrize(
    "field, value",
    [
        ("oidc_issuer", "http://idp.example.org"),
        ("oidc_issuer", "http://localhost.evil.com"),
        ("oidc_client_secret", ""),
        ("oidc_client_secret", "   "),
        ("oidc_client_id", ""),
        ("applire_base_url", ""),
        ("applire_base_url", "http://localhost:8001"),  # the shipped default
    ],
)
def test_validate_config_refuses(fake, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(oidc.OidcConfigError):
        oidc.validate_config()


def test_no_insecure_switch_exists():
    names = [n for n in type(settings).model_fields if n.startswith("oidc_")]
    assert sorted(names) == ["oidc_button_label", "oidc_client_id", "oidc_client_secret",
                             "oidc_issuer", "oidc_scopes"]


def test_http_client_verifies_tls_and_never_follows_redirects():
    kw = oidc.client_kwargs()
    assert kw["verify"] is True and kw["follow_redirects"] is False


# --- discovery -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discovery_happy_path_and_trailing_slash(fake, monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER + "/")
    fake.discovery["issuer"] = ISSUER + "/"
    d = await oidc.discover()
    assert d.issuer == ISSUER and d.token_endpoint == f"{ISSUER}/token"


@pytest.mark.asyncio
async def test_discovery_issuer_mismatch_is_refused(fake):
    fake.discovery["issuer"] = "https://other-idp.test"
    with pytest.raises(oidc.OidcError, match="issuer"):
        await oidc.discover()


@pytest.mark.asyncio
async def test_discovery_redirect_is_not_followed(fake):
    # The redirect target serves a perfectly good document: only a client that
    # follows redirects would accept it.
    fake.discovery_response = httpx.Response(302, headers={"Location": "https://idp.test/real-discovery"})
    with pytest.raises(oidc.OidcError, match="302"):
        await oidc.discover()
    assert [str(r.url) for r in fake.requests] == [f"{ISSUER}/.well-known/openid-configuration"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["token_endpoint", "authorization_endpoint"])
async def test_discovery_http_endpoint_is_refused(fake, endpoint):
    fake.discovery[endpoint] = "http://idp.test/x"
    with pytest.raises(oidc.OidcError, match=endpoint):
        await oidc.discover()


@pytest.mark.asyncio
async def test_token_endpoint_redirect_is_not_followed(fake):
    flow = oidc.FlowState(state="s", nonce="n", verifier="v" * 43, intent="login")
    fake.token_response = httpx.Response(307, headers={"Location": "https://idp.test/real-token"})
    with pytest.raises(oidc.OidcError, match="307"):
        await oidc.exchange_code("code", flow)


@pytest.mark.asyncio
async def test_authorize_url_carries_pkce_s256_nonce_and_reauth_prompt(fake):
    url, flow = await oidc.begin("reauth", uid=None)
    q = httpx.URL(url).params
    assert q["code_challenge_method"] == "S256" and q["nonce"] == flow.nonce
    assert q["prompt"] == "login" and q["max_age"] == "0"
    assert q["redirect_uri"] == "http://applire.test/api/auth/oidc/callback"
    url2, _ = await oidc.begin("login")
    assert "prompt" not in httpx.URL(url2).params


# --- ID-token claims -----------------------------------------------------------------

class REL(int):
    """A time offset resolved when the test RUNS (parametrize values are built at
    collection time — a full-suite run starts this test minutes later)."""


def _claims(**over):
    now = int(time.time())
    over = {k: (now + int(v) if isinstance(v, REL) else v) for k, v in over.items()}
    body = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "s", "iat": now, "exp": now + 300, "nonce": "N"}
    for k, v in over.items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


@pytest.mark.parametrize(
    "over",
    [
        {"iss": "https://other.test"},
        {"iss": None},
        {"aud": "someone-else"},
        {"aud": ["someone-else"]},
        {"aud": [CLIENT_ID, "other"]},                       # list > 1 without azp
        {"aud": [CLIENT_ID, "other"], "azp": "other"},
        {"azp": "other"},
        {"aud": None},
        {"exp": REL(-400)},
        {"iat": REL(-400)},
        {"iat": REL(+400)},
        {"exp": True},
        {"nonce": "wrong"},
        {"nonce": None},
        {"sub": ""},
    ],
)
def test_claim_check_refuses(fake, over):
    with pytest.raises(oidc.OidcError):
        oidc.check_claims(_claims(**over), nonce="N")


@pytest.mark.parametrize(
    "over",
    [{}, {"aud": [CLIENT_ID]}, {"aud": [CLIENT_ID, "other"], "azp": CLIENT_ID},
     {"iss": ISSUER + "/"}],
)
def test_claim_check_accepts(fake, over):
    c = oidc.check_claims(_claims(**over), nonce="N")
    assert c.issuer == ISSUER and c.subject == "s"


def test_auth_time_must_be_an_integer(fake):
    assert oidc.check_claims(_claims(auth_time="123"), nonce="N").auth_time is None
    assert oidc.check_claims(_claims(auth_time=True), nonce="N").auth_time is None
    assert oidc.check_claims(_claims(auth_time=123), nonce="N").auth_time == 123


# --- state cookie / next -------------------------------------------------------------

def test_state_cookie_rejects_tampering_expiry_and_param_mismatch(fake):
    flow = oidc.FlowState(state="abc", nonce="n", verifier="v", intent="link", uid="u")
    cookie = oidc.encode_state(flow)
    assert oidc.decode_state(cookie, "abc").uid == "u"
    with pytest.raises(oidc.OidcError):
        oidc.decode_state(cookie, "abd")
    payload, mac = cookie.split(".")
    forged = oidc._b64(oidc._unb64(payload).replace(b'"u":"u"', b'"u":"x"'))
    with pytest.raises(oidc.OidcError):
        oidc.decode_state(f"{forged}.{mac}", "abc")
    old = oidc.FlowState(state="abc", nonce="n", verifier="v", intent="login", exp=int(time.time()) - 1)
    with pytest.raises(oidc.OidcError):
        oidc.decode_state(oidc.encode_state(old), "abc")


@pytest.mark.parametrize(
    "value, expected",
    [("/applications", "/applications"), ("//evil.com", "/"), ("https://evil.com", "/"),
     ("/\\evil.com", "/"), ("evil", "/"), (None, "/"), ("/a\nb", "/")],
)
def test_safe_next(value, expected):
    assert oidc.safe_next(value) == expected
