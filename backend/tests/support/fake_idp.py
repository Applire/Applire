# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Package 1d test support: an in-process fake OIDC provider (no network) and an app
with the OIDC routers mounted on the real ``LocalAuthProvider``.

The fake answers discovery and the token endpoint through ``httpx.MockTransport``
(installed as ``applire.auth.oidc._transport``). It checks what a real IdP checks
— client authentication, the PKCE verifier against the challenge sent to the
authorize URL, the redirect URI — so a regression in the request side fails too.
Codes are reusable on purpose: the replay test must be refused by Applire, not by
the fake.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI, Response

from applire.auth import get_auth_provider
from applire.auth.local import LocalAuthProvider
from applire.auth.sessions import SESSION_COOKIE, issue_session
from applire.db.session import get_db
from applire.models.auth import AuthLink
from applire.models.user import User
from applire.routers import auth_oidc, me_account, me_oidc, me_reauth

ISSUER = "https://idp.test"
CLIENT_ID = "applire-client"
CLIENT_SECRET = "client-secret-value"
BASE_URL = "http://applire.test"
ORIGIN = {"Origin": BASE_URL}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_id_token(claims: dict[str, Any]) -> str:
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    return f"{header}.{_b64(json.dumps(claims).encode())}.{_b64(b'not-checked-trusted-from-tls')}"


class FakeIdP:
    def __init__(self) -> None:
        self.discovery: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        }
        self.discovery_response: httpx.Response | None = None
        self.token_response: httpx.Response | None = None
        self.codes: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.token_requests = 0

    # -- driving -------------------------------------------------------------
    def code_for(self, authorize_url: str, **claims: Any) -> tuple[str, str]:
        """Register a code for the flow behind ``authorize_url``; ``(code, state)``.

        ``claims`` override the defaults; a value ``None`` removes the claim.
        """
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": "sub-1", "email": "invitee@example.org",
            "email_verified": True, "iat": now, "exp": now + 300, "auth_time": now,
            "nonce": q["nonce"],
        }
        for key, value in claims.items():
            if value is None:
                body.pop(key, None)
            else:
                body[key] = value
        code = secrets.token_urlsafe(12)
        self.codes[code] = {"claims": body, "challenge": q["code_challenge"],
                            "redirect_uri": q["redirect_uri"]}
        return code, q["state"]

    # -- transport -----------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return self.discovery_response or httpx.Response(200, json=self.discovery)
        if url == "https://idp.test/real-discovery":
            return httpx.Response(200, json=self.discovery)
        if url == self.discovery["token_endpoint"]:
            self.token_requests += 1
            if self.token_response is not None:
                return self.token_response
            return self._token(request)
        if url == "https://idp.test/real-token":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "invalid_client"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        entry = self.codes.get(form.get("code", ""))
        if entry is None or form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        if _b64(hashlib.sha256(verifier.encode()).digest()) != entry["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant", "why": "pkce"})
        if form.get("redirect_uri") != entry["redirect_uri"]:
            return httpx.Response(400, json={"error": "invalid_grant", "why": "redirect_uri"})
        return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer",
                                         "id_token": make_id_token(entry["claims"])})


def build_app(db) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_oidc.router)
    app.include_router(me_oidc.router)
    app.include_router(me_reauth.router)
    app.include_router(me_account.router)

    async def _db():
        yield db

    async def _provider():
        return LocalAuthProvider()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def add_person(
    db, *, email: str, password_hash: str | None = None, pending: bool = False,
    invite_expires_in: timedelta | None = timedelta(days=7),
    oidc_subject: str | None = None, disabled: bool = False,
) -> User:
    """A person; ``pending=True`` = invited, no credential, never signed in (+ invite link)."""
    user = User(id=uuid.uuid4(), email=email, role="user")
    user.password_hash = password_hash
    if oidc_subject is not None:
        user.oidc_issuer, user.oidc_subject = ISSUER, oidc_subject
    if not pending:
        user.last_login_at = _now()
    if disabled:
        user.disabled_at = _now()
    db.add(user)
    if pending and invite_expires_in is not None:
        db.add(AuthLink(user_id=user.id, purpose="invite",
                        token_hash=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
                        expires_at=_now() + invite_expires_in))
    await db.commit()
    return user


async def sign_in(db, client: httpx.AsyncClient, user: User) -> str:
    """Issue a real session for ``user`` and put its cookie in ``client``'s jar."""
    resp = Response()
    await issue_session(db, user, resp)
    await db.commit()
    raw = resp.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set(SESSION_COOKIE, raw, domain="applire.test", path="/")
    return raw


@asynccontextmanager
async def no_cookie(client: httpx.AsyncClient, name: str):
    saved = client.cookies.get(name)
    client.cookies.delete(name)
    try:
        yield
    finally:
        if saved is not None:
            client.cookies.set(name, saved, domain="applire.test", path="/")


def configure(monkeypatch, fake: FakeIdP) -> None:
    """OIDC settings, instance secret and the fake transport (undone by monkeypatch)."""
    from applire.auth import links, oidc
    from applire.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(settings, "oidc_scopes", "openid email profile")
    monkeypatch.setattr(settings, "applire_base_url", BASE_URL)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(links, "_instance_secret", b"1d-test-instance-secret-32-bytes")
    monkeypatch.setattr(oidc, "_transport", httpx.MockTransport(fake.handler))
    oidc.clear_caches()
