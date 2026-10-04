# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared immutable posting cache (ADR-092 cl. 5, S-17, RD-2, MD-10; US334; Strawberry 4a)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from applire.models.application import Application
from applire.models.job import JobAnalysis
from applire.ownership import OwnedNotFound
from applire.services.job import ensure_application_link, get_job_for_user


async def _job(db, **kw) -> JobAnalysis:
    j = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Engineer, Python.",
        role_title=kw.pop("role_title", "Engineer"),
        company_name=kw.pop("company_name", "Acme"),
        language_requirement="English",
        **kw,
    )
    db.add(j)
    await db.flush()
    return j


@pytest.mark.asyncio
async def test_get_job_for_user_needs_a_link(async_db, two_users):
    a, b = two_users
    job = await _job(async_db)
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, job.id, a.id)
    await ensure_application_link(async_db, job, a.id)
    assert (await get_job_for_user(async_db, job.id, a.id)).id == job.id
    with pytest.raises(OwnedNotFound) as exc:
        await get_job_for_user(async_db, job.id, b.id)
    assert exc.value.status_code == 404 and exc.value.detail == "job not found"


@pytest.mark.asyncio
async def test_get_job_for_user_soft_deleted_link_still_counts(async_db, two_users):
    from datetime import datetime, timezone

    a, _ = two_users
    job = await _job(async_db)
    app = await ensure_application_link(async_db, job, a.id)
    app.deleted_at = datetime.now(timezone.utc)
    await async_db.flush()
    assert (await get_job_for_user(async_db, job.id, a.id)).id == job.id


@pytest.mark.asyncio
async def test_get_job_for_user_missing_and_deleted_posting_are_404(async_db, two_users):
    from datetime import datetime, timezone

    a, _ = two_users
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, uuid.uuid4(), a.id)
    job = await _job(async_db)
    await ensure_application_link(async_db, job, a.id)
    job.deleted_at = datetime.now(timezone.utc)
    await async_db.flush()
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, job.id, a.id)


@pytest.mark.asyncio
async def test_ensure_link_is_get_or_create_and_overrides_stay_private(async_db, two_users):
    a, b = two_users
    job = await _job(async_db, role_title="Engineer", company_name="Acme")
    app_a = await ensure_application_link(async_db, job, a.id, role_title_override="  Staff Engineer ")
    again = await ensure_application_link(async_db, job, a.id)
    assert again.id == app_a.id
    assert app_a.role_title == "Staff Engineer" and app_a.company_name == "Acme"
    assert app_a.user_status == "tracking"
    app_b = await ensure_application_link(async_db, job, b.id, company_name_override="Acme GmbH")
    assert app_b.id != app_a.id
    assert (app_b.role_title, app_b.company_name) == ("Engineer", "Acme GmbH")
    await async_db.refresh(job)
    assert (job.role_title, job.company_name) == ("Engineer", "Acme"), "shared posting untouched"
    n = await async_db.scalar(select(func.count()).select_from(Application))
    assert n == 2


@pytest.mark.asyncio
async def test_ensure_link_blank_override_keeps_value(async_db, two_users):
    a, _ = two_users
    job = await _job(async_db)
    await ensure_application_link(async_db, job, a.id, role_title_override="Lead")
    app = await ensure_application_link(async_db, job, a.id, role_title_override="   ")
    assert app.role_title == "Lead"


# ---------------------------------------------------------------------------
# Two users, one posting (US334 evidence) — through the REST doors
# ---------------------------------------------------------------------------

_JD = (
    "Senior Platform Engineer at Nordlicht GmbH. Required: Python, Kubernetes, "
    "PostgreSQL. Nice to have: Terraform. Team of eight, hybrid in Hamburg."
)


def _jd_payload() -> dict:
    return {
        "role_title": "Senior Platform Engineer",
        "company_name": "Nordlicht GmbH",
        "required_skills": ["Python", "Kubernetes", "PostgreSQL"],
        "nice_to_have_skills": ["Terraform"],
        "keywords": ["Python"],
        "seniority_level": "senior",
        "company_culture_signals": [],
        "language_requirement": "English",
    }


class _AsUser:
    def __init__(self, user) -> None:  # noqa: ANN001
        self.user = user

    async def get_current_user(self, request, db=None):  # noqa: ANN001
        return self.user


@pytest.fixture
def no_review_loop(monkeypatch):
    """analyze_jd's reviewer loop is out of scope here — the draft settles as-is."""

    async def _settle(*, draft, **_kw):  # noqa: ANN001
        return draft

    monkeypatch.setattr("applire.services.job.review_and_refine", _settle)


async def _call(async_db, user, method: str, path: str, json: dict | None = None, provider=None):
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.main import app
    from applire.routers.job import _get_provider

    async def _db():
        yield async_db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsUser(user)
    if provider is not None:
        app.dependency_overrides[_get_provider] = lambda: provider
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)
        app.dependency_overrides.pop(_get_provider, None)


@pytest.mark.asyncio
async def test_two_users_share_one_posting_but_nothing_personal(async_db, two_users, no_review_loop):
    """US334 / S-17 / RD-2: A and B analyse the same posting → ONE job_analyses row,
    analysed once (one LLM call); each gets their own application, flow and gap
    analysis; each one's title override is private; the shared row is untouched."""
    from unittest.mock import AsyncMock

    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.services.job import analyze_jd

    a, b = two_users
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_jd_payload())

    ra = await analyze_jd(_JD, async_db, provider, user_id=a.id, role_title_override="Platform Lead")
    rb = await analyze_jd(_JD, async_db, provider, user_id=b.id, company_name_override="Nordlicht")
    assert ra.id == rb.id, "one shared posting row"
    assert provider.aparse_json.await_count == 1, "the second user is a cache hit — analysed once"
    assert (await async_db.scalar(select(func.count()).select_from(JobAnalysis))) == 1

    # private labels in the analyze responses …
    assert (ra.role_title, ra.company_name) == ("Platform Lead", "Nordlicht GmbH")
    assert (rb.role_title, rb.company_name) == ("Senior Platform Engineer", "Nordlicht")
    # … the shared row carries only what the POSTING says (cl. 5a) …
    job = await async_db.get(JobAnalysis, ra.id)
    await async_db.refresh(job)
    assert (job.role_title, job.company_name) == ("Senior Platform Engineer", "Nordlicht GmbH")
    # … and each user reads their own labels back through GET /api/job/{id}.
    ga = await _call(async_db, a, "GET", f"/api/job/{ra.id}")
    gb = await _call(async_db, b, "GET", f"/api/job/{ra.id}")
    assert ga.status_code == gb.status_code == 200
    assert ga.json()["role_title"] == "Platform Lead" and ga.json()["company_name"] == "Nordlicht GmbH"
    assert gb.json()["role_title"] == "Senior Platform Engineer" and gb.json()["company_name"] == "Nordlicht"

    # separate applications …
    apps = (await async_db.execute(select(Application).where(Application.job_analysis_id == ra.id))).scalars().all()
    assert sorted(x.user_id for x in apps) == sorted([a.id, b.id])

    # … separate flows (create_flow per user, same posting) …
    fa = await _call(async_db, a, "POST", "/api/flow", json={"job_id": str(ra.id)})
    fb = await _call(async_db, b, "POST", "/api/flow", json={"job_id": str(ra.id)})
    assert fa.status_code == fb.status_code == 201, (fa.text, fb.text)
    assert fa.json()["flow_id"] != fb.json()["flow_id"]
    assert fa.json()["job_summary"]["role_title"] == "Platform Lead"
    assert fb.json()["job_summary"]["role_title"] == "Senior Platform Engineer"
    flows = (await async_db.execute(select(FlowSession).where(FlowSession.job_id == ra.id))).scalars().all()
    assert sorted(f.user_id for f in flows) == sorted([a.id, b.id])
    # B cannot read A's flow
    other = await _call(async_db, b, "GET", f"/api/flow/{fa.json()['flow_id']}/state")
    assert other.status_code == 404

    # … separate gap analyses: each GET /gaps answers with the caller's own row.
    from tests.support.profile_factory import make_master_profile

    for owner, score in ((a, 0.4), (b, 0.9)):
        profile = make_master_profile(user_id=owner.id, profile_json={"personal_info": {"name": "X"}})
        async_db.add(profile)
        await async_db.flush()
        async_db.add(GapAnalysis(job_analysis_id=ra.id, profile_id=profile.id, user_id=owner.id, match_score=score))
    await async_db.commit()
    sa = await _call(async_db, a, "GET", f"/api/job/{ra.id}/gaps")
    sb = await _call(async_db, b, "GET", f"/api/job/{ra.id}/gaps")
    assert sa.status_code == sb.status_code == 200, (sa.text, sb.text)
    assert sa.json()["match_score"] == 0.4 and sb.json()["match_score"] == 0.9


@pytest.mark.asyncio
async def test_a_third_user_without_a_link_gets_404_everywhere(async_db, two_users, no_review_loop):
    from unittest.mock import AsyncMock

    from applire.services.job import analyze_jd
    from tests.support.owners import make_user

    a, _ = two_users
    c = await make_user(async_db, email="owner-c@example.org")
    await async_db.commit()
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_jd_payload())
    job = await analyze_jd(_JD, async_db, provider, user_id=a.id)
    for method, path, body in (
        ("GET", f"/api/job/{job.id}", None),
        ("GET", f"/api/job/{job.id}/gaps", None),
        ("POST", f"/api/job/{job.id}/gap-jobs", None),
        ("POST", "/api/flow", {"job_id": str(job.id)}),
        ("POST", "/api/applications", {"job_analysis_id": str(job.id)}),
    ):
        resp = await _call(async_db, c, method, path, json=body)
        assert resp.status_code == 404, f"{method} {path} → {resp.status_code} {resp.text[:120]}"


@pytest.mark.asyncio
async def test_url_dedup_only_for_scraped_text(async_db, two_users, no_review_loop):
    """MD-10: text a person pasted (with a URL) must never be served to another
    user who asks for that URL — only text Applire scraped itself is URL-shared."""
    from unittest.mock import AsyncMock

    from applire.services.job import analyze_jd

    a, b = two_users
    url = "https://jobs.example/nordlicht/42"
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_jd_payload())

    pasted = await analyze_jd(
        _JD + " MY PRIVATE NOTE: salary 95k agreed with recruiter.", async_db, provider,
        source_url=url, user_id=a.id, raw_text_origin="supplied",
    )
    scraped_b = await analyze_jd(_JD, async_db, provider, source_url=url, user_id=b.id)
    assert scraped_b.id != pasted.id, "B's scrape of the URL must not hit A's pasted row"
    assert "PRIVATE" not in (await async_db.get(JobAnalysis, scraped_b.id)).raw_text
    assert provider.aparse_json.await_count == 2

    # a second scrape of the same URL (A this time, different page text) IS a hit
    again = await analyze_jd(_JD + " (updated)", async_db, provider, source_url=url, user_id=a.id)
    assert again.id == scraped_b.id
    assert provider.aparse_json.await_count == 2
    origins = dict((await async_db.execute(select(JobAnalysis.id, JobAnalysis.raw_text_origin))).all())
    assert origins == {pasted.id: "supplied", scraped_b.id: "scraped"}


@pytest.mark.asyncio
async def test_repost_gets_a_hidden_link_no_phantom_card(async_db, two_users, no_review_loop):
    """4a-1 (recommendation B): a Branch-F repost (same text, other URL) is linked
    but hidden; the user's existing card is the only visible one."""
    from unittest.mock import AsyncMock

    from applire.services.job import analyze_jd

    a, _ = two_users
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_jd_payload())
    first = await analyze_jd(_JD, async_db, provider, source_url="https://board-a.example/1", user_id=a.id)
    repost = await analyze_jd(_JD + " ", async_db, provider, source_url="https://board-b.example/9", user_id=a.id)
    assert repost.id != first.id and repost.duplicate_of is not None
    assert repost.duplicate_of.matched_on == "text"
    visible = (await async_db.execute(
        select(Application).where(Application.user_id == a.id, Application.deleted_at.is_(None))
    )).scalars().all()
    assert [x.job_analysis_id for x in visible] == [first.id]
    assert (await get_job_for_user(async_db, repost.id, a.id)).id == repost.id, "access via hidden link"


@pytest.mark.asyncio
async def test_analyze_without_owner_refuses(async_db, no_review_loop):
    """Ruling 3d-1: no user_id and no user owner context → OwnerContextMissing."""
    from unittest.mock import AsyncMock

    from applire import ownership
    from applire.services.job import analyze_jd

    provider = AsyncMock()
    with ownership.unscoped("tooling"):
        with pytest.raises(ownership.OwnerContextMissing):
            await analyze_jd(_JD, async_db, provider)
    provider.aparse_json.assert_not_called()


@pytest.mark.asyncio
async def test_analyze_retries_once_when_the_link_insert_hits_an_integrity_error(
    async_db, two_users, no_review_loop, monkeypatch
):
    """ADR-092 cl. 11 / SF-OWN.4: a concurrent erasure deletes the posting between
    the cache hit and the link insert → FK error → the dedup runs once more."""
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError

    import applire.services.job as job_svc

    a, _ = two_users
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_jd_payload())
    real = job_svc.ensure_application_link
    calls = []

    async def flaky(*args, **kw):  # noqa: ANN002, ANN003
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO applications", {}, Exception("FOREIGN KEY constraint failed"))
        return await real(*args, **kw)

    monkeypatch.setattr(job_svc, "ensure_application_link", flaky)
    res = await job_svc.analyze_jd(_JD, async_db, provider, user_id=a.id)
    assert len(calls) == 2
    assert (await get_job_for_user(async_db, res.id, a.id)).id == res.id


@pytest.mark.asyncio
async def test_gaps_door_requires_the_link_even_with_own_gap_rows(async_db, two_users):
    """MD-23: the gaps routes resolve the posting through get_job_for_user AT THE
    DOOR — the caller's own gap row on a posting they hold no link to (pre-0076
    data) is not a way in."""
    from applire.models.gap import GapAnalysis
    from tests.support.profile_factory import make_master_profile

    a, _ = two_users
    job = await _job(async_db)
    profile = make_master_profile(user_id=a.id, profile_json={"personal_info": {"name": "A"}})
    async_db.add(profile)
    await async_db.flush()
    async_db.add(GapAnalysis(job_analysis_id=job.id, profile_id=profile.id, user_id=a.id, match_score=0.5))
    await async_db.commit()
    assert (await _call(async_db, a, "GET", f"/api/job/{job.id}/gaps")).status_code == 404
    await ensure_application_link(async_db, job, a.id)
    await async_db.commit()
    assert (await _call(async_db, a, "GET", f"/api/job/{job.id}/gaps")).status_code == 200
