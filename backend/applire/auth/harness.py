# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fenced NoAuth test harness (ADR-091 cl. 3; D-1, MD-9; SF-IAM.2).

``HarnessAuthProvider`` answers every request as the stub user with the
``admin`` role — for unit tests and the harness E2E lanes only. It is reachable
only when ALL fences hold:

(a) ``AUTH_HARNESS=true`` (registry; withheld from ``.env.example``) — the
    factory only builds this provider then.
(b) **No user holds a credential** (password hash or OIDC binding) — checked at
    startup **and on every request** (cached ≤ 5 s per engine). Once false,
    every request gets **503 harness_disabled** ("instance claimed"); and
    ``POST /api/setup`` answers 409 ``harness_active`` while the flag is on.
(c) **A test database**: SQLite in-memory (sufficient by itself — it cannot
    hold anyone's data across a restart), **or** a Postgres database whose name
    ends in ``_ci``/``_test`` **and** the boot latch holds: at startup
    ``master_profiles`` has zero rows. The latch is evaluated once at boot
    (``enforce_at_startup``), so an E2E lane that seeds after boot keeps
    working while any boot against an existing vault refuses (exit 1).
(d) It says so: a WARNING block every boot, ``/api/auth/state.harness``, the
    red banner, and an audit row ``harness.boot``.

Residual (stated in the ADR): a fresh production database deliberately named
``*_ci``/``*_test`` started with the flag is open for data entered after that
boot until the next restart refuses it. In-process use without a lifespan (the
unit suites) leaves the latch unevaluated; a serving process always runs the
lifespan, which exits 1 on a failed fence.
"""

from __future__ import annotations

import logging
import time
import uuid
import weakref
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.base import AuthProvider
from applire.config import settings
from applire.models.user import ROLE_ADMIN, User

logger = logging.getLogger("applire.auth.harness")

#: The stub identity (ADR-091 cl. 15: the stub constants live here). Same values
#: as the retired ``auth/no_auth.py`` constants, which tests still import.
STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
STUB_EMAIL = "local@applire.community"
STUB_CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

CREDENTIAL_CACHE_SECONDS = 5.0
TEST_DB_SUFFIXES = ("_ci", "_test")

HARNESS_DISABLED_MESSAGE = "harness disabled: instance claimed"


class HarnessRefused(SystemExit):
    """A fence failed at startup; the process must exit 1."""

    def __init__(self, reason: str):
        super().__init__(1)
        self.reason = reason


def harness_disabled(reason: str = HARNESS_DISABLED_MESSAGE) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error_code": "harness_disabled", "message": reason},
    )


# ---------------------------------------------------------------------------
# Fence (c) — the test database
# ---------------------------------------------------------------------------

#: None = not evaluated in this process (no lifespan ran); True/False after boot.
_boot_latch: bool | None = None


def boot_latch() -> bool | None:
    return _boot_latch


def _set_boot_latch(value: bool | None) -> None:
    global _boot_latch
    _boot_latch = value


def is_sqlite_memory(url: str) -> bool:
    try:
        u = make_url(url)
    except Exception:  # noqa: BLE001 — an unparsable URL is not a test DB
        return False
    if not u.drivername.startswith("sqlite"):
        return False
    return u.database in (None, "", ":memory:") or "mode=memory" in str(u.query)


def is_test_postgres(url: str) -> bool:
    try:
        u = make_url(url)
    except Exception:  # noqa: BLE001
        return False
    return u.drivername.startswith("postgresql") and bool(u.database) and (
        u.database.endswith(TEST_DB_SUFFIXES)
    )


def database_fence_reason(url: str | None = None) -> str | None:
    """``None`` if fence (c) holds for ``url`` (default: the configured one), else the reason."""
    url = settings.database_url if url is None else url
    if is_sqlite_memory(url):
        return None
    if is_test_postgres(url):
        if _boot_latch is False:
            return "the database held a vault (master profiles) when this process started"
        return None
    return (
        "the database is neither SQLite in-memory nor a Postgres database whose "
        "name ends in _ci or _test"
    )


# ---------------------------------------------------------------------------
# Fence (b) — nobody holds a credential
# ---------------------------------------------------------------------------

_credential_cache: "weakref.WeakKeyDictionary[object, tuple[float, bool]]" = (
    weakref.WeakKeyDictionary()
)


def credential_predicate():
    """SQL: this user holds a credential (password or OIDC binding)."""
    return or_(User.password_hash.is_not(None), User.oidc_subject.is_not(None))


async def any_credential(db: AsyncSession, *, use_cache: bool = True) -> bool:
    """True iff some user holds a password hash or an OIDC binding."""
    bind = db.get_bind()
    now = time.monotonic()
    if use_cache:
        hit = _credential_cache.get(bind)
        if hit is not None and now - hit[0] <= CREDENTIAL_CACHE_SECONDS:
            return hit[1]
    try:
        value = bool(
            (await db.execute(select(exists().where(credential_predicate())))).scalar()
        )
    except (DatabaseError, OSError):
        # No ``users`` table (a unit test that creates only the tables it needs),
        # or no database reachable at all (an in-process test against an unset
        # URL): such a database holds no credential and serves no data. A
        # serving process has both — the lifespan migrates before serving.
        await db.rollback()
        return False
    try:
        _credential_cache[bind] = (now, value)
    except TypeError:  # pragma: no cover — a bind that cannot be weak-referenced
        pass
    return value


def forget_credential_cache() -> None:
    _credential_cache.clear()


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


def stub_user() -> User:
    """A transient stub row (never added to a session)."""
    return User(
        id=STUB_USER_ID,
        email=STUB_EMAIL,
        created_at=STUB_CREATED_AT,
        deleted_at=None,
        role=ROLE_ADMIN,
    )


class HarnessAuthProvider(AuthProvider):
    """Every request is the stub user acting as admin — behind fences (a)–(d)."""

    is_harness = True

    async def get_current_user(self, request: Request, db: AsyncSession) -> User | None:
        reason = database_fence_reason()
        if reason is not None:
            raise harness_disabled(f"harness disabled: {reason}")
        user: User | None = None
        if isinstance(db, AsyncSession):
            if await any_credential(db):
                raise harness_disabled()
            try:
                user = await db.get(User, STUB_USER_ID)
            except (DatabaseError, OSError):
                await db.rollback()
                user = None
        # A test that overrides ``get_db`` with a non-session double gets the
        # transient stub; production ``get_db`` always yields an AsyncSession.
        if user is None:
            user = stub_user()
        request.state.auth_via = "harness"
        return user


# ---------------------------------------------------------------------------
# Startup (backend lifespan and ``mcp/__main__.py``)
# ---------------------------------------------------------------------------

_WARNING_BLOCK = (
    "\n"
    "==================================================================\n"
    "  AUTH_HARNESS IS ON — TEST HARNESS, NOT A PRODUCT MODE\n"
    "  Every request is answered as the administrator, without login.\n"
    "  This is only allowed on a test database with no account.\n"
    "  Never set AUTH_HARNESS on an instance that holds real data.\n"
    "==================================================================="
)


async def enforce_at_startup(db: AsyncSession) -> None:
    """Check fences (b) and (c) once at boot; set the latch; raise ``HarnessRefused``.

    No-op unless ``AUTH_HARNESS`` is on. The caller commits nothing here.
    """
    if not settings.auth_harness:
        return
    url = settings.database_url
    if not (is_sqlite_memory(url) or is_test_postgres(url)):
        raise HarnessRefused(database_fence_reason(url) or "not a test database")
    if await any_credential(db, use_cache=False):
        raise HarnessRefused("an account already holds a password or a sign-in binding")
    if is_test_postgres(url):
        from applire.models.profile import MasterProfile
        from applire.ownership import unscoped

        with unscoped("startup-backfill"):
            profiles = (
                await db.execute(select(func.count()).select_from(MasterProfile))
            ).scalar_one()
        _set_boot_latch(profiles == 0)
        if profiles:
            raise HarnessRefused(
                f"the database already holds {profiles} profile(s) — the harness never "
                "serves an existing vault"
            )
    logger.warning(_WARNING_BLOCK)


def log_refusal(exc: HarnessRefused) -> None:
    logger.error(
        "AUTH_HARNESS refused: %s. Remove AUTH_HARNESS from the environment; "
        "the instance then starts with login (AUTH_PROVIDER=local).",
        exc.reason,
    )
