# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retention for N accounts (D-4, ADR-005 amended 2026-10-03, ADR-092 cl. 12;
System-FMEA SF-RET / SF-OWN).

* users: tombstoned on INACTIVITY — ``coalesce(last_active_at, created_at)`` —
  never on sign-up date alone; an admin is never tombstoned;
* orphan postings (no reference from any of the seven tables, older than
  ``INTERVIEW_SESSION_TTL_DAYS``) purged; referenced or young ones kept;
* dead auth sessions / used-or-expired links and grants purged; live ones kept;
* audit rows older than ``AUDIT_LOG_RETENTION_DAYS`` deleted, ``0`` keeps all;
* the sweep runs under the declared ``unscoped("retention")`` reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.audit import AuditEvent
from applire.models.auth import AuthLink, AuthSession, ReauthGrant
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.retention import worker
from tests.support.isolation import OwnerWorld

pytestmark = pytest.mark.no_owner_context

def _now() -> datetime:
    # per call, never at import: in a full run the module is imported minutes
    # before this test runs, and a "+5 min" grant would already have expired.
    return datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return _now() - timedelta(days=days)


@pytest_asyncio.fixture
async def factory():
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(eng, expire_on_commit=False)
    yield f
    await eng.dispose()


async def _user(s, *, created_days: float, active_days: float | None, role: str = "user") -> uuid.UUID:
    u = User(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@example.org", role=role,
        created_at=_ago(created_days),
        last_active_at=_ago(active_days) if active_days is not None else None,
    )
    s.add(u)
    await s.flush()
    return u.id


@pytest.mark.asyncio
async def test_users_are_tombstoned_on_inactivity_not_on_age_and_admins_never(factory):
    ttl = worker._INACTIVITY_TTL_DAYS
    with ownership.unscoped("retention"):
        async with factory() as s:
            old_but_active = await _user(s, created_days=ttl + 400, active_days=3)
            old_never_active = await _user(s, created_days=ttl + 1, active_days=None)
            old_inactive = await _user(s, created_days=ttl + 400, active_days=ttl + 5)
            young_never_active = await _user(s, created_days=10, active_days=None)
            inactive_admin = await _user(s, created_days=ttl + 400, active_days=ttl + 5, role="admin")
            await s.commit()
            n = await worker._tombstone_inactive_users(s)
        async with factory() as s:
            dead = {
                u.id for u in (await s.execute(select(User))).scalars() if u.deleted_at is not None
            }
    assert dead == {old_never_active, old_inactive}
    assert n == 2
    assert old_but_active not in dead  # the pre-D-4 rule (created_at) killed this one
    assert inactive_admin not in dead
    assert young_never_active not in dead


@pytest.mark.asyncio
async def test_orphan_postings_are_purged_referenced_and_young_ones_kept(factory):
    with ownership.unscoped("tooling"):
        async with factory() as s:
            b = User(id=uuid.uuid4(), email="ret-b@example.org", role="user")
            s.add(b)
            await s.flush()
            w = OwnerWorld(s, b)
            referenced = (await w.application()).job_analysis_id

            def _posting(days: float) -> JobAnalysis:
                j = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="x", role_title="r",
                                seniority_level="mid", language_requirement="English",
                                created_at=_ago(days))
                s.add(j)
                return j

            old_orphan = _posting(worker._SESSION_TTL_DAYS + 2)
            young_orphan = _posting(1)
            await s.flush()
            # make the referenced one old too: only the reference keeps it alive
            (await s.get(JobAnalysis, referenced)).created_at = _ago(worker._SESSION_TTL_DAYS + 50)
            await s.commit()
            old_id, young_id = old_orphan.id, young_orphan.id
    with ownership.unscoped("retention"):
        async with factory() as s:
            n = await worker._purge_orphan_postings(s)
        async with factory() as s:
            left = set((await s.execute(select(JobAnalysis.id))).scalars())
    assert n == 1
    assert old_id not in left
    assert {referenced, young_id} <= left


@pytest.mark.asyncio
async def test_auth_housekeeping_purges_dead_credentials_only(factory):
    from applire.auth.sessions import ABSOLUTE_TIMEOUT, IDLE_TIMEOUT

    with ownership.unscoped("retention"):
        async with factory() as s:
            uid = await _user(s, created_days=1, active_days=0)

            def _sess(**kw) -> AuthSession:
                x = AuthSession(user_id=uid, token_hash=uuid.uuid4().hex, **kw)
                s.add(x)
                return x

            live = _sess(created_at=_ago(1), last_seen_at=_ago(0.1))
            revoked = _sess(created_at=_ago(1), last_seen_at=_ago(0.1), revoked_at=_ago(0.5))
            idle = _sess(created_at=_ago(20), last_seen_at=_now() - IDLE_TIMEOUT - timedelta(hours=1))
            too_old = _sess(created_at=_now() - ABSOLUTE_TIMEOUT - timedelta(hours=1), last_seen_at=_ago(0.1))
            await s.flush()
            s.add_all([
                AuthLink(user_id=uid, purpose="reset", token_hash=uuid.uuid4().hex, expires_at=_now() + timedelta(hours=1)),
                AuthLink(user_id=uid, purpose="reset", token_hash=uuid.uuid4().hex, expires_at=_ago(0.1)),
                AuthLink(user_id=uid, purpose="invite", token_hash=uuid.uuid4().hex,
                         expires_at=_now() + timedelta(days=3), used_at=_ago(0.2)),
                ReauthGrant(user_id=uid, session_id=live.id, action="account.delete", target_id=uid,
                            expires_at=_now() + timedelta(minutes=5)),
                ReauthGrant(user_id=uid, session_id=live.id, action="account.delete", target_id=uid,
                            expires_at=_ago(0.01)),
                ReauthGrant(user_id=uid, session_id=revoked.id, action="account.delete", target_id=uid,
                            expires_at=_now() + timedelta(minutes=5)),
            ])
            await s.commit()
            out = await worker._purge_auth_housekeeping(s)
        async with factory() as s:
            sessions = set((await s.execute(select(AuthSession.id))).scalars())
            links = (await s.execute(select(AuthLink))).scalars().all()
            grants = (await s.execute(select(ReauthGrant))).scalars().all()
    assert sessions == {live.id}
    assert out == {"auth_sessions_deleted": 3, "auth_links_deleted": 2, "reauth_grants_deleted": 2}
    assert len(links) == 1 and links[0].used_at is None
    assert len(grants) == 1 and grants[0].session_id == live.id
    assert idle.id not in sessions and too_old.id not in sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("days,expected_left", [(730, 1), (0, 2)])
async def test_audit_rows_older_than_the_retention_are_deleted(factory, monkeypatch, days, expected_left):
    from applire.config import settings

    monkeypatch.setattr(settings, "audit_log_retention_days", days)
    with ownership.unscoped("retention"):
        async with factory() as s:
            s.add_all([
                AuditEvent(action="user.created", at=_ago(800), detail={}),
                AuditEvent(action="user.created", at=_ago(5), detail={}),
            ])
            await s.commit()
            n = await worker._purge_audit_events(s)
        async with factory() as s:
            left = (await s.execute(select(AuditEvent))).scalars().all()
    assert len(left) == expected_left
    assert n == 2 - expected_left
    if days:
        assert all(e.action == "user.created" and (e.at.replace(tzinfo=timezone.utc) > _ago(10)) for e in left)


@pytest.mark.asyncio
async def test_the_sweep_runs_under_the_declared_retention_reason(monkeypatch):
    seen = []

    async def _probe():
        ctx = ownership.current_owner()
        seen.append(ctx.reason if ctx else None)
        return {}

    monkeypatch.setattr(worker, "_sweep_unscoped", _probe)
    await worker._sweep()
    assert seen == ["retention"]
