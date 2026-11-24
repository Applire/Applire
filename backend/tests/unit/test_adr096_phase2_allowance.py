# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 2 (amended 2026-11-23; Sorbet invite-gate WP-A) — the
``allowance`` policy, debit allocation (MD-9), the daily cap and the plan seam.

Each guard named in WP-A's evidence contract has a test here that fails when the
guard is removed (mutation kills recorded in ``invite-gate/a/report.md``):

* refusal at zero (``allowance_exhausted`` with ``renews_on``);
* earliest-expiring first (the month's grant before purchased applications);
* non-rolling months (last month's debits stay with last month's grant);
* one debit per job (a regeneration is free, also in a later month);
* the daily cap and the fail-closed rule refuse instance-paid admission;
* no fallback to the instance key (row 7 stays refused under ``none``);
* the capability field is false without a plan provider (a Community install).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from applire import ownership
from applire.auth import links
from applire.config import settings
from applire.models.allowance import AllowanceDaily, AllowanceLedger
from applire.models.user import User
from applire.services import allowance, allowance_seam

NOV = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
DEC = datetime(2026, 12, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    allowance.reset_context_for_tests()
    allowance_seam.reset_for_tests()
    links.set_instance_secret("instance-secret-for-phase2-tests")
    yield
    allowance.reset_context_for_tests()
    allowance_seam.reset_for_tests()
    links.set_instance_secret(None)


@pytest.fixture
def allowance_on(monkeypatch):
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_applications_per_month", 2)
    monkeypatch.setattr(settings, "allowance_imports_per_month", 3)
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 1000)


async def _person(db, email="tester-a@example.org") -> User:
    user = User(email=email)
    db.add(user)
    await db.flush()
    return user


async def _debits(db, action="application") -> list[AllowanceLedger]:
    rows = await db.execute(
        select(AllowanceLedger).where(AllowanceLedger.kind == "debit", AllowanceLedger.action == action)
    )
    return list(rows.scalars())


class FakePlans(allowance_seam.PlanProvider):
    """A plan provider with one fixed plan per person (or none)."""

    name = "fake"

    def __init__(self, plans: dict[uuid.UUID, int] | None = None) -> None:
        self.plans = plans or {}

    async def ensure_month_grant(self, db, *, user, subject, period, period_start, expires_at):
        amount = self.plans.get(user.id)
        if amount is None:
            return False
        await allowance.grant(
            db, subject=subject, action="application", amount=amount,
            source=allowance.SOURCE_SUBSCRIPTION, expires_at=expires_at, ref=f"plan:{period}",
            user_id=user.id,
        )
        return True

    async def status(self, db, *, user, period_start):
        amount = self.plans.get(user.id)
        plan = {"id": "p", "applications": amount, "price_cents": 990, "currency": "EUR"} if amount else None
        return {"plan": plan, "pending": None}

    async def catalog(self, db):
        return {"currency": "EUR", "plans": [], "packs": [], "simulated": True}

    async def change_plan(self, db, *, user, subject, plan_id):
        return {"change": "unchanged"}

    async def purchase(self, db, *, user, subject, pack_id, idempotency_key):
        await allowance.grant(
            db, subject=subject, action="application", amount=5,
            source=allowance.SOURCE_PURCHASE, expires_at=None, ref=f"purchase:{idempotency_key}",
            user_id=user.id,
        )
        return {"purchase_id": idempotency_key, "applications": 5, "created": True}


# ── the policy switch ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_none_debits_nothing_and_writes_no_row(async_db):
    person = await _person(async_db)
    for i in range(20):
        await allowance.debit_for_door(async_db, person.id, "application", ref=f"job-{i}", now=NOV)
    count = (await async_db.execute(select(func.count()).select_from(AllowanceLedger))).scalar_one()
    assert count == 0


def test_an_unknown_policy_value_reads_as_none(monkeypatch):
    from applire.config import Settings

    assert Settings(allowance_policy="Allowance").allowance_policy == "allowance"
    assert Settings(allowance_policy="alowance").allowance_policy == "none"
    assert Settings(allowance_policy="").allowance_policy == "none"


# ── refusal at zero, one debit per job ────────────────────────────────────────


@pytest.mark.asyncio
async def test_refusal_at_zero_names_the_action_and_the_renewal(async_db, allowance_on):
    person = await _person(async_db)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=NOV)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-2", now=NOV)
    with pytest.raises(allowance.AdmissionRefused) as exc:
        await allowance.debit_for_door(async_db, person.id, "application", ref="job-3", now=NOV)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "allowance_exhausted"
    assert exc.value.detail["action"] == "application"
    assert exc.value.detail["renews_on"] == "2026-12-01"
    assert len(await _debits(async_db)) == 2


@pytest.mark.asyncio
async def test_a_regeneration_on_a_debited_job_is_free_also_next_month(async_db, allowance_on):
    person = await _person(async_db)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=NOV)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-2", now=NOV)
    # at zero, the started application can be finished (cl. 7; ADR-097 cl. 5 precedent)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=NOV)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=DEC)
    assert len(await _debits(async_db)) == 2


@pytest.mark.asyncio
async def test_imports_are_metered_three_a_month(async_db, allowance_on):
    person = await _person(async_db)
    for i in range(3):
        await allowance.debit_for_door(async_db, person.id, "profile_import", ref=f"imp-{i}", now=NOV)
    with pytest.raises(allowance.AdmissionRefused) as exc:
        await allowance.debit_for_door(async_db, person.id, "profile_import", ref="imp-4", now=NOV)
    assert exc.value.detail["action"] == "profile_import"


# ── allocation (MD-9) ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_non_rolling_month_starts_full(async_db, allowance_on):
    person = await _person(async_db)
    await allowance.debit_for_door(async_db, person.id, "application", ref="nov-1", now=NOV)
    await allowance.debit_for_door(async_db, person.id, "application", ref="nov-2", now=NOV)
    subject = person.allowance_subject
    assert await allowance.balance(async_db, subject=subject, action="application", now=NOV) == 0
    # December: November's debits stay with November's grant.
    await allowance.debit_for_door(async_db, person.id, "application", ref="dec-1", now=DEC)
    await allowance.debit_for_door(async_db, person.id, "application", ref="dec-2", now=DEC)
    assert await allowance.balance(async_db, subject=subject, action="application", now=DEC) == 0


@pytest.mark.asyncio
async def test_the_months_grant_is_spent_before_purchased_applications(async_db, allowance_on):
    person = await _person(async_db)
    subject = await allowance.ensure_subject(async_db, person)
    # purchased first (older row), never expiring
    await allowance.grant(
        async_db, subject=subject, action="application", amount=5, source="purchase",
        expires_at=None, ref="purchase:1", user_id=person.id,
    )
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=NOV)
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-2", now=NOV)
    grants = {
        r.id: r for r in (await async_db.execute(
            select(AllowanceLedger).where(AllowanceLedger.kind == "grant")
        )).scalars()
    }
    spent_from = [grants[d.grant_id].source for d in await _debits(async_db)]
    assert spent_from == ["monthly", "monthly"]
    # the month's 2 are gone; the purchased 5 carry over into December untouched
    assert await allowance.balance(async_db, subject=subject, action="application", now=NOV) == 5
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-3", now=NOV)
    third = (await _debits(async_db))[-1]
    assert grants[third.grant_id].source == "purchase"
    # December: the new month's grant goes first again, the purchase keeps its 4
    await allowance.debit_for_door(async_db, person.id, "application", ref="job-4", now=DEC)
    assert grants.get((await _debits(async_db))[-1].grant_id) is None  # a new (December) grant
    assert await allowance.balance(async_db, subject=subject, action="application", now=DEC) == 1 + 4


# ── who is metered ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_own_key_application_is_not_metered_but_an_import_is(async_db, allowance_on):
    person = await _person(async_db)
    allowance._decision.set(allowance.PayerDecision("user", "model_work", person.id, family="mistral"))
    for i in range(4):
        await allowance.debit_for_door(async_db, person.id, "application", ref=f"job-{i}", now=NOV)
    assert await _debits(async_db) == []
    for i in range(3):
        await allowance.debit_for_door(async_db, person.id, "profile_import", ref=f"imp-{i}", now=NOV)
    with pytest.raises(allowance.AdmissionRefused):
        await allowance.debit_for_door(async_db, person.id, "profile_import", ref="imp-x", now=NOV)


# ── the plan seam ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_a_plan_provider_no_plan_means_plan_required(async_db, allowance_on):
    person = await _person(async_db)
    allowance_seam.register_plan_provider(lambda: FakePlans({}))
    with pytest.raises(allowance.AdmissionRefused) as exc:
        await allowance.debit_for_door(async_db, person.id, "application", ref="job-1", now=NOV)
    assert exc.value.detail["error_code"] == "plan_required"
    # imports are not part of a plan (K-3b): still Core's monthly grant
    await allowance.debit_for_door(async_db, person.id, "profile_import", ref="imp-1", now=NOV)


@pytest.mark.asyncio
async def test_with_a_plan_the_plans_number_replaces_the_setting(async_db, allowance_on):
    person = await _person(async_db)
    allowance_seam.register_plan_provider(lambda: FakePlans({person.id: 5}))
    for i in range(5):
        await allowance.debit_for_door(async_db, person.id, "application", ref=f"job-{i}", now=NOV)
    with pytest.raises(allowance.AdmissionRefused) as exc:
        await allowance.debit_for_door(async_db, person.id, "application", ref="job-6", now=NOV)
    assert exc.value.detail["error_code"] == "allowance_exhausted"


@pytest.mark.asyncio
async def test_the_capability_is_false_on_community_and_under_none(async_db, monkeypatch):
    person = await _person(async_db)
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["purchasing_available"] is False and status["policy"] == "none"
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["purchasing_available"] is False  # no provider registered
    assert status["plan"] is None and status["catalog"] is None
    allowance_seam.register_plan_provider(lambda: FakePlans({person.id: 5}))
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["purchasing_available"] is True
    monkeypatch.setattr(settings, "allowance_policy", "none")
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["purchasing_available"] is False


def test_core_registers_no_plan_provider_of_its_own():
    """A Community install: nothing in ``applire`` (outside tests) registers one."""
    import subprocess
    import sys

    code = (
        "import applire.config, applire.main\n"
        "from applire.services import allowance_seam as s\n"
        "assert s.plan_provider() is None and not s.purchasing_available()\n"
    )
    env = {"DATABASE_URL": "sqlite+aiosqlite://", "AUTH_HARNESS": "true", "PATH": "/usr/bin:/bin"}
    import os

    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "backend")
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]


@pytest.mark.asyncio
async def test_status_counts_the_month_and_the_purchased(async_db, allowance_on):
    person = await _person(async_db)
    allowance_seam.register_plan_provider(lambda: FakePlans({person.id: 5}))
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["applications"]["limit"] == 5 and status["applications"]["used"] == 0
    assert status["renews_on"] == "2026-12-01"
    for i in range(3):
        await allowance.debit_for_door(async_db, person.id, "application", ref=f"job-{i}", now=NOV)
    await FakePlans().purchase(
        async_db, user=person, subject=person.allowance_subject, pack_id="p5", idempotency_key="k1"
    )
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status["applications"] == {
        "limit": 5, "used": 3, "remaining": 2, "purchased_remaining": 5, "has_plan": True,
    }
    assert status["imports"]["limit"] == 3 and status["imports"]["used"] == 0


# ── the daily cap, fail closed (cl. 8) ────────────────────────────────────────


def _instance_facts(owner, **kw):
    return allowance.PayerFacts(owner_id=owner, mode="instance", **kw)


def test_the_daily_cap_refuses_instance_paid_work_only_under_allowance(monkeypatch):
    owner = uuid.uuid4()
    facts = _instance_facts(owner, day_cap_reached=True)
    assert allowance.decide(facts, "model_work").payer == "instance"  # policy none
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "instance_daily_limit"


def test_the_counter_off_refuses_instance_paid_work_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    facts = _instance_facts(uuid.uuid4(), counting_ok=False)
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "allowance_unavailable" and exc.value.status_code == 503


def test_row_7_stays_refused_under_none_and_row_6_pays_instance_under_allowance(monkeypatch):
    facts = allowance.PayerFacts(owner_id=uuid.uuid4(), mode="user")
    with pytest.raises(allowance.AdmissionRefused) as exc:
        allowance.decide(facts, "model_work")
    assert exc.value.code == "provider_key_required"
    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    assert allowance.decide(facts, "model_work").payer == "instance"


@pytest.mark.asyncio
async def test_the_usage_sink_adds_instance_tokens_to_the_day(async_db, allowance_on):
    with ownership.unscoped("tooling"):
        await allowance.add_daily_tokens(async_db, payer="instance", tokens=400, now=NOV)
        await allowance.add_daily_tokens(async_db, payer="system", tokens=300, now=NOV)
        await allowance.add_daily_tokens(async_db, payer="user", tokens=9999, now=NOV)
        assert await allowance.day_total(async_db, now=NOV) == 700
        assert not await allowance.day_cap_reached(async_db, now=NOV)
        await allowance.add_daily_tokens(async_db, payer="instance", tokens=300, now=NOV)
        assert await allowance.day_cap_reached(async_db, now=NOV)
        # the next Berlin day starts at zero
        assert not await allowance.day_cap_reached(async_db, now=NOV + timedelta(days=1))


@pytest.mark.asyncio
async def test_under_none_the_sink_writes_no_daily_row(async_db):
    await allowance.add_daily_tokens(async_db, payer="instance", tokens=400, now=NOV)
    count = (await async_db.execute(select(func.count()).select_from(AllowanceDaily))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_load_facts_reads_the_cap_under_allowance(async_db, allowance_on):
    person = await _person(async_db)
    await allowance.add_daily_tokens(async_db, payer="instance", tokens=5000)
    facts = await allowance.load_facts(async_db, person)
    assert facts.day_cap_reached is True
    with pytest.raises(allowance.AdmissionRefused):
        allowance.decide(facts, "model_work")


# ── the month boundary ────────────────────────────────────────────────────────


def test_the_month_is_the_berlin_calendar_month():
    # 30 Nov 23:30 UTC is already 1 Dec in Berlin
    period, start, expires = allowance.month_period(datetime(2026, 11, 30, 23, 30, tzinfo=timezone.utc))
    assert period == "2026-12" and str(start) == "2026-12-01"
    assert expires == datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
    period, _, expires = allowance.month_period(datetime(2026, 11, 30, 22, 30, tzinfo=timezone.utc))
    assert period == "2026-11" and expires == datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)
    assert str(allowance.renews_on(datetime(2026, 12, 20, tzinfo=timezone.utc))) == "2027-01-01"


# ── cl. 9: the subject at creation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_subject_is_stamped_at_account_creation(async_db):
    person = await _person(async_db, "Anna.Muster+jobs@Gmail.com")
    assert person.allowance_subject == allowance.subject_hash("annamuster@gmail.com")


@pytest.mark.asyncio
async def test_without_the_instance_secret_creation_still_works_and_stays_lazy(async_db):
    links.set_instance_secret(None)
    person = await _person(async_db, "cli@example.org")
    assert person.allowance_subject is None
    links.set_instance_secret("instance-secret-for-phase2-tests")
    assert await allowance.ensure_subject(async_db, person) == allowance.subject_hash("cli@example.org")


@pytest.mark.asyncio
async def test_an_email_change_restamps_but_the_tombstone_keeps_its_clear(async_db):
    person = await _person(async_db, "one@example.org")
    person.email = "two@example.org"
    await async_db.flush()
    assert person.allowance_subject == allowance.subject_hash("two@example.org")
    person.email = f"deleted+{person.id}@invalid"
    person.allowance_subject = None
    await async_db.flush()
    assert person.allowance_subject is None


# ── start validation ──────────────────────────────────────────────────────────


def test_allowance_needs_a_daily_cap_and_noop_embeddings(monkeypatch):
    from applire import main

    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 0)
    monkeypatch.setattr(settings, "embedding_provider", "mistral")
    problems = main.allowance_env_problems()
    assert len(problems) == 2
    with pytest.raises(RuntimeError):
        main._validate_allowance_env()
    monkeypatch.setattr(settings, "allowance_policy", "none")
    assert main.allowance_env_problems() == []


def test_allowance_refuses_to_start_with_usage_tracking_off(monkeypatch):
    """adv/plans F3: without usage tracking the counter cannot count, so every
    instance-paid action would answer allowance_unavailable for good."""
    from applire import main
    from applire.services.ops import config as ops_config

    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 1000)
    monkeypatch.setattr(settings, "embedding_provider", "noop")
    assert main.allowance_env_problems() == []
    for off in ("off", "false", "0", "no"):
        monkeypatch.setattr(ops_config, "LLM_USAGE_TRACKING", off)
        problems = main.allowance_env_problems()
        assert len(problems) == 1 and "LLM_USAGE_TRACKING" in problems[0], off
        with pytest.raises(RuntimeError, match="LLM_USAGE_TRACKING"):
            main._validate_allowance_env()
    monkeypatch.setattr(settings, "allowance_policy", "none")
    assert main.allowance_env_problems() == []  # Community default: tracking may be off


def test_a_start_refusal_names_its_fix_in_the_log(monkeypatch):
    """adv/plans F3, live: the log boundary keeps only the head line of the
    server's startup traceback, so the RuntimeError text never reached the
    container log ("Application startup failed" and an error id). The refusal
    is logged as our own record, which the boundary passes whole."""
    import io
    import logging

    from applire import log_boundary, main
    from applire.services.ops import config as ops_config

    monkeypatch.setattr(settings, "allowance_policy", "allowance")
    monkeypatch.setattr(settings, "allowance_tokens_per_day", 1000)
    monkeypatch.setattr(settings, "embedding_provider", "noop")
    monkeypatch.setattr(ops_config, "LLM_USAGE_TRACKING", "off")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log_boundary.install(handler)
    logger = logging.getLogger("applire")
    logger.addHandler(handler)
    try:
        with pytest.raises(RuntimeError):
            main._validate_allowance_env()
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "LLM_USAGE_TRACKING" in out and "ALLOWANCE_POLICY=none" in out, out


# ── default mode leaks nothing (policy none) ──────────────────────────────────


@pytest.mark.asyncio
async def test_policy_none_never_reads_the_daily_counter_or_the_sink_state(async_db, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("policy none read the allowance counter")

    def boom_sync(*a, **k):
        raise AssertionError("policy none read the sink state")

    monkeypatch.setattr(allowance, "day_cap_reached", boom)
    monkeypatch.setattr(allowance, "usage_counting_ok", boom_sync)
    person = await _person(async_db)
    facts = await allowance.load_facts(async_db, person)
    assert facts.day_cap_reached is False and facts.counting_ok is True
    assert allowance.decide(allowance.PayerFacts(owner_id=person.id, mode="instance"), "model_work").payer == "instance"
    status = await allowance.allowance_status(async_db, person, now=NOV)
    assert status == {
        "policy": "none", "purchasing_available": False, "renews_on": "2026-12-01",
        "applications": None, "imports": None, "plan": None, "pending": None, "catalog": None,
    }


# ── adv/plans re-probe N5: the refusal names its own wording ──────────────────


@pytest.mark.parametrize("code", ["instance_daily_limit", "allowance_unavailable"])
def test_an_instance_refusal_says_whether_it_comes_from_the_test_system(code, monkeypatch):
    """The dialog picks "Das Testsystem …" or "Diese Instanz …" (A-4) from the
    refusal itself, so it is right before the allowance data has loaded and
    while that read fails (likely in the very outage the dialog is about)."""
    monkeypatch.setattr(allowance_seam, "purchasing_available", lambda: False)
    assert allowance.AdmissionRefused(code).detail["purchasing_available"] is False
    monkeypatch.setattr(allowance_seam, "purchasing_available", lambda: True)
    assert allowance.AdmissionRefused(code).detail["purchasing_available"] is True


def test_a_persons_own_refusal_carries_no_instance_flag():
    assert "purchasing_available" not in allowance.AdmissionRefused("allowance_exhausted", action="application").detail
