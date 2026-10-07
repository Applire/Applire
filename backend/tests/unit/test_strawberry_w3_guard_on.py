# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry W3 / 3e — the owner guard is ON, fail-closed (ADR-092 cl. 6–8; MD-24).

* A real request path whose dependency names no owner raises
  ``OwnerContextMissing`` — and the same request passes with the guard off
  (the baseline the guard rescues) and with the real auth dependency.
* A cross-user rule run without its declared ``unscoped`` reason raises.
* The 0-fallback ratchet (MD-24 (2)) records a door and ignores a test-direct call.
* The orchestrator checks the artifact's owner before the transition (MD-24 (5)).
* One owner-helper module; the W2 modules are re-export shims (MD-24 (4)).
* Profile routes never echo ``str(exc)`` in a 500 (MD-24 (6)).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from applire import ownership
from applire.auth.deps import require_user
from applire.db.session import get_db
from applire.main import app
from applire.models.user import User
from tests.support import owner_ratchet

APPLIRE = Path(__file__).resolve().parents[2] / "applire"


# ---------------------------------------------------------------------------
# Fail-closed: a request path without an owner context
# ---------------------------------------------------------------------------


async def _list_applications(async_db, dependency):
    async def _db():
        yield async_db

    app.dependency_overrides[get_db] = _db
    if dependency is not None:
        app.dependency_overrides[require_user] = dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.get("/api/applications")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_user, None)


def _user_without_owner_context():
    """A dependency that authenticates but never names the owner — the shape of a
    sync dependency (threadpool copy) or a forgotten ``set_owner``."""
    uid = uuid.uuid4()

    async def _dep():
        return User(id=uid, email=f"{uid.hex[:8]}@example.org")

    return _dep


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_a_request_without_an_owner_context_raises(async_db, monkeypatch):
    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)
    assert ownership.current_owner() is None
    with pytest.raises(ownership.OwnerContextMissing, match="applications"):
        await _list_applications(async_db, _user_without_owner_context())


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_baseline_the_same_request_runs_with_the_guard_off(async_db, monkeypatch):
    """The arm the guard rescues: guard off, the ownerless request is served."""
    monkeypatch.setattr(ownership, "GUARD_ENABLED", False)
    resp = await _list_applications(async_db, _user_without_owner_context())
    assert resp.status_code == 200


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_the_real_auth_dependency_names_the_owner(async_db, monkeypatch):
    """The production dependency (harness provider here) sets the context."""
    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)
    resp = await _list_applications(async_db, None)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Fail-closed: a cross-user rule without its declared reason
# ---------------------------------------------------------------------------


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_a_retention_rule_without_its_declared_reason_raises(async_db, monkeypatch):
    from applire.retention.worker import _tombstone_inactive_profiles

    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)
    with pytest.raises(ownership.OwnerContextMissing):
        await _tombstone_inactive_profiles(async_db)
    await async_db.rollback()
    with ownership.unscoped("retention"):  # the declared entry point's context
        assert await _tombstone_inactive_profiles(async_db) == 0


def test_an_undeclared_reason_is_refused():
    with pytest.raises(ValueError, match="closed list"):
        with ownership.unscoped("cleanup"):  # type: ignore[arg-type]
            pass


# ---------------------------------------------------------------------------
# The 0-fallback ratchet sees doors, not test-direct calls (MD-24 (2))
# ---------------------------------------------------------------------------


@pytest.mark.owner_fallback_allowed
@pytest.mark.asyncio
async def test_the_ratchet_records_a_fallback_reached_through_http():
    from fastapi import FastAPI

    from applire.services.owner_resolution import resolve_user_id

    toy = FastAPI()

    @toy.get("/x")
    async def x():
        return {"uid": str(resolve_user_id(None, "toy.site"))}

    owner_ratchet.DOOR_HITS.clear()
    async with AsyncClient(transport=ASGITransport(app=toy), base_url="http://t") as c:
        assert (await c.get("/x")).status_code == 200
    hits = list(owner_ratchet.DOOR_HITS)
    owner_ratchet.DOOR_HITS.clear()
    assert [(k, s) for k, s, _door in hits] == [("fallback", "toy.site")]


@pytest.mark.asyncio
async def test_the_ratchet_ignores_a_test_direct_fallback():
    from applire.services.owner_resolution import OWNER_FALLBACK_STATS, resolve_user_id

    before = OWNER_FALLBACK_STATS["toy.direct"]
    resolve_user_id(None, "toy.direct")
    assert OWNER_FALLBACK_STATS["toy.direct"] == before + 1
    assert owner_ratchet.DOOR_HITS == []


# ---------------------------------------------------------------------------
# Orchestrator: the artifact's owner before the transition (MD-24 (5))
# ---------------------------------------------------------------------------


async def _flow_and_cvs(db, a, b):
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="t", role_title="R",
        seniority_level="mid", language_requirement="English",
    )
    db.add(job)
    await db.flush()
    cvs = {}
    for owner in (a, b):
        profile = make_master_profile(user_id=owner.id, profile_json={})
        db.add(profile)
        await db.flush()
        cv = GeneratedCV(
            job_analysis_id=job.id, profile_id=profile.id, user_id=owner.id, tailored_data={}
        )
        db.add(cv)
        await db.flush()
        cvs[owner.id] = cv.id
    flow = FlowSession(user_id=a.id, job_id=job.id, current_step="jd_analysis")
    db.add(flow)
    await db.commit()
    return flow.id, cvs


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["foreign", "missing"])
async def test_a_foreign_or_missing_artifact_answers_not_found_before_the_transition(
    async_db, two_users, which
):
    """jd_analysis → complete is an INVALID transition; a foreign or missing CV id
    answers ArtifactNotFoundError there too (both doors map it the same way)."""
    from applire.schemas.flow import AdvanceFlowRequest
    from applire.services.flow.orchestrator import ArtifactNotFoundError, advance_flow

    a, b = two_users
    flow_id, cvs = await _flow_and_cvs(async_db, a, b)
    artifact = cvs[b.id] if which == "foreign" else uuid.uuid4()
    with ownership.owner_context(a.id), pytest.raises(ArtifactNotFoundError):
        await advance_flow(
            flow_id, AdvanceFlowRequest(step="complete", artifact_id=artifact), async_db,
            user_id=a.id,
        )


@pytest.mark.asyncio
async def test_an_own_artifact_at_an_invalid_transition_is_an_invalid_transition(
    async_db, two_users
):
    from applire.schemas.flow import AdvanceFlowRequest
    from applire.services.flow.orchestrator import InvalidTransitionError, advance_flow

    a, b = two_users
    flow_id, cvs = await _flow_and_cvs(async_db, a, b)
    with ownership.owner_context(a.id), pytest.raises(InvalidTransitionError):
        await advance_flow(
            flow_id, AdvanceFlowRequest(step="complete", artifact_id=cvs[a.id]), async_db,
            user_id=a.id,
        )


def test_the_mcp_door_no_longer_pre_checks_the_artifact():
    src = (APPLIRE / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "_owned_artifact" not in src


# ---------------------------------------------------------------------------
# One owner-helper module (MD-24 (4))
# ---------------------------------------------------------------------------


def test_the_w2_owner_helpers_are_shims_of_one_module():
    from applire.services import cv_owner, owner_resolution, owner_scope

    assert owner_scope.owned_row is owner_resolution.owned_row
    assert owner_scope.resolve_user_id is owner_resolution.resolve_user_id
    for name in ("owned_cv", "resolve_owner", "job_for_user", "OWNER_FALLBACK_STATS"):
        assert getattr(cv_owner, name) is getattr(owner_resolution, name)


def test_no_production_module_imports_the_shims():
    offenders = []
    for path in sorted(APPLIRE.rglob("*.py")):
        if path.name in ("cv_owner.py", "owner_scope.py"):
            continue
        src = path.read_text(encoding="utf-8")
        if "services.cv_owner import" in src or "services.owner_scope import" in src:
            offenders.append(str(path.relative_to(APPLIRE)))
    assert offenders == []


# ---------------------------------------------------------------------------
# Profile routes: no str(exc) in a 500 (MD-24 (6))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_profile_500_never_echoes_the_exception(async_client, monkeypatch):
    secret = "SQL touches owned table 'master_profiles' :: SELECT secret"

    async def _boom(*_a, **_kw):
        raise RuntimeError(secret)

    monkeypatch.setattr("applire.routers.profile.resolve_conflict", _boom)
    resp = await async_client.post(
        "/api/profile/conflicts/c1/resolve", json={"resolution": "existing"}
    )
    assert resp.status_code == 500
    assert secret not in resp.text
    assert resp.json()["detail"] == "An unexpected error occurred. Please try again."


def test_no_profile_route_echoes_str_exc_in_a_500():
    import re

    src = (APPLIRE / "routers" / "profile.py").read_text(encoding="utf-8")
    blocks = re.findall(r"HTTP_500_INTERNAL_SERVER_ERROR,?\s*detail=([^)\n]+)", src)
    assert blocks and all("str(exc)" not in b for b in blocks)
