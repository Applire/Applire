# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Personal tokens — agent, api, probe (ADR-091 cl. 17; frozen interface F3).

Format ``apl_<prefix8>_<43 b64url>``: the prefix is 8 characters of ``[a-z0-9]``
(it never contains the separator, so parsing is positional and unambiguous) and
the secret is 32 random bytes in unpadded base64url. ``personal_tokens`` stores
the **sha256 of the whole token** — never the token. Lookup is by the unique
prefix, then ``hmac.compare_digest`` on the hash (adversarial-security §S
"agent token storage": HOLDS).

Scopes are explicit and never widen (S-5, RD-10):

=========  ===========================================================
``agent``  the stdio MCP door only — **never** accepted over HTTP
``api``    ``Authorization: Bearer`` on the REST API with the owner's role,
           except the credential-management routes (session only)
``probe``  ``GET /api/ops/health`` only (created by an admin)
=========  ===========================================================

Validity — not revoked, the owner neither disabled nor deleted — is re-checked
on **every** call (MD-3): nothing here caches a positive answer across requests.
``last_used_at`` is written at most once a minute.

The request helpers at the bottom are what ``auth/deps.py`` and ``auth/csrf.py``
(package 1a) build on, so the rule *"if an ``Authorization`` header is present
only the bearer is evaluated, and only a valid ``api`` bearer is exempt from the
origin check"* has one implementation (ADR-091 cl. 12, adversarial-security §3).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

    from applire.models.auth import PersonalToken

logger = logging.getLogger(__name__)

TokenScope = Literal["agent", "api", "probe"]
TOKEN_SCOPES: tuple[TokenScope, ...] = ("agent", "api", "probe")
TOKEN_PREFIX = "apl_"

_PREFIX_ALPHABET = string.ascii_lowercase + string.digits
_PREFIX_LEN = 8
_SECRET_BYTES = 32  # -> 43 unpadded base64url characters
_TOKEN_RE = re.compile(r"^apl_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
_LAST_USED_GRANULARITY = timedelta(minutes=1)
_PREFIX_RETRIES = 5

#: Compared against when the prefix is unknown, so an unknown prefix costs the
#: same hash + compare as a known one (no timing oracle on prefix existence).
_DUMMY_HASH = hashlib.sha256(b"applire-dummy-token").hexdigest()


class InvalidToken(Exception):
    """Missing, malformed, unknown, revoked, wrong-scope, or owner inactive.

    One exception for every cause — the caller never learns which (no oracle).
    """


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    """sha256 hex of the whole token string (what ``personal_tokens.token_hash`` holds)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_token(raw: str | None) -> str | None:
    """The 8-character prefix of a well-formed token, else ``None``."""
    if not raw or not isinstance(raw, str):
        return None
    match = _TOKEN_RE.match(raw)
    return match.group(1) if match else None


def _new_prefix() -> str:
    return "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(_PREFIX_LEN))


def generate_token(prefix: str | None = None) -> tuple[str, str, str]:
    """``(raw, prefix, token_hash)`` for a fresh token. ``raw`` is shown once."""
    prefix = prefix or _new_prefix()
    raw = f"{TOKEN_PREFIX}{prefix}_{secrets.token_urlsafe(_SECRET_BYTES)}"
    assert _TOKEN_RE.match(raw), "token format drifted"
    return raw, prefix, hash_token(raw)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes for DateTime(timezone=True) columns.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def user_is_active(user: User | None) -> bool:
    """Neither disabled nor deleted (ADR-091 cl. 8)."""
    if user is None:
        return False
    return getattr(user, "disabled_at", None) is None and user.deleted_at is None


# ---------------------------------------------------------------------------
# Resolution (MD-3: every call re-checks)
# ---------------------------------------------------------------------------


async def _resolve(db: AsyncSession, raw: str | None, scope: TokenScope) -> User | None:
    from applire.models.auth import PersonalToken

    if scope not in TOKEN_SCOPES:
        raise ValueError(f"unknown token scope {scope!r}")
    prefix = parse_token(raw)
    if prefix is None:
        return None
    row = (
        await db.execute(select(PersonalToken).where(PersonalToken.prefix == prefix))
    ).scalar_one_or_none()
    presented = hash_token(raw)  # type: ignore[arg-type]
    stored = row.token_hash if row is not None else _DUMMY_HASH
    if not hmac.compare_digest(presented, stored) or row is None:
        return None
    if row.scope != scope or row.revoked_at is not None:
        return None
    user = await db.get(User, row.user_id, populate_existing=True)
    if not user_is_active(user):
        return None
    now = _now()
    last = _aware(row.last_used_at)
    if last is None or now - last >= _LAST_USED_GRANULARITY:
        await db.execute(
            update(PersonalToken)
            .where(PersonalToken.id == row.id)
            .values(last_used_at=now)
        )
        await db.commit()
    return user


async def resolve_agent_token(db: AsyncSession, raw: str) -> User:
    """The live user owning the ``agent``-scope token ``raw``, else ``InvalidToken``.

    Used by ``mcp/__main__.py`` at start (exit 1 on failure) and per tool call
    (``mcp/identity.py``, MD-3).
    """
    user = await _resolve(db, raw, "agent")
    if user is None:
        raise InvalidToken("invalid agent token")
    return user


async def resolve_bearer(db: AsyncSession, raw: str, scope: TokenScope) -> User | None:
    """The live user owning ``raw`` if it is a valid token **of ``scope``**, else ``None``.

    ``scope`` is ``"api"`` for REST bearer auth and ``"probe"`` for the ops route.
    An ``agent`` token presented here with ``scope="api"`` is ``None`` — agent
    tokens never authenticate HTTP (ADR-091 cl. 17).
    Updates ``last_used_at`` at most once per minute.
    """
    return await _resolve(db, raw, scope)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def create_token(
    db: AsyncSession, *, user_id: uuid.UUID, scope: TokenScope, name: str
) -> tuple["PersonalToken", str]:
    """Insert a token row and return ``(row, raw)``; flushes, the caller commits
    (so the audit row lands in the same transaction).

    A prefix collision (``UNIQUE(prefix)``) retries with a new prefix inside a
    savepoint (adversarial-security §S: "prefix collision must retry on insert").
    """
    from applire.models.auth import PersonalToken

    if scope not in TOKEN_SCOPES:
        raise ValueError(f"unknown token scope {scope!r}")
    last_error: Exception | None = None
    for _ in range(_PREFIX_RETRIES):
        raw, prefix, token_hash = generate_token()
        row = PersonalToken(
            id=uuid.uuid4(),
            user_id=user_id,
            scope=scope,
            name=name,
            prefix=prefix,
            token_hash=token_hash,
            created_at=_now(),
        )
        try:
            async with db.begin_nested():
                db.add(row)
        except IntegrityError as exc:  # prefix taken — draw again
            last_error = exc
            continue
        return row, raw
    raise RuntimeError("could not allocate a unique token prefix") from last_error


async def list_tokens(
    db: AsyncSession, *, user_id: uuid.UUID | None, scopes: Iterable[TokenScope]
) -> list["PersonalToken"]:
    """Live (not revoked) tokens of ``scopes``; ``user_id=None`` = every owner."""
    from applire.models.auth import PersonalToken

    stmt = select(PersonalToken).where(
        PersonalToken.scope.in_(list(scopes)), PersonalToken.revoked_at.is_(None)
    )
    if user_id is not None:
        stmt = stmt.where(PersonalToken.user_id == user_id)
    stmt = stmt.order_by(PersonalToken.created_at.desc(), PersonalToken.id)
    return list((await db.execute(stmt)).scalars().all())


async def bump_link_epoch(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Invalidate every outstanding signed document link of ``user_id`` (cl. 18)."""
    await db.execute(
        update(User).where(User.id == user_id).values(link_epoch=User.link_epoch + 1)
    )


async def revoke_token(
    db: AsyncSession,
    *,
    token_id: uuid.UUID,
    scopes: Iterable[TokenScope],
    user_id: uuid.UUID | None = None,
) -> "PersonalToken | None":
    """Revoke one live token; ``None`` when it is missing, foreign, already revoked
    or of another scope (all indistinguishable — 404 for the caller, S-10).

    ``user_id`` restricts to that owner (``/api/me/tokens``); ``None`` = any owner
    (admin probe tokens). Revoking an ``agent`` token bumps the owner's
    ``link_epoch``, so the links that agent was handed die with it. Does not
    commit (the caller writes the audit row in the same transaction).
    """
    from applire.models.auth import PersonalToken

    stmt = (
        update(PersonalToken)
        .where(
            PersonalToken.id == token_id,
            PersonalToken.scope.in_(list(scopes)),
            PersonalToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
        .returning(PersonalToken.id, PersonalToken.user_id, PersonalToken.scope)
    )
    if user_id is not None:
        stmt = stmt.where(PersonalToken.user_id == user_id)
    hit = (await db.execute(stmt)).first()
    if hit is None:
        return None
    if hit.scope == "agent":
        await bump_link_epoch(db, hit.user_id)
    return await db.get(PersonalToken, hit.id, populate_existing=True)


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every live ``agent`` + ``api`` token of ``user_id`` and bump its
    ``link_epoch`` (signed links die too). Returns the number revoked.

    Called by package 1b for admin "revoke all tokens", disable and delete. Does
    **not** commit — it joins the caller's transaction (disable/delete write more
    rows in the same unit of work).
    """
    from applire.models.auth import PersonalToken

    result = await db.execute(
        update(PersonalToken)
        .where(
            PersonalToken.user_id == user_id,
            PersonalToken.scope.in_(["agent", "api"]),
            PersonalToken.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
        .execution_options(synchronize_session=False)
    )
    await bump_link_epoch(db, user_id)
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Audit seam (S-11): one helper, one guarded import
# ---------------------------------------------------------------------------


async def audit_token_event(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    owner_id: uuid.UUID,
    token_id: uuid.UUID | None = None,
    scope: str | None = None,
    count: int | None = None,
) -> bool:
    """Write the audit row for a token action through 1b's ``services.audit.record``.

    ``details`` carry ids, the scope and a count only — never a token, a prefix,
    a name or an email (ADR-091 cl. 26 key-set rule). Returns ``False`` when the
    audit module is absent (this branch alone, before 1b is merged — the main
    session removes the guard at integration). Does not commit.
    """
    try:
        from applire.services.audit import record  # package 1b
    except ImportError:  # pragma: no cover - integration guard
        logger.debug("services.audit absent — %s not audited on this branch", action)
        return False
    details: dict[str, Any] = {}
    if token_id is not None:
        details["token_id"] = str(token_id)
    if scope is not None:
        details["scope"] = scope
    if count is not None:
        details["count"] = count
    await record(
        db,
        actor_id=actor_id,
        action=action,
        target_type="user",
        target_id=owner_id,
        details=details,
    )
    return True


# ---------------------------------------------------------------------------
# Request helpers (consumed by auth/deps.py, auth/csrf.py, auth/deps_links.py)
# ---------------------------------------------------------------------------

_STATE_ATTR = "_applire_bearer_cache"


def bearer_from_request(request: "Request") -> str | None:
    """The bearer credential of ``request``.

    * ``None`` — no ``Authorization`` header at all (the cookie may be read).
    * ``""``   — a header is present but is not ``Bearer <token>``: the caller
      answers 401 and **never** falls back to the cookie (adversarial-security §3).
    * otherwise the raw token string (unvalidated).
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, value = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


async def request_bearer_user(
    request: "Request", db: AsyncSession, scope: TokenScope = "api"
) -> User | None:
    """Resolve the request's bearer for ``scope`` once per request (cached on
    ``request.state`` per scope, so the CSRF check and the dependency do not query
    twice). ``None`` when there is no header, it is malformed, or invalid."""
    cache: dict[str, Any] = getattr(request.state, _STATE_ATTR, None) or {}
    if scope in cache:
        return cache[scope]
    raw = bearer_from_request(request)
    user = await resolve_bearer(db, raw, scope) if raw else None
    cache[scope] = user
    setattr(request.state, _STATE_ATTR, cache)
    return user


async def is_csrf_exempt(request: "Request", db: AsyncSession) -> bool:
    """True **only** when the request carries a valid ``api``-scope bearer of an
    active user (ADR-091 cl. 12). Presence of a header is not enough: an invalid,
    revoked, ``agent`` or ``probe`` bearer is not exempt (adversarial-security §3 —
    a same-site page could otherwise add ``Authorization: Bearer x`` beside the
    cookie and skip the origin check)."""
    return await request_bearer_user(request, db, "api") is not None
