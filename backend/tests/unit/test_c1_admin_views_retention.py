# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WP-C1 — retention toggle, LinkedIn switch, admin audit / usage / dashboard views.

ADR-093 (runtime instance settings), #738 (retention OFF), #726 (LinkedIn guest
pages), #694 (admin dashboard).  Companion to ``test_c1_instance_settings.py``;
its ``env`` fixture, ``Acting`` and the autouse overlay cleaner are reused.

Zero provider calls, zero network: the provider probe is stubbed (``env``), every
fetch tier is stubbed, the retention sweep runs against an in-memory SQLite with a
stub storage backend (the orphan-file scan would otherwise enumerate the real
upload volume).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from mcp.shared.exceptions import McpError
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401 — every table on Base.metadata
from applire import config as app_config
from applire import ownership
from applire.config import settings
from applire.db.session import Base
from applire.models.application import Application
from applire.models.audit import AuditEvent
from applire.models.auth import AuthLink
from applire.models.cv import GeneratedCV
from applire.models.instance_settings import InstanceSetting
from applire.models.job import JobAnalysis
from applire.models.llm_usage import LlmUsage
from applire.models.session import InterviewSession
from applire.models.uploads import UploadRecord
from applire.models.user import User
from applire.retention import worker
from applire.services import audit
from applire.services import instance_settings as svc
from applire.services import scraper
from applire.services.admin import failed_jobs
from applire.services.ops import probes
from applire.services.scraper import (
    LINKEDIN_DISABLED_CODE,
    ScraperError,
    is_linkedin_url,
    scrape_job_url,
)
from tests.support.isolation import OwnerWorld
from tests.support.owners_1b import add_user
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse: the MCP door signs links
from tests.unit.test_c1_instance_settings import (  # noqa: F401 — fixtures reused
    ORIGIN,
    Acting,
    _clean_overlay,
    _put,
    env,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return _now() - timedelta(days=days)


# =====================================================================================
# 1 + 2. Retention OFF (#738, ruling C1-3) against the REAL worker._sweep
# =====================================================================================


class _StubStorage:
    """No enumeration support → the orphan scan skips; deletes touch no disk."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def list_files(self):
        return None

    async def delete(self, path: str) -> None:
        self.deleted.append(path)


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(svc, "_session_factory", lambda: f)
    monkeypatch.setattr(worker, "AsyncSessionLocal", f)
    monkeypatch.setattr("applire.storage.get_storage", lambda: _StubStorage())
    yield f
    await engine.dispose()


async def _owner(s, label: str) -> OwnerWorld:
    u = User(id=uuid.uuid4(), email=f"{label}-{uuid.uuid4().hex[:6]}@example.org", role="user")
    s.add(u)
    await s.flush()
    return OwnerWorld(s, u)


async def _seed_world(factory) -> dict[str, uuid.UUID]:
    """One row for every rule the toggle gates or spares (all synthetic)."""
    ids: dict[str, uuid.UUID] = {}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            a = await _owner(s, "alice")
            # (1) a live (non-cancelled) application, 730-day-inactive
            live = await a.application()
            live.expires_at = _ago(2)
            ids["live_app"] = live.id
            # (2) an interview session older than its TTL
            sess = await a.interview()
            sess.updated_at = _ago(worker._SESSION_TTL_DAYS + 10)
            ids["session"] = sess.id
            # (3) an expired upload
            up = UploadRecord(
                user_id=a.user.id, original_filename="cv.pdf", content_hash="h" * 64,
                mime_type="application/pdf", file_path="/nonexistent/cv.pdf", byte_size=3,
                created_at=_ago(worker._UPLOADS_TTL_DAYS + 3), expires_at=_ago(3),
            )
            s.add(up)
            await s.flush()
            ids["upload"] = up.id
            # (4) an expired auth link — housekeeping that must always run
            link = AuthLink(
                user_id=a.user.id, purpose="reset", token_hash=uuid.uuid4().hex * 2,
                created_at=_ago(3), expires_at=_ago(2),
            )
            s.add(link)
            await s.flush()
            ids["link"] = link.id

            b = await _owner(s, "bob")
            # (5) a CANCELLED application past its short clock, with its document
            cancelled = await b.application()
            cancelled.user_status = "cancelled"
            cancelled.expires_at = _ago(1)
            cv = await b.cv()
            cv.expires_at = _now() + timedelta(days=365)  # calendar TTL would spare it
            ids["cancelled_app"], ids["cancelled_cv"] = cancelled.id, cv.id
            await s.commit()
    return ids


async def _state(factory, ids) -> dict[str, bool]:
    """True = the row still exists / is not tombstoned."""
    with ownership.unscoped("tooling"):
        async with factory() as s:
            live = await s.get(Application, ids["live_app"])
            cancelled = await s.get(Application, ids["cancelled_app"])
            return {
                "live_app_untouched": live is not None and live.deleted_at is None,
                "cancelled_app_tombstoned": cancelled.deleted_at is not None,
                "session_exists": await s.get(InterviewSession, ids["session"]) is not None,
                "upload_exists": await s.get(UploadRecord, ids["upload"]) is not None,
                "link_exists": await s.get(AuthLink, ids["link"]) is not None,
                "cancelled_cv_exists": await s.get(GeneratedCV, ids["cancelled_cv"]) is not None,
            }


async def _skipped_rows(factory) -> list[AuditEvent]:
    async with factory() as s:
        return list((await s.execute(
            select(AuditEvent).where(AuditEvent.action == "retention.skipped"))).scalars())


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_retention_off_suspends_the_calendar_ttls_but_not_the_cancelled_path_or_housekeeping(factory):
    ids = await _seed_world(factory)
    async with factory() as s:
        s.add(InstanceSetting(key="RETENTION_ENABLED", value=False))
        await s.commit()

    report = await worker._sweep()

    state = await _state(factory, ids)
    # (a) the personal-data TTLs are suspended
    assert state["upload_exists"] is True
    assert state["session_exists"] is True
    assert state["live_app_untouched"] is True
    # (b) the user's own discard decision still runs (US222)
    assert state["cancelled_app_tombstoned"] is True
    assert state["cancelled_cv_exists"] is False
    assert report["applications_tombstoned"] == 1
    assert report["cancelled_cvs_deleted"] == 1
    assert report["uploads_deleted"] == 0 and report["interview_sessions_deleted"] == 0
    # (c) auth housekeeping is never suspended
    assert state["link_exists"] is False
    assert report["auth_links_deleted"] == 1
    # (d) what was in force is on the report, and the skip left a trace
    assert report["retention_enabled"] is False
    assert report["retention_source"] == "panel"
    rows = await _skipped_rows(factory)
    assert len(rows) == 1 and rows[0].detail["source"] == "panel"


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_retention_on_purges_the_same_seed(factory):
    ids = await _seed_world(factory)
    # no override row: the environment default (on) is in force

    report = await worker._sweep()

    state = await _state(factory, ids)
    assert state["upload_exists"] is False
    assert state["session_exists"] is False
    assert state["live_app_untouched"] is False  # tombstoned by the 730-day rule
    assert state["cancelled_app_tombstoned"] is True and state["cancelled_cv_exists"] is False
    assert state["link_exists"] is False
    assert report["uploads_deleted"] == 1 and report["interview_sessions_deleted"] == 1
    assert report["applications_tombstoned"] == 2
    assert report["retention_enabled"] is True
    assert await _skipped_rows(factory) == []


def test_suspendable_rules_are_exactly_the_documented_set():
    """Mutation guard: widening this tuple suspends a rule the ADR keeps on; narrowing
    it lets a toggled-off instance keep purging personal data."""
    assert worker.SUSPENDABLE_RULES == (
        "uploads",
        "interview_sessions",
        "generated_cvs",
        "generated_cover_letters",
        "master_profiles_inactivity",
        "users_inactivity",
        "applications_inactivity",
        "orphan_postings",
    )
    # what must NEVER be suspendable
    for always_on in ("cancelled_applications", "auth_housekeeping", "erasure", "audit_events"):
        assert always_on not in worker.SUSPENDABLE_RULES


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_cancelled_only_tombstones_only_expired_cancelled_applications(factory):
    with ownership.unscoped("tooling"):
        async with factory() as s:
            ids = {}
            for name, status, expires in (
                ("tracking_expired", "tracking", _ago(1)),
                ("cancelled_expired", "cancelled", _ago(1)),
                ("cancelled_young", "cancelled", _now() + timedelta(days=5)),
            ):
                w = await _owner(s, name)
                app = await w.application()
                app.user_status, app.expires_at = status, expires
                ids[name] = app.id
            await s.commit()

        async with factory() as s:
            n = await worker._tombstone_inactive_applications(s, cancelled_only=True)
        assert n == 1
        async with factory() as s:
            dead = {k for k, v in ids.items() if (await s.get(Application, v)).deleted_at is not None}
        assert dead == {"cancelled_expired"}, "cancelled_only must never touch a non-cancelled row"

        # the baseline: without the guard the 730-day rule takes the tracking one too,
        # so the assertion above is the guard's doing, not an artefact of the seed
        async with factory() as s:
            n = await worker._tombstone_inactive_applications(s)
        assert n == 1  # the tracking one (the cancelled one is already tombstoned)
        async with factory() as s:
            assert (await s.get(Application, ids["tracking_expired"])).deleted_at is not None
            assert (await s.get(Application, ids["cancelled_young"])).deleted_at is None


# =====================================================================================
# 3. LinkedIn switch (#726)
# =====================================================================================

LI = "https://www.linkedin.com/jobs/view/1"


@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/jobs/view/1", True),
    ("https://de.linkedin.com/jobs/view/2", True),
    ("https://linkedin.com/jobs/view/3", True),
    ("https://LinkedIn.com./jobs/view/3", True),
    ("https://lnkd.in/x", True),
    ("https://linkedin.com.evil.example/", False),
    ("https://notlinkedin.com/", False),
    ("https://evil.example/?u=https://linkedin.com/", False),
    ("https://jobs.example.com/1", False),
])
def test_is_linkedin_url(url, expected):
    assert is_linkedin_url(url) is expected


def _no_fetch(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("a fetch tier ran although the LinkedIn switch is off")

    monkeypatch.setattr(scraper, "_fetch_tier1", boom)
    monkeypatch.setattr(scraper, "_fetch_tier2", boom)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [LI, "https://lnkd.in/x", "https://de.linkedin.com/jobs/view/9"])
async def test_switch_off_refuses_linkedin_before_any_fetch(monkeypatch, url):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    _no_fetch(monkeypatch)
    with pytest.raises(ScraperError) as exc:
        await scrape_job_url(url)
    assert exc.value.code == "linkedin_guest_fetch_disabled" == LINKEDIN_DISABLED_CODE


@pytest.mark.asyncio
async def test_switch_off_leaves_other_hosts_alone(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    seen = []

    async def tier1(url):
        seen.append(url)
        return "posting body"

    monkeypatch.setattr(scraper, "_fetch_tier1", tier1)
    assert await scrape_job_url("https://jobs.example.com/1") == "posting body"
    assert seen == ["https://jobs.example.com/1"]


@pytest.mark.asyncio
async def test_switch_on_reaches_tier1_for_linkedin(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", True)
    seen = []

    async def tier1(url):
        seen.append(url)
        return "linkedin posting text"

    async def tier2(_url):
        raise AssertionError("tier 2 must not run when tier 1 answered")

    monkeypatch.setattr(scraper, "_fetch_tier1", tier1)
    monkeypatch.setattr(scraper, "_fetch_tier2", tier2)
    assert await scrape_job_url(LI) == "linkedin posting text"
    assert seen == [LI]


def test_web_door_answers_422_with_the_machine_readable_code(monkeypatch):
    from applire.main import app

    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    _no_fetch(monkeypatch)
    res = TestClient(app).post("/api/job/analyze", json={"url": LI})
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["error_code"] == "linkedin_guest_fetch_disabled"
    assert "paste" in detail["message"].lower()


def test_web_door_keeps_jd_fetch_failed_for_another_scrape_failure(monkeypatch):
    from applire.main import app

    with patch("applire.routers.job.scrape_job_url", new_callable=AsyncMock,
               side_effect=ScraperError("https://jobs.example.com/x", "403 Forbidden")):
        res = TestClient(app).post("/api/job/analyze", json={"url": "https://jobs.example.com/x"})
    assert res.status_code == 422
    assert res.json()["detail"]["error_code"] == "jd_fetch_failed"


@pytest.mark.asyncio
async def test_mcp_door_carries_the_reason_beside_the_text(monkeypatch):
    from applire.mcp import server

    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    _no_fetch(monkeypatch)
    # _agent_call → instance_settings.pinned_call() → refresh(): never touch a real DB
    monkeypatch.setattr(svc, "refresh", AsyncMock(return_value={}))
    with patch("applire.mcp.server.get_provider"), patch("applire.mcp.server.get_db"):
        with pytest.raises(McpError) as exc:
            await server.analyze_jd(url=LI)
    assert exc.value.error.data == {"reason": "linkedin_guest_fetch_disabled"}
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_mcp_door_keeps_jd_fetch_failed_for_another_scrape_failure(monkeypatch):
    from applire.mcp import server

    monkeypatch.setattr(svc, "refresh", AsyncMock(return_value={}))
    with (
        patch("applire.mcp.server.scrape_job_url",
              AsyncMock(side_effect=ScraperError("https://jobs.example.com/x", "403 Forbidden"))),
        patch("applire.mcp.server.get_provider"),
    ):
        with pytest.raises(McpError) as exc:
            await server.analyze_jd(url="https://jobs.example.com/x")
    assert exc.value.error.data == {"reason": "jd_fetch_failed"}


# =====================================================================================
# 4. Audit view
# =====================================================================================


async def _seed_audit(db, admin):
    """Seven rows, oldest first; returns (u1, u2, actions oldest-first)."""
    u1 = await add_user(db, email="u1@example.org")
    u2 = await add_user(db, email="u2@example.org")
    plan = [
        (admin.id, "password.changed", None, None, {}),
        (admin.id, "user.disabled", "user", u1.id, {}),
        (admin.id, "user.enabled", "user", u1.id, {}),
        (u1.id, "password.changed", None, None, {}),
        (u1.id, "token.created", "token", uuid.uuid4(), {"token_id": str(uuid.uuid4()), "scope": "read"}),
        (None, "user.disabled", "user", u2.id, {}),
        (u2.id, "password.changed", None, None, {}),
    ]
    for actor, action, ttype, tid, details in plan:
        await audit.record(db, actor_id=actor, action=action, target_type=ttype,
                           target_id=tid, details=details)
        await asyncio.sleep(0.003)  # distinct `at`: the keyset order is then the insert order
    await db.commit()
    return u1, u2, [p[1] for p in plan]


@pytest.mark.asyncio
async def test_audit_pages_newest_first_and_the_cursor_walks_every_row_once(env):
    db, client, _, admin = env
    _, _, actions = await _seed_audit(db, admin)

    first = await client.get("/api/admin/audit", params={"limit": 3})
    assert first.status_code == 200, first.text
    body = first.json()
    assert [i["action"] for i in body["items"]] == list(reversed(actions))[:3]
    assert body["next_cursor"]
    assert "password.changed" in body["actions"]

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/admin/audit", params=params)).json()
        seen += page["items"]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3  # 3 + 3 + 1
    assert [i["action"] for i in seen] == list(reversed(actions))
    assert len({i["id"] for i in seen}) == len(seen) == 7, "a row appeared twice or went missing"
    ats = [i["at"] for i in seen]
    assert ats == sorted(ats, reverse=True)


@pytest.mark.asyncio
async def test_audit_action_filter_is_repeatable_and_actor_filter_works(env):
    db, client, _, admin = env
    u1, _, _ = await _seed_audit(db, admin)

    r = await client.get("/api/admin/audit", params=[("action", "user.disabled")])
    assert [i["action"] for i in r.json()["items"]] == ["user.disabled", "user.disabled"]

    r = await client.get("/api/admin/audit",
                         params=[("action", "user.disabled"), ("action", "user.enabled")])
    assert sorted(i["action"] for i in r.json()["items"]) == ["user.disabled", "user.disabled", "user.enabled"]

    r = await client.get("/api/admin/audit", params={"actor_id": str(u1.id)})
    items = r.json()["items"]
    assert sorted(i["action"] for i in items) == ["password.changed", "token.created"]
    assert all(i["actor_user_id"] == str(u1.id) and i["actor_email"] == "u1@example.org" for i in items)

    r = await client.get("/api/admin/audit", params={"actor_id": str(admin.id), "action": "user.enabled"})
    assert len(r.json()["items"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["not-a-cursor!!", "bm9wZQ", "A" * 60])
async def test_audit_tampered_cursor_is_422_invalid_cursor(env, cursor):
    db, client, _, admin = env
    await _seed_audit(db, admin)
    r = await client.get("/api/admin/audit", params={"cursor": cursor})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_code"] == "invalid_cursor"


@pytest.mark.asyncio
async def test_audit_resolves_actor_email_and_is_none_for_an_erased_user(env):
    db, client, _, admin = env
    _, u2, _ = await _seed_audit(db, admin)
    items = (await client.get("/api/admin/audit", params={"actor_id": str(u2.id)})).json()["items"]
    assert items[0]["actor_email"] == "u2@example.org"

    u2.deleted_at = _now()  # erased
    await db.commit()
    body = (await client.get("/api/admin/audit", params={"limit": 200})).json()
    by_actor = {i["actor_user_id"]: i for i in body["items"] if i["actor_user_id"]}
    assert by_actor[str(u2.id)]["actor_email"] is None
    assert by_actor[str(admin.id)]["actor_email"] == "admin@example.org"
    # the system actor has no email either
    system = next(i for i in body["items"] if i["actor_user_id"] is None)
    assert system["actor_email"] is None
    # target of the system row is the erased u2 → also unresolved
    assert system["target_email"] is None


# =====================================================================================
# 5. Usage view
# =====================================================================================


def _usage(user_id, *, days_ago, provider, model, kind, prompt, completion, ok=True, estimated=False):
    return LlmUsage(
        user_id=user_id, created_at=_ago(days_ago), provider=provider, model=model,
        document_kind=kind, prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion, ok=ok, estimated=estimated,
    )


async def _seed_usage(db):
    u1 = await add_user(db, email="usage1@example.org")
    u2 = await add_user(db, email="usage2@example.org")
    await add_user(db, email="usage-idle@example.org")
    db.add_all([
        _usage(u1.id, days_ago=1, provider="mistral", model="m1", kind="cv", prompt=100, completion=50),
        _usage(u1.id, days_ago=2, provider="mistral", model="m1", kind="cover_letter", prompt=20, completion=10),
        _usage(u1.id, days_ago=3, provider="mistral", model="m1", kind="cv", prompt=5, completion=0, ok=False),
        _usage(u2.id, days_ago=1, provider="openrouter", model="o1", kind="", prompt=40, completion=10,
               estimated=True),
        _usage(None, days_ago=1, provider="mistral", model="m1", kind="cv", prompt=7, completion=3),
        _usage(u1.id, days_ago=40, provider="mistral", model="m1", kind="cv", prompt=1000, completion=0),
    ])
    await db.commit()


def _user_row(body, email):
    return next(u for u in body["users"] if u["email"] == email)


@pytest.mark.asyncio
async def test_usage_aggregates_per_user_provider_and_kind(env):
    db, client, _, _ = env
    await _seed_usage(db)
    r = await client.get("/api/admin/usage", params={"days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_days"] == 30

    u1 = _user_row(body, "usage1@example.org")["totals"]
    assert (u1["calls"], u1["failed_calls"], u1["prompt_tokens"], u1["completion_tokens"], u1["total_tokens"]) == \
        (3, 1, 125, 60, 185)  # the 40-day-old row is outside the window
    u2 = _user_row(body, "usage2@example.org")["totals"]
    assert (u2["calls"], u2["estimated_calls"], u2["total_tokens"]) == (1, 1, 50)
    idle = _user_row(body, "usage-idle@example.org")
    assert all(v == 0 for v in idle["totals"].values()) and idle["last_call_at"] is None
    assert _user_row(body, "usage1@example.org")["last_call_at"] is not None

    assert (body["unattributed"]["calls"], body["unattributed"]["total_tokens"]) == (1, 10)
    assert (body["totals"]["calls"], body["totals"]["total_tokens"]) == (5, 245)

    prov = {(p["provider"], p["model"]): p["totals"] for p in body["by_provider"]}
    assert prov[("mistral", "m1")]["calls"] == 4 and prov[("mistral", "m1")]["total_tokens"] == 195
    assert prov[("openrouter", "o1")]["total_tokens"] == 50
    assert [(p["provider"]) for p in body["by_provider"]] == ["mistral", "openrouter"]  # by tokens desc

    kinds = body["by_document_kind"]
    assert (kinds["cv"]["calls"], kinds["cv"]["total_tokens"]) == (3, 165)
    assert (kinds["cover_letter"]["calls"], kinds["cover_letter"]["total_tokens"]) == (1, 30)
    assert (kinds["other"]["calls"], kinds["other"]["total_tokens"]) == (1, 50)  # "" → other
    # users sorted by tokens desc: heaviest first
    emails = [u["email"] for u in body["users"]]
    assert emails.index("usage1@example.org") < emails.index("usage2@example.org") < emails.index("usage-idle@example.org")


@pytest.mark.asyncio
async def test_usage_days_bounds_the_window(env):
    db, client, _, _ = env
    await _seed_usage(db)
    wide = (await client.get("/api/admin/usage", params={"days": 60})).json()
    assert _user_row(wide, "usage1@example.org")["totals"]["total_tokens"] == 1185  # 185 + the 40-day row
    narrow = (await client.get("/api/admin/usage", params={"days": 30})).json()
    assert _user_row(narrow, "usage1@example.org")["totals"]["total_tokens"] == 185


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 366, -1])
async def test_usage_days_out_of_range_is_422(env, days):
    _, client, _, _ = env
    assert (await client.get("/api/admin/usage", params={"days": days})).status_code == 422
    # the bounds themselves are accepted
    for ok in (1, 365):
        assert (await client.get("/api/admin/usage", params={"days": ok})).status_code == 200


# =====================================================================================
# 6. Dashboard
# =====================================================================================

CONTENT_SENTINEL = "SECRET-CONTENT-c1"


async def _seed_failed_cv(db, owner_email="failer@example.org"):
    owner = await add_user(db, email=owner_email)
    with ownership.unscoped("tooling"):
        cv = await OwnerWorld(db, owner).cv()
        cv.status = "failed"
        cv.error_code = "llm_timeout"
        cv.error_message = f"Provider quoted: {CONTENT_SENTINEL}"
        await db.commit()
    return owner, cv


@pytest.mark.asyncio
async def test_dashboard_lists_a_failed_job_without_its_message(env):
    db, client, _, _ = env
    owner, cv = await _seed_failed_cv(db)
    r = await client.get("/api/admin/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert CONTENT_SENTINEL not in r.text
    assert "error_message" not in r.text
    fj = body["failed_jobs"]
    assert fj["count"] == 1 and fj["window_days"] == failed_jobs.WINDOW_DAYS
    item = fj["items"][0]
    assert item["kind"] == "cv" and item["id"] == str(cv.id)
    assert item["error_code"] == "llm_timeout"
    assert item["user_id"] == str(owner.id) and item["user_email"] == "failer@example.org"
    assert {"code": "failed_jobs", "severity": "warning"} in body["notices"]
    # the cheap notices endpoint carries it too
    notices = (await client.get("/api/admin/notices")).json()
    assert {"code": "failed_jobs", "severity": "warning"} in notices["items"]


@pytest.mark.asyncio
async def test_dashboard_without_failures_has_no_failed_jobs_notice(env):
    _, client, _, _ = env
    body = (await client.get("/api/admin/dashboard")).json()
    assert body["failed_jobs"] == {"window_days": failed_jobs.WINDOW_DAYS, "count": 0, "items": []}
    assert all(n["code"] != "failed_jobs" for n in body["notices"])


@pytest.mark.asyncio
async def test_dashboard_counts_users_by_state(env):
    db, client, _, _ = env
    await add_user(db, email="act@example.org")
    await add_user(db, email="pend@example.org", state="pending")
    await add_user(db, email="dis@example.org", state="disabled")
    body = (await client.get("/api/admin/dashboard")).json()
    assert body["users"] == {"total": 4, "active": 2, "pending": 1, "disabled": 1, "admins": 1}


@pytest.mark.asyncio
async def test_dashboard_retention_block_carries_the_six_ttls(env):
    _, client, _, _ = env
    r = (await client.get("/api/admin/dashboard")).json()["retention"]
    assert set(r["ttl_days"]) == {"uploads", "interview_sessions", "generated_documents",
                                  "cancelled_applications", "profile_inactivity", "audit_log"}
    assert all(isinstance(v, int) for v in r["ttl_days"].values())
    assert r["enabled"] is True and r["changed_by_email"] is None


@pytest.mark.asyncio
async def test_dashboard_follows_the_retention_toggle_through_the_panel(env):
    db, client, _, _ = env
    off = await _put(client, {"RETENTION_ENABLED": False})
    assert off.status_code == 200, off.text

    body = (await client.get("/api/admin/dashboard")).json()
    ret = body["retention"]
    assert ret["enabled"] is False and ret["source"] == "panel"
    assert ret["changed_by_email"] == "admin@example.org"
    assert ret["enabled_since"] is None  # only while ON
    assert {"code": "retention_disabled", "severity": "warning"} in body["notices"]
    notices = (await client.get("/api/admin/notices")).json()
    assert notices["count"] >= 1
    assert {"code": "retention_disabled", "severity": "warning"} in notices["items"]

    on = await _put(client, {"RETENTION_ENABLED": True})
    assert on.status_code == 200, on.text
    body = (await client.get("/api/admin/dashboard")).json()
    ret = body["retention"]
    assert ret["enabled"] is True
    assert ret["enabled_since"] is not None
    assert ret["changed_by_email"] is None
    assert all(n["code"] != "retention_disabled" for n in body["notices"])


def test_failed_job_item_schema_has_exactly_the_agreed_fields():
    from applire.schemas.admin import FailedJobItem

    assert set(FailedJobItem.model_fields) == {"kind", "id", "user_id", "user_email", "failed_at", "error_code"}


def _columns(sql: str) -> set[tuple[str, str]]:
    return set(re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", sql))


def test_failed_job_statements_read_only_allowlisted_columns():
    stmts = failed_jobs.failed_job_statements()
    assert set(stmts) == {"cv", "cover_letter", "import", "gap"}
    tables = {"generated_cvs", "generated_cover_letters", "cv_import_jobs", "gap_analysis_jobs"}
    for name, stmt in stmts.items():
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        cols = _columns(sql)
        assert cols, (name, sql)
        for table, col in cols:
            assert table in tables, (name, table, sql)
            assert col in failed_jobs.ALLOWED_COLUMNS, (name, table, col, sql)
        assert "error_message" not in sql, name
    assert "error_message" not in failed_jobs.ALLOWED_COLUMNS


# =====================================================================================
# 7. Provider probe fingerprint (ADR-093)
# =====================================================================================


@pytest.fixture
def probe_world(monkeypatch):
    """A probe that can run with no network and no provider call."""
    calls = {"reachability": 0}

    async def reachability():
        calls["reachability"] += 1
        return "ok", ""

    async def credit(_family):
        return "n/a", {}

    monkeypatch.setattr(probes, "_probe_reachability", reachability)
    monkeypatch.setattr(probes, "_probe_credit", credit)
    monkeypatch.setattr(probes.ops_config, "provider_probe_enabled", lambda: True)
    monkeypatch.setattr(probes.ops_config, "reachability_probe_enabled", lambda: True)
    probes.reset_provider_cache()
    yield calls
    probes.reset_provider_cache()


@pytest.mark.asyncio
async def test_a_provider_switch_is_not_answered_from_the_previous_providers_cache(probe_world, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mistral")
    first = await probes.probe_provider()
    assert first.detail["provider"] == "mistral"
    assert probes._provider_cache is not None and probes._provider_cache[2].startswith("mistral|")

    # same config again: served from the cache, no second reachability call
    assert await probes.probe_provider() is first
    assert probe_world["reachability"] == 1

    app_config.set_overlay_latest({"llm_provider": "ollama"})  # the admin switches in the panel
    second = await probes.probe_provider()
    assert second is not first
    assert second.detail["provider"] == "ollama"
    assert probe_world["reachability"] == 2
    assert probes._provider_cache[2].startswith("ollama|")


def test_fingerprint_changes_with_family_model_and_key_but_never_holds_the_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_model", "a/b")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-fp-SENTINEL-1")
    base = probes._provider_fingerprint()
    assert "sk-fp-SENTINEL-1" not in base

    monkeypatch.setattr(settings, "openrouter_model", "a/c")
    assert probes._provider_fingerprint() != base
    monkeypatch.setattr(settings, "openrouter_model", "a/b")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-fp-SENTINEL-2")
    assert probes._provider_fingerprint() != base
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-fp-SENTINEL-1")
    assert probes._provider_fingerprint() == base
    app_config.set_overlay_latest({"llm_provider": "ollama"})
    assert probes._provider_fingerprint() != base
