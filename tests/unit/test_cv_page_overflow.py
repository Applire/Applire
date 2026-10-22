# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#786 L-3 (founder ruling 2026-10-22) — the x+1 decision.

A CV longer than its page target gets a proposal at generation (the page ladder,
walked further as a dry run until the render reaches the target); the person keeps
the extra page or cuts. "Kürzen" is deterministic — it swaps in exactly the data the
proposal measured, so it removes exactly what the card named — and can be undone.
The render is faked here (pages counted from the data); the ladder itself is covered
by ``test_cv_page_ladder.py``.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _data(n_certs: int) -> dict:
    return {
        "contact": {"name": "T", "email": "t@t.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
        "summary": "", "work_history": [], "education": [], "skills": [], "languages": [],
        "show_photo": False,
        "certifications": [
            {"name": f"Cert {2010 + i}", "date_obtained": f"{2010 + i}"} for i in range(n_certs)
        ],
    }


def _pages(data: dict) -> int:
    """Fake render: two pages plus one per two certifications beyond the first."""
    return 2 + max(0, len(data.get("certifications") or []) - 1) // 2


@pytest.fixture
def fake_render(monkeypatch):
    import applire.services.ats_audit as ats_audit
    import applire.services.cv as cv

    renders: list[dict] = []

    async def _html(cv_id, db, *, user_id=None):
        return cv._fake_record_data()  # set per test

    async def _pdf(html, **_kw):
        renders.append(html)
        return html

    monkeypatch.setattr(cv, "get_cv_html", _html)
    monkeypatch.setattr(cv, "_html_to_pdf", _pdf)
    monkeypatch.setattr(ats_audit, "extract_text_and_pages", lambda pdf: ("", _pages(pdf)))
    return renders


def _ctx(target: int):
    from applire.services.cv import CondenseContext

    return CondenseContext(budgets=MagicMock(region="DACH"), target=target)


def _measured(count: int, target: int):
    from applire.services.cv import MeasuredRender

    return MeasuredRender("", count, True, target, "DACH")


async def _propose(record, target, count, monkeypatch, fake_render):
    import applire.services.cv as cv

    monkeypatch.setattr(cv, "_fake_record_data", lambda: copy.deepcopy(record.tailored_data), raising=False)
    await cv._propose_page_cut(record, MagicMock(), _ctx(target), _measured(count, target))


@pytest.mark.asyncio
async def test_proposal_names_the_cuts_that_reach_the_target_and_restores_the_data(monkeypatch, fake_render):
    full = _data(5)  # 4 pages on the fake render
    record = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=copy.deepcopy(full),
                             section_overrides=None, page_overflow="stale")
    await _propose(record, 2, _pages(full), monkeypatch, fake_render)

    po = record.page_overflow
    assert record.tailored_data == full, "the dry run must leave the delivered data untouched"
    assert po["target"] == 2 and po["actual"] == 4 and po["choice"] is None
    assert po["proposed"] == [{"kind": "oldest_certification", "items": ["Cert 2010", "Cert 2011", "Cert 2012"]}]
    assert [c["name"] for c in po["cut_data"]["certifications"]] == ["Cert 2013", "Cert 2014"]
    assert _pages(po["cut_data"]) == 2


@pytest.mark.asyncio
async def test_no_proposal_when_the_cv_fits_or_carries_a_section_edit(monkeypatch, fake_render):
    fits = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=_data(1),
                           section_overrides=None, page_overflow={"old": 1})
    await _propose(fits, 2, 2, monkeypatch, fake_render)
    assert fits.page_overflow is None

    edited = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=_data(5),
                             section_overrides={"skills": {}}, page_overflow=None)
    await _propose(edited, 2, 4, monkeypatch, fake_render)
    assert edited.page_overflow is None
    assert fake_render == [], "neither case may render"


@pytest.mark.asyncio
async def test_unreachable_target_offers_nothing(monkeypatch, fake_render):
    record = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=_data(5),
                             section_overrides=None, page_overflow=None)
    await _propose(record, 1, 4, monkeypatch, fake_render)  # the fake never goes below 2
    assert record.page_overflow["proposed"] == []
    assert record.page_overflow["cut_data"] is None


# ---------------------------------------------------------------------------
# POST /api/cv/{cv_id}/page-overflow + the status field, over the real ASGI stack
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db, monkeypatch):
    import applire.services.cv as cv
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.cv import router as cv_router
    from tests.support.owners import HARNESS_USER_ID

    audits: list = []

    async def _audit(record, session, **_kw):
        audits.append(copy.deepcopy(record.tailored_data))
        await session.commit()

    monkeypatch.setattr(cv, "_update_ats_report", _audit)
    app = FastAPI()
    app.include_router(cv_router)
    app.dependency_overrides[get_db] = lambda: db
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.audits = audits
        yield ac


def _cv_with_proposal(**over):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _subject_hash

    full, cut = _data(5), _data(1)
    cut["certifications"] = [{"name": "Cert 2014", "date_obtained": "2014"}]
    po = {
        "target": 2, "actual": 4,
        "proposed": [{"kind": "oldest_certification", "items": ["Cert 2010", "Cert 2011", "Cert 2012", "Cert 2013"]}],
        "choice": None, "basis_hash": _subject_hash(full), "cut_data": cut, "full_data": None,
    }
    fields = dict(id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
                  tailored_data=full, template="classic_german", status="ready", page_overflow=po)
    fields.update(over)
    return GeneratedCV(**fields), full, cut


@pytest.mark.asyncio
async def test_cut_swaps_in_the_proposal_and_keep_restores_it(client, db):
    record, full, cut = _cv_with_proposal()
    db.add(record)
    await db.commit()

    r = await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "cut"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choice"] == "cut" and body["uncut_available"] is True and body["cut_available"] is False
    await db.refresh(record)
    assert record.tailored_data == cut
    assert record.content_snapshot, "the section editor must see the cut content"
    assert client.audits[-1] == cut, "the page-length finding is re-audited on the cut content"

    r = await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    assert r.json()["choice"] == "keep"
    await db.refresh(record)
    assert record.tailored_data == full
    assert r.json()["cut_available"] is True, "the choice can be changed again"


@pytest.mark.asyncio
async def test_keep_first_only_remembers_the_choice(client, db):
    record, full, _ = _cv_with_proposal()
    db.add(record)
    await db.commit()
    r = await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200 and r.json()["choice"] == "keep"
    await db.refresh(record)
    assert record.tailored_data == full and record.page_overflow["choice"] == "keep"
    assert client.audits == []


@pytest.mark.asyncio
async def test_cut_refuses_a_cv_that_changed_since_the_proposal(client, db):
    changed = _data(5)
    changed["summary"] = "edited after generation"
    record, _, _ = _cv_with_proposal()
    record.tailored_data = changed
    db.add(record)
    await db.commit()
    r = await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "cut"})
    assert r.status_code == 409
    await db.refresh(record)
    assert record.tailored_data == changed and record.page_overflow["choice"] is None


@pytest.mark.asyncio
async def test_cut_refuses_a_cv_with_a_section_edit(client, db):
    record, full, _ = _cv_with_proposal(section_overrides={"summary": {"text": "x"}})
    db.add(record)
    await db.commit()
    r = await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "cut"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_no_decision_is_409_and_bad_choice_is_422(client, db):
    record, _, _ = _cv_with_proposal(page_overflow=None)
    db.add(record)
    await db.commit()
    assert (await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "cut"})).status_code == 409
    assert (await client.post(f"/api/cv/{record.id}/page-overflow", json={"choice": "maybe"})).status_code == 422
    assert (await client.post(f"/api/cv/{uuid.uuid4()}/page-overflow", json={"choice": "cut"})).status_code == 404


@pytest.mark.asyncio
async def test_status_carries_the_decision_without_the_data_copies(client, db, monkeypatch):
    import applire.services.signature as signature

    monkeypatch.setattr(signature, "resolve_signature_effective", AsyncMock(return_value=False))
    monkeypatch.setattr(signature, "resolve_signature_available", AsyncMock(return_value=False))
    record, _, _ = _cv_with_proposal()
    db.add(record)
    await db.commit()
    r = await client.get(f"/api/cv/{record.id}/status")
    assert r.status_code == 200, r.text
    po = r.json()["page_overflow"]
    assert po == {
        "target": 2, "actual": 4,
        "proposed": [{"kind": "oldest_certification", "items": ["Cert 2010", "Cert 2011", "Cert 2012", "Cert 2013"]}],
        "choice": None, "cut_available": True, "uncut_available": False,
    }
