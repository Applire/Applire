# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generic OIDC sign-in, linking and fresh re-authentication (ADR-091 cl. 6, 23; S-1, RD-4, MD-7).

Authorization-code flow with PKCE S256. The ID token is trusted from the TLS
channel to the token endpoint (OIDC Core §3.1.3.7 step 6, ruling RD-4 — no new
crypto dependency), which holds only under the conditions enforced here:

* ``OIDC_ISSUER`` is ``https`` — ``http`` only for the parsed hostnames
  ``localhost``/``127.0.0.1``/``::1`` (never ``startswith``: ``localhost.evil.com``
  is refused). The same rule applies to the discovered authorization and token
  endpoints.
* the discovery document's ``issuer`` equals ``OIDC_ISSUER`` (trailing slash
  normalised);
* every request uses ``verify=True`` and ``follow_redirects=False`` — a 3xx is a
  failure; no insecure switch exists;
* the token request authenticates the client with its (required) secret;
* claims ``iss``, ``aud``/``azp``, ``exp``, ``iat`` (±5 min), ``nonce`` are checked.

Identity is ``(oidc_issuer, oidc_subject)``. An unknown identity binds **only** to
a *pending* invited account whose ``lower(email)`` equals the claim's, when
``email_verified is True`` (strict boolean — Cognito/Entra send the string
``"false"``), in one transaction that also marks the invite used (MD-7, R3-2).

State, nonce and PKCE verifier travel in a signed (HMAC, key
``derive_key("oidc-state")``), httpOnly, 10-minute cookie; each state is accepted
once per process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Response
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.links import derive_key
from applire.config import configured_base_url, settings
from applire.models.auth import AuthLink
from applire.models.user import User

STATE_COOKIE = "applire_oidc_state"
STATE_COOKIE_PATH = "/api/auth/oidc"
STATE_TTL_SECONDS = 600
CLOCK_SKEW_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 10.0
DISCOVERY_TTL_SECONDS = 3600
CALLBACK_PATH = "/api/auth/oidc/callback"
LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
USED_STATES_MAX = 10_000

Intent = Literal["login", "link", "reauth"]


class OidcConfigError(ValueError):
    """The OIDC settings cannot be used; startup refuses (ADR-091 cl. 6)."""


class OidcError(Exception):
    """A protocol or claim check failed (→ ``oidc_failed``). ``reason`` is for logs only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def oidc_enabled() -> bool:
    return bool((settings.oidc_issuer or "").strip())


def normalise_issuer(value: str) -> str:
    return (value or "").strip().rstrip("/")


def configured_issuer() -> str:
    return normalise_issuer(settings.oidc_issuer)


def is_secure_url(url: str) -> bool:
    """``https``, or ``http`` on a loopback hostname — matched on the PARSED hostname."""
    try:
        parts = urlsplit((url or "").strip())
        hostname = parts.hostname
    except ValueError:
        return False
    if not hostname:
        return False
    scheme = parts.scheme.lower()
    if scheme == "https":
        return True
    return scheme == "http" and hostname.lower() in LOOPBACK_HOSTNAMES


def redirect_uri() -> str:
    return settings.applire_base_url.strip().rstrip("/") + CALLBACK_PATH


def validate_config() -> None:
    """Raise :class:`OidcConfigError` when OIDC is on but unusable (lifespan calls this)."""
    if not oidc_enabled():
        return
    if not is_secure_url(configured_issuer()):
        raise OidcConfigError(
            "OIDC_ISSUER must be an https URL (http is allowed only for localhost)."
        )
    if not (settings.oidc_client_id or "").strip():
        raise OidcConfigError("OIDC_ISSUER is set but OIDC_CLIENT_ID is empty.")
    if not (settings.oidc_client_secret or "").strip():
        raise OidcConfigError("OIDC_ISSUER is set but OIDC_CLIENT_SECRET is empty.")
    if configured_base_url() is None:  # S-1: unset = absent or empty, never a value compare
        raise OidcConfigError(
            "OIDC needs APPLIRE_BASE_URL set to the address people open Applire on "
            "(the redirect URI is <APPLIRE_BASE_URL>/api/auth/oidc/callback)."
        )


# ---------------------------------------------------------------------------
# HTTP (one place; tests install a MockTransport via ``_transport``)
# ---------------------------------------------------------------------------

_transport: httpx.AsyncBaseTransport | None = None


def client_kwargs() -> dict[str, Any]:
    """The only way this module talks to the IdP: TLS verified, no redirects."""
    return {"verify": True, "follow_redirects": False, "timeout": HTTP_TIMEOUT_SECONDS}


def _client() -> httpx.AsyncClient:
    kwargs = client_kwargs()
    if _transport is not None:
        kwargs["transport"] = _transport
    return httpx.AsyncClient(**kwargs)


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    token_auth_methods: tuple[str, ...]


_discovery_cache: dict[str, tuple[float, Discovery]] = {}


def clear_caches() -> None:
    _discovery_cache.clear()
    _used_states.clear()


async def discover() -> Discovery:
    issuer = configured_issuer()
    cached = _discovery_cache.get(issuer)
    if cached is not None and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    url = issuer + "/.well-known/openid-configuration"
    try:
        async with _client() as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OidcError(f"discovery request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise OidcError(f"discovery answered {resp.status_code}")
    try:
        doc = resp.json()
    except ValueError as exc:
        raise OidcError("discovery is not JSON") from exc
    if not isinstance(doc, dict):
        raise OidcError("discovery is not an object")
    if normalise_issuer(str(doc.get("issuer") or "")) != issuer:
        raise OidcError("discovery issuer differs from OIDC_ISSUER")
    auth_ep = doc.get("authorization_endpoint")
    token_ep = doc.get("token_endpoint")
    if not isinstance(auth_ep, str) or not is_secure_url(auth_ep):
        raise OidcError("authorization_endpoint missing or not https")
    if not isinstance(token_ep, str) or not is_secure_url(token_ep):
        raise OidcError("token_endpoint missing or not https")
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    found = Discovery(
        issuer=issuer,
        authorization_endpoint=auth_ep,
        token_endpoint=token_ep,
        token_auth_methods=tuple(str(m) for m in methods if isinstance(m, str)),
    )
    _discovery_cache[issuer] = (time.monotonic(), found)
    return found


# ---------------------------------------------------------------------------
# Signed state cookie
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class FlowState:
    state: str
    nonce: str
    verifier: str
    intent: Intent
    next: str = "/"
    uid: str | None = None
    grant_id: str | None = None
    exp: int = field(default_factory=lambda: int(time.time()) + STATE_TTL_SECONDS)


def _mac(payload: str) -> str:
    return _b64(hmac.new(derive_key("oidc-state"), payload.encode("ascii"), hashlib.sha256).digest())


def encode_state(flow: FlowState) -> str:
    body = {
        "s": flow.state, "n": flow.nonce, "v": flow.verifier, "i": flow.intent,
        "x": flow.next, "u": flow.uid, "g": flow.grant_id, "e": flow.exp,
    }
    payload = _b64(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_mac(payload)}"


def decode_state(cookie: str | None, state_param: str | None) -> FlowState:
    """Verify MAC, expiry and the ``state`` query parameter; raise :class:`OidcError`."""
    if not cookie or not state_param or len(cookie) > 4096:
        raise OidcError("state cookie or parameter missing")
    payload, _, mac = cookie.partition(".")
    if not payload or not mac or not hmac.compare_digest(mac, _mac(payload)):
        raise OidcError("state cookie signature invalid")
    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OidcError("state cookie unreadable") from exc
    if not isinstance(body.get("e"), int) or body["e"] < int(time.time()):
        raise OidcError("state expired")
    if not isinstance(body.get("s"), str) or not hmac.compare_digest(body["s"], state_param):
        raise OidcError("state parameter does not match the cookie")
    if body.get("i") not in ("login", "link", "reauth"):
        raise OidcError("unknown intent")
    return FlowState(
        state=body["s"], nonce=body["n"], verifier=body["v"], intent=body["i"],
        next=body.get("x") or "/", uid=body.get("u"), grant_id=body.get("g"), exp=body["e"],
    )


def set_state_cookie(response: Response, flow: FlowState) -> None:
    response.set_cookie(
        STATE_COOKIE, encode_state(flow), max_age=STATE_TTL_SECONDS, path=STATE_COOKIE_PATH,
        httponly=True, samesite="lax", secure=bool(settings.cookie_secure),
    )


def clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        STATE_COOKIE, path=STATE_COOKIE_PATH, httponly=True, samesite="lax",
        secure=bool(settings.cookie_secure),
    )


_used_states: OrderedDict[str, float] = OrderedDict()


def claim_state_once(flow: FlowState) -> None:
    """Accept each state once per process (a replayed callback fails)."""
    now = time.time()
    while _used_states and next(iter(_used_states.values())) < now:
        _used_states.popitem(last=False)
    key = hashlib.sha256(flow.state.encode("utf-8")).hexdigest()
    if key in _used_states:
        raise OidcError("state already used")
    _used_states[key] = float(flow.exp)
    while len(_used_states) > USED_STATES_MAX:
        _used_states.popitem(last=False)


def safe_next(value: str | None) -> str:
    """A same-origin relative path, else ``/`` (contract §3.4)."""
    if not value or len(value) > 512:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    return value


# ---------------------------------------------------------------------------
# Authorization request
# ---------------------------------------------------------------------------


async def begin(
    intent: Intent,
    *,
    next_path: str = "/",
    uid: uuid.UUID | None = None,
    grant_id: uuid.UUID | None = None,
) -> tuple[str, FlowState]:
    """The IdP authorize URL and the flow state to put in the cookie."""
    disc = await discover()
    flow = FlowState(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),
        intent=intent,
        next=safe_next(next_path),
        uid=str(uid) if uid is not None else None,
        grant_id=str(grant_id) if grant_id is not None else None,
    )
    challenge = _b64(hashlib.sha256(flow.verifier.encode("ascii")).digest())
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id.strip(),
        "redirect_uri": redirect_uri(),
        "scope": (settings.oidc_scopes or "openid email profile").strip(),
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if intent == "reauth":
        params["prompt"] = "login"
        params["max_age"] = "0"
    sep = "&" if urlsplit(disc.authorization_endpoint).query else "?"
    return disc.authorization_endpoint + sep + urlencode(params), flow


# ---------------------------------------------------------------------------
# Code exchange + ID-token claims
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claims:
    issuer: str
    subject: str
    email: str | None
    email_verified: Any
    auth_time: int | None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise OidcError("id_token is not a JWS compact serialisation")
    try:
        body = json.loads(_unb64(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OidcError("id_token payload unreadable") from exc
    if not isinstance(body, dict):
        raise OidcError("id_token payload is not an object")
    return body


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_claims(body: dict[str, Any], *, nonce: str, now: float | None = None) -> Claims:
    """ID-token checks (ADR-091 cl. 6; OIDC Core §3.1.3.7 steps 2–5, 9–11)."""
    now = time.time() if now is None else now
    issuer = configured_issuer()
    client_id = settings.oidc_client_id.strip()
    if normalise_issuer(str(body.get("iss") or "")) != issuer:
        raise OidcError("iss differs from OIDC_ISSUER")
    aud = body.get("aud")
    azp = body.get("azp")
    if isinstance(aud, str):
        if aud != client_id:
            raise OidcError("aud is not this client")
    elif isinstance(aud, list) and all(isinstance(a, str) for a in aud):
        if client_id not in aud:
            raise OidcError("aud does not contain this client")
        if len(aud) > 1 and azp is None:
            raise OidcError("multiple audiences without azp")
    else:
        raise OidcError("aud missing")
    if azp is not None and azp != client_id:
        raise OidcError("azp is not this client")
    exp, iat = body.get("exp"), body.get("iat")
    if not _is_int(exp) or exp + CLOCK_SKEW_SECONDS < now:
        raise OidcError("id_token expired")
    if not _is_int(iat) or abs(now - iat) > CLOCK_SKEW_SECONDS:
        raise OidcError("iat outside the allowed skew")
    got_nonce = body.get("nonce")
    if not isinstance(got_nonce, str) or not hmac.compare_digest(got_nonce, nonce):
        raise OidcError("nonce mismatch")
    sub = body.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise OidcError("sub missing")
    email = body.get("email")
    auth_time = body.get("auth_time")
    return Claims(
        issuer=issuer,
        subject=sub,
        email=email if isinstance(email, str) and email else None,
        email_verified=body.get("email_verified"),
        auth_time=auth_time if _is_int(auth_time) else None,
    )


async def exchange_code(code: str | None, flow: FlowState) -> Claims:
    if not code or len(code) > 4096:
        raise OidcError("code missing")
    disc = await discover()
    client_id = settings.oidc_client_id.strip()
    secret = settings.oidc_client_secret.strip()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": flow.verifier,
    }
    auth: httpx.BasicAuth | None = None
    if "client_secret_basic" in disc.token_auth_methods or "client_secret_post" not in disc.token_auth_methods:
        auth = httpx.BasicAuth(client_id, secret)
    else:
        form["client_id"] = client_id
        form["client_secret"] = secret
    try:
        async with _client() as client:
            resp = await client.post(
                disc.token_endpoint, data=form, auth=auth, headers={"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise OidcError(f"token request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise OidcError(f"token endpoint answered {resp.status_code}")
    try:
        token_body = resp.json()
    except ValueError as exc:
        raise OidcError("token response is not JSON") from exc
    id_token = token_body.get("id_token") if isinstance(token_body, dict) else None
    if not isinstance(id_token, str):
        raise OidcError("token response carries no id_token")
    return check_claims(_decode_jwt_payload(id_token), nonce=flow.nonce)


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------


class Outcome:
    """Result codes of :func:`resolve_login` (contract §2 redirect codes)."""

    OK = "ok"
    NO_ACCOUNT = "oidc_no_account"
    DISABLED = "account_disabled"


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def find_bound_user(db: AsyncSession, claims: Claims) -> User | None:
    return (
        await db.execute(
            select(User).where(
                User.oidc_issuer == claims.issuer, User.oidc_subject == claims.subject
            )
        )
    ).scalar_one_or_none()


async def bind_pending_invite(db: AsyncSession, claims: Claims) -> uuid.UUID | None:
    """MD-7 binding: only a pending invited account, only a strictly verified email.

    One transaction (the caller commits on success, this function rolls back on a
    lost race): mark the open invite used, then the atomic ``UPDATE users …``.
    """
    if claims.email_verified is not True or not claims.email:
        return None
    now = _now()
    pending = (
        select(User.id)
        .where(
            func.lower(User.email) == func.lower(claims.email),
            User.password_hash.is_(None),
            User.oidc_subject.is_(None),
            User.last_login_at.is_(None),
            User.disabled_at.is_(None),
            User.deleted_at.is_(None),
        )
    )
    claimed = (
        await db.execute(
            update(AuthLink)
            .where(
                AuthLink.user_id.in_(pending),
                AuthLink.purpose == "invite",
                AuthLink.used_at.is_(None),
                AuthLink.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthLink.user_id, AuthLink.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    user_ids = {row[0] for row in claimed}
    if len(user_ids) != 1:
        if claimed:
            await db.rollback()
        return None
    (user_id,) = user_ids
    try:
        bound = (
            await db.execute(
                update(User)
                .where(
                    and_(
                        User.id == user_id,
                        User.password_hash.is_(None),
                        User.oidc_subject.is_(None),
                        User.last_login_at.is_(None),
                        User.disabled_at.is_(None),
                        User.deleted_at.is_(None),
                    )
                )
                .values(
                    oidc_issuer=claims.issuer,
                    oidc_subject=claims.subject,
                    last_login_at=now,
                    email_verified_at=now,
                )
                .returning(User.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
    except IntegrityError:
        await db.rollback()
        return None
    if bound is None:
        await db.rollback()
        return None
    from applire.services.audit import record

    await record(db, actor_id=bound, action="invite.redeemed", target_type="user",
                 target_id=bound, details={"link_id": claimed[0][1]})
    await record(db, actor_id=bound, action="oidc.linked", target_type="user",
                 target_id=bound, details={"issuer": claims.issuer})
    return bound


async def link_to_user(db: AsyncSession, user_id: uuid.UUID, claims: Claims) -> bool:
    """Link from a signed-in session: the identity must be free, the account unbound."""
    other = await find_bound_user(db, claims)
    if other is not None:
        return other.id == user_id
    try:
        linked = (
            await db.execute(
                update(User)
                .where(User.id == user_id, User.oidc_subject.is_(None), User.deleted_at.is_(None))
                .values(oidc_issuer=claims.issuer, oidc_subject=claims.subject)
                .returning(User.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
    except IntegrityError:
        await db.rollback()
        return False
    if linked is None:
        return False
    from applire.services.audit import record

    await record(db, actor_id=user_id, action="oidc.linked", target_type="user",
                 target_id=user_id, details={"issuer": claims.issuer})
    return True
