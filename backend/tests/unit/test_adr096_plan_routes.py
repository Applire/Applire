# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 2 (amended 2026-11-23, K-3e) — ``/api/me/allowance``,
``/api/me/plan``, ``/api/me/purchases`` over HTTP, through the real auth chain.

The seam test for a Community install: no plan provider is registered, so the
capability field is false and both write routes answer 402 — whatever the
policy. With a provider (a fake standing in for an overlay) the routes delegate,
audit and answer the new status.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from applire.auth import get_auth_provider, links
from applire.config import settings
from applire.db.session import get_db
from applire.models.audit import AuditEvent
from applire.models.user import User
from applire.services import allowance, allowance_seam
from tests.support.owners_1b import add_user, client_for

ORIGIN = {"Origin": "http://applire.test"}


class Acting:
    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None
        self.via = "session"

    async def get_current_user(self, request, db):
        if self.user_id is None:
            return None
        request.state.auth_via = self.via
        return await db.get(User, self.user_id)


class Plans(allowance_seam.PlanProvider):
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.plan: str | None = None

    async def ensure_month_grant(self, db, *, user, subject, period, period_start, expires_at):
        if self.plan is None:
            return False
        await allowance.grant(db, subject=subject, action="application", amount=5,
                              source="subscription", expires_at=expires_at, ref=f"plan:{period}",
                              user_id=user.id)
        return True

    async def status(self, db, *, user, period_start):
        plan = {"id": self.plan, "label": "Jobseeker", "applications": 5, "price_cents": 990,
                "currency": "EUR"} if self.plan else None
        return {"plan": plan, "pending": None}

    async def catalog(self, db):
        return {"currency": "EUR", "simulated": True,
                "plans": [{"id": "jobseeker", "label": "Jobseeker", "applications": 5, "price_cents": 990}],
                "packs": [{"id": "pack5", "applications": 5, "price_cents": 990}]}

    async def change_plan(self, db, *, user, subject, plan_id):
        self.calls.append(("plan", plan_id, subject))
        if plan_id != "jobseeker":
            raise allowance_seam.PlanError("unknown_plan", 422, "There is no plan with this id.")
        previous, self.plan = self.plan, plan_id
        return {"change": "unchanged" if previous == plan_id else "chosen",
                "from_plan": previous, "to_plan": plan_id, "simulated": True}

    async def purchase(self, db, *, user, subject, pack_id, idempotency_key):
        self.calls.append(("purchase", pack_id, idempotency_key))
        return {"purchase_id": "p-1", "pack_id": pack_id, "applications": 5, "price_cents": 990,
                "amount_due_cents": 0, "simulated": True, "created": True}


@pytest.fixture(autouse=True)
def _clean():
    previous = links._instance_secret
    links.set_instance_secret("plan-route-tests-secret-0123456789")
    allowance_seam.reset_for_tests()
    yield
    allowance_seam.reset_for_tests()
    links._instance_secret = previous


@pytest_asyncio.fixture
async def env(async_db):
    from applire.routers import me_plan

    app = FastAPI()
    app.include_router(me_plan.router)
    acting = Acting()

    async def _db():
        yield async_db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    person = await add_user(async_db, email="tester-p@example.org")
    acting.user_id = person.id
    async with client_for(app) as client:
        yield async_db, client, acting, person


@pytest.mark.asyncio
@pytest.mark.parametrize("pol", ["none", "allowance"])
async def test_community_reports_no_purchasing_and_refuses_both_writes(env, monkeypatch, pol):
    db, client, *_ = env
    monkeypatch.setattr(settings, "allowance_policy", pol)
    r = await client.get("/api/me/allowance")
    assert r.status_code == 200 and r.json()["purchasing_available"] is False
    assert r.json()["plan"] is None and r.json()["catalog"] is None
    r = await client.put("/api/me/plan", json={"plan_id": "jobseeker"}, headers=ORIGIN)
    assert r.status_code == 402 and r.json()["detail"]["error_code"] == "purchasing_unavailable"
    r = await client.post("/api/me/purchases", json={"pack_id": "pack5", "idempotency_key": "k"}, headers=ORIGIN)
    assert r.status_code == 402
    assert (await db.execute(select(AuditEvent))).scalars().first() is None


@pytest.mark.asyncio
async def test_with_a_provider_but_policy_none_the_writes_stay_closed(env):
    _, client, *_ = env
    allowance_seam.register_plan_provider(Plans)
    r = await client.get("/api/me/allowance")
    assert r.json()["purchasing_available"] is False
    r = await client.put("/api/me/plan", json={"plan_id": "jobseeker"}, headers=ORIGIN)
    assert r.status_code == 402


@pytest.mark.asyncio
async def test_with_a_provider_the_plan_route_delegates_and_audits(env, monkeypatch):
    db, client, _, person = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    plans = Plans()
    allowance_seam.register_plan_provider(lambda: plans)
    before = (await client.get("/api/me/allowance")).json()
    assert before["purchasing_available"] is True and before["applications"]["has_plan"] is False
    r = await client.put("/api/me/plan", json={"plan_id": "jobseeker"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["change"] == "chosen" and body["allowance"]["plan"]["id"] == "jobseeker"
    assert plans.calls[0][2] == allowance.subject_hash("tester-p@example.org")
    (ev,) = (await db.execute(select(AuditEvent).where(AuditEvent.action == "plan.changed"))).scalars()
    assert ev.detail == {"change": "chosen", "from_plan": None, "to_plan": "jobseeker", "simulated": True}
    # choosing the same plan again: no audit row
    await client.put("/api/me/plan", json={"plan_id": "jobseeker"}, headers=ORIGIN)
    assert len((await db.execute(select(AuditEvent).where(AuditEvent.action == "plan.changed"))).scalars().all()) == 1
    r = await client.put("/api/me/plan", json={"plan_id": "enterprise"}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "unknown_plan"
    r = await client.put("/api/me/plan", json={"plan_id": "x" * 65}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "invalid_request"


@pytest.mark.asyncio
async def test_a_purchase_delegates_audits_and_needs_a_key(env, monkeypatch):
    db, client, *_ = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    plans = Plans()
    allowance_seam.register_plan_provider(lambda: plans)
    r = await client.post("/api/me/purchases", json={"pack_id": "pack5"}, headers=ORIGIN)
    assert r.status_code == 422
    r = await client.post("/api/me/purchases", json={"pack_id": "pack5", "idempotency_key": "k-1"}, headers=ORIGIN)
    assert r.status_code == 201, r.text
    assert r.json()["purchase"]["amount_due_cents"] == 0
    (ev,) = (await db.execute(select(AuditEvent).where(AuditEvent.action == "purchase.recorded"))).scalars()
    assert ev.detail == {"purchase_id": "p-1", "pack_id": "pack5", "applications": 5, "simulated": True}


@pytest.mark.asyncio
async def test_an_agent_token_cannot_choose_a_plan_or_buy(env, monkeypatch):
    _, client, acting, _ = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    allowance_seam.register_plan_provider(Plans)
    acting.via = "bearer"
    assert (await client.get("/api/me/allowance")).status_code == 200
    r = await client.put("/api/me/plan", json={"plan_id": "jobseeker"}, headers=ORIGIN)
    assert r.status_code == 403
    r = await client.post("/api/me/purchases", json={"pack_id": "pack5", "idempotency_key": "k"}, headers=ORIGIN)
    assert r.status_code == 403


# ── DH item 5 (delivery run 2026-11-25): the web asks the allowance BEFORE the tick list ──


@pytest.mark.asyncio
async def test_allowance_names_the_refusal_a_new_job_would_get_before_the_tick_list(env, monkeypatch):
    db, client, _, person = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_applications_per_month", 1)
    started, new = uuid.uuid4(), uuid.uuid4()
    r = await client.get("/api/me/allowance", params={"job_id": str(new)})
    assert r.status_code == 200 and r.json()["application_refusal"] is None  # 1 left
    await allowance.debit_for_door(db, person.id, "application", ref=str(started))
    await db.commit()
    body = (await client.get("/api/me/allowance", params={"job_id": str(new)})).json()
    assert body["application_refusal"] == {
        "error_code": "allowance_exhausted", "action": "application", "renews_on": body["renews_on"],
    }
    # the job that already counts is free (a second document, a new version)
    r = await client.get("/api/me/allowance", params={"job_id": str(started)})
    assert r.json()["application_refusal"] is None
    # no job named: the field is not computed
    assert (await client.get("/api/me/allowance")).json().get("application_refusal") is None


@pytest.mark.asyncio
async def test_allowance_refusal_preview_is_null_when_nothing_is_metered(env, monkeypatch):
    _, client, *_ = env
    monkeypatch.setattr(settings, "allowance_policy", "none")
    r = await client.get("/api/me/allowance", params={"job_id": str(uuid.uuid4())})
    assert r.status_code == 200 and r.json().get("application_refusal") is None


@pytest.mark.asyncio
async def test_allowance_refusal_preview_says_plan_required_without_a_plan(env, monkeypatch):
    _, client, *_ = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    allowance_seam.register_plan_provider(Plans)
    r = await client.get("/api/me/allowance", params={"job_id": str(uuid.uuid4())})
    assert r.json()["application_refusal"]["error_code"] == "plan_required"


@pytest.mark.asyncio
async def test_allowance_refusal_preview_rejects_a_malformed_job_id(env, monkeypatch):
    _, client, *_ = env
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    r = await client.get("/api/me/allowance", params={"job_id": "x" * 200})
    assert r.status_code == 422
