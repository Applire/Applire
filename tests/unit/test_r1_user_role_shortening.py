# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R-1 (review-cards UAT-3): when the page ladder cannot reach the target, the person
picks non-current roles that keep only title, company and dates (the U-6 strip, with no
15-year limit, never automatic), previews the page count, and applies. The render is
faked here (pages counted from the data); no Chromium. All data synthetic."""
from __future__ import annotations

import asyncio
import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import FactPin
from applire.services.cv_page_ladder import (
    OLD_ROLE_BULLETS,
    shortenable_roles,
    shorten_roles,
)


def _cv() -> dict:
    return {
        "contact": {"name": "T", "email": "t@example.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
        "summary": "Lead.", "education": [], "skills": [], "languages": [], "certifications": [],
        "show_photo": False, "projects": [],
        "work_history": [
            {"id": "w0", "company": "Now AG", "role": "Lead", "start_date": "2019-01", "end_date": None,
             "is_current": True, "bullets": ["Leads 12 people.", "Owns the roadmap."]},
            {"id": "w1", "company": "Mid GmbH", "role": "Architect", "start_date": "2014-03", "end_date": "2018-12",
             "is_current": False, "bullets": ["Designed the LIMS.", "Cut cost by a third.", "Mentored 4."]},
            {"id": "w2", "company": "Lab Institute", "role": "Research Assistant", "start_date": "2010-01",
             "end_date": "2013-06", "is_current": False, "bullets": ["Ran assays.", "Wrote SOPs."],
             "projects": [{"name": "Assay study", "bullets": ["Ran 40 assays.", "Published."]}]},
            {"id": "w3", "company": "Diagnostics Inc", "role": "Technician", "start_date": "2007-01",
             "end_date": "2009-12", "is_current": False, "bullets": ["Calibrated analysers."]},
            {"id": "w4", "company": "Empty Co", "role": "Intern", "start_date": "2006-01",
             "end_date": "2006-06", "is_current": False, "bullets": []},
        ],
    }


def _pin(entry_id: str, quote: str) -> FactPin:
    return FactPin(entry_type="work", entry_id=entry_id, quote=quote, targets=["cv"])


def _carriers(pins):
    from applire.services.pin_reach import bullet_pin_carrier_indices

    return lambda entry, texts: bullet_pin_carrier_indices(
        texts, entry_id=str(entry.get("id") or ""), pins=pins
    )


# --------------------------------------------------------------------------- ladder


def test_shorten_roles_keeps_title_company_dates_and_pins():
    data = _cv()
    before = copy.deepcopy(data)
    pins = [_pin("w1", "Cut cost by a third.")]
    out, cut = shorten_roles(data, [1, 2], pinned_bullets=_carriers(pins))

    assert data == before, "the input must not be mutated"
    assert cut.kind == OLD_ROLE_BULLETS == "old_role_bullets"
    assert cut.items == ("Architect — Mid GmbH — 2018-12", "Research Assistant — Lab Institute — 2013-06")
    w1, w2 = out["work_history"][1], out["work_history"][2]
    assert w1["bullets"] == ["Cut cost by a third."], "a pinned bullet stays"
    assert w2["bullets"] == [] and w2["projects"] == []
    assert {k: w2[k] for k in ("role", "company", "start_date", "end_date")} == {
        "role": "Research Assistant", "company": "Lab Institute",
        "start_date": "2010-01", "end_date": "2013-06",
    }
    assert out["work_history"][0] == before["work_history"][0]
    assert out["work_history"][3] == before["work_history"][3]


def test_shorten_roles_skips_a_fully_pinned_role_without_naming_it():
    data = _cv()
    pins = [_pin("w3", "Calibrated analysers.")]
    out, cut = shorten_roles(data, [3], pinned_bullets=_carriers(pins))
    assert cut.items == () and out == data


def test_shortenable_roles_lists_dates_and_removable_bullet_counts():
    pins = [_pin("w1", "Cut cost by a third.")]
    got = shortenable_roles(_cv(), pinned_bullets=_carriers(pins))
    assert got == [
        {"index": 1, "title": "Architect", "company": "Mid GmbH", "dates": "03/2014 – 12/2018", "bullets": 2},
        {"index": 2, "title": "Research Assistant", "company": "Lab Institute", "dates": "01/2010 – 06/2013", "bullets": 4},
        {"index": 3, "title": "Technician", "company": "Diagnostics Inc", "dates": "01/2007 – 12/2009", "bullets": 1},
    ]
    # the current role and the role with nothing on it are absent; a fully pinned one too
    full = shortenable_roles(_cv(), pinned_bullets=_carriers([_pin("w3", "Calibrated analysers.")]))
    assert [r["index"] for r in full] == [1, 2]


# --------------------------------------------------------------------------- the API


def _pages(data: dict) -> int:
    """Fake render: one page plus one per three bullets (nested project bullets count)."""
    n = 0
    for w in data.get("work_history") or []:
        n += len(w.get("bullets") or [])
        n += sum(len(p.get("bullets") or []) for p in w.get("projects") or [])
    return 1 + n // 3


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
    renders: list = []

    async def _audit(record, session, **_kw):
        audits.append(copy.deepcopy(record.tailored_data))
        await session.commit()

    async def _measure(record, session):
        return cv.MeasuredRender("", _pages(record.tailored_data), False, 1, "DACH")

    async def _render_pages(record, session):
        renders.append(copy.deepcopy(record.tailored_data))
        return _pages(record.tailored_data)

    monkeypatch.setattr(cv, "_update_ats_report", _audit)
    monkeypatch.setattr(cv, "_measure_current", _measure)
    monkeypatch.setattr(cv, "_render_pages", _render_pages)
    app = FastAPI()
    app.include_router(cv_router)
    app.dependency_overrides[get_db] = lambda: db
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.audits, ac.renders = audits, renders
        yield ac


def _record(**over):
    """A CV 2 pages over a 1-page target whose ladder found nothing (cut_pages None)."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _subject_hash

    full = _cv()
    po = {
        "target": 1, "actual": _pages(full), "full_pages": _pages(full), "proposed": [],
        "choice": None, "basis_hash": _subject_hash(full), "cut_data": None, "full_data": None,
        "reaches_target": False, "cut_pages": None, "auto_cuts": [],
    }
    fields = dict(id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
                  tailored_data=full, template="classic_german", status="ready", page_overflow=po)
    fields.update(over)
    return GeneratedCV(**fields), full


def test_shortenable_roles_only_when_target_unreachable():
    from applire.services.cv import page_overflow_public

    rec, _ = _record()
    got = page_overflow_public(rec)["shortenable_roles"]
    assert [r["index"] for r in got] == [1, 2, 3]
    assert set(got[0]) == {"index", "title", "company", "dates", "bullets"}

    # a reachable proposal -> nothing to pick
    reach, _ = _record()
    reach.page_overflow = {**reach.page_overflow, "reaches_target": True, "cut_pages": 1, "cut_data": _cv()}
    assert page_overflow_public(reach)["shortenable_roles"] == []

    # the ladder reaches only a closer length (reaches_target False, cut_pages set) -> list
    closer, _ = _record()
    closer.page_overflow = {**closer.page_overflow, "cut_pages": 2, "cut_data": _cv()}
    assert [r["index"] for r in page_overflow_public(closer)["shortenable_roles"]] == [1, 2, 3]

    # section edits -> the cut path is closed -> empty
    edited, _ = _record(section_overrides={"summary": "x"})
    assert page_overflow_public(edited)["shortenable_roles"] == []

    # a pin that covers a whole role takes it off the list
    pinned = page_overflow_public(rec, pins=[_pin("w3", "Calibrated analysers.")])
    assert [r["index"] for r in pinned["shortenable_roles"]] == [1, 2]


def test_a_row_stored_before_b2_offers_no_roles_when_the_public_payload_says_it_reaches():
    """m-2: `cut_data` present, no `cut_pages` / `reaches_target` keys. The public
    payload defaults those to "reaches the target"; the role menu must read the
    same defaults (it once read cut_pages as None and offered the menu)."""
    from applire.services.cv import page_overflow_public

    rec, _ = _record()
    po = {k: v for k, v in rec.page_overflow.items() if k not in ("cut_pages", "reaches_target")}
    po["cut_data"] = _cv()
    rec.page_overflow = po
    out = page_overflow_public(rec)
    assert out["reaches_target"] is True and out["cut_pages"] == 1
    assert out["shortenable_roles"] == []


@pytest.mark.asyncio
async def test_preview_renders_candidate_without_writing(client, db):
    rec, full = _record()
    db.add(rec)
    await db.commit()
    before = copy.deepcopy(rec.page_overflow)

    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1, 2]})
    assert r.status_code == 200, r.text
    assert r.json() == {"pages": _pages(_without(full, 1, 2))}
    # the candidate (not the stored CV) was rendered
    assert client.renders and client.renders[-1]["work_history"][1]["bullets"] == []
    await db.refresh(rec)
    assert rec.tailored_data == full and rec.page_overflow == before
    assert client.audits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [[], list(range(31)), [1, 1], [0], [4], [99]])
async def test_preview_validates_the_role_list(client, db, roles):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": roles})
    assert r.status_code == 422, r.text
    assert client.renders == []


@pytest.mark.asyncio
async def test_preview_cross_user_404(client, db):
    rec, _ = _record(user_id=uuid.uuid4())
    db.add(rec)
    await db.commit()
    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1]})
    assert r.status_code == 404
    assert client.renders == []


@pytest.mark.asyncio
async def test_preview_renders_one_at_a_time(db, monkeypatch):
    """Each render launches a fresh Chromium: two previews must never overlap."""
    import applire.services.cv as cv

    from tests.support.owners import HARNESS_USER_ID

    rec, _ = _record(user_id=HARNESS_USER_ID)
    db.add(rec)
    await db.commit()
    live = {"now": 0, "max": 0, "calls": 0}

    async def _render(record, session):
        live["now"] += 1
        live["calls"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.05)
        live["now"] -= 1
        return 2

    monkeypatch.setattr(cv, "_render_pages", _render)
    got = await asyncio.gather(*[
        cv.preview_page_overflow_roles(rec.id, [1], db, user_id=rec.user_id) for _ in range(2)
    ])
    assert got == [2, 2] and live["calls"] == 2
    assert live["max"] == 1, "two previews rendered at the same time"


@pytest.mark.asyncio
async def test_apply_user_roles_measures_and_reports_measured_pages(client, db):
    rec, full = _record()
    db.add(rec)
    await db.commit()

    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1, 2]})
    previewed = r.json()["pages"]

    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1, 2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choice"] == "cut" and body["uncut_available"] is True and body["cut_available"] is False
    assert body["actual"] == _pages(body_cut := (await _reload(db, rec)).tailored_data) == previewed
    assert body["proposed"] == [{"kind": "old_role_bullets", "items": [
        "Architect — Mid GmbH — 2018-12", "Research Assistant — Lab Institute — 2013-06"]}]
    assert body["cut_pages"] == body["actual"] and body["reaches_target"] is (body["actual"] <= 1)
    assert body["shortenable_roles"] == [], "a cut already applied: undo first"
    assert body_cut["work_history"][1]["bullets"] == []
    assert client.audits[-1] == body_cut, "the page-length finding is re-audited on the cut content"
    rec = await _reload(db, rec)
    assert rec.content_snapshot
    from applire.services.cv import _pages_kept_at

    # the page-length row reads the pick as the decision, at the MEASURED length
    expected = None if body["reaches_target"] else body["actual"]
    assert _pages_kept_at(rec.page_overflow) == expected


async def _reload(db, rec):
    await db.refresh(rec)
    return rec


@pytest.mark.asyncio
async def test_apply_reports_the_apply_time_measure_not_the_preview(client, db, monkeypatch):
    """Review Focus 3: the preview said 2, the apply render says 3 -> the response says 3."""
    import applire.services.cv as cv

    rec, _ = _record()
    db.add(rec)
    await db.commit()

    async def _render(record, session):
        return 2

    async def _measure(record, session):
        return cv.MeasuredRender("", 3, False, 1, "DACH")

    monkeypatch.setattr(cv, "_render_pages", _render)
    monkeypatch.setattr(cv, "_measure_current", _measure)
    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1]})
    assert r.json() == {"pages": 2}
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})
    assert r.status_code == 200, r.text
    assert r.json()["actual"] == 3
    assert r.json()["cut_pages"] == 3 and r.json()["reaches_target"] is False


@pytest.mark.asyncio
async def test_apply_user_roles_409_on_section_overrides(client, db):
    rec, _ = _record(section_overrides={"summary": "edited"})
    db.add(rec)
    await db.commit()
    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1]})
    assert r.status_code == 422  # nothing is shortenable on an edited CV
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})
    assert r.status_code == 409, r.text
    await db.refresh(rec)
    assert rec.page_overflow["choice"] is None and rec.tailored_data == _cv()


@pytest.mark.asyncio
async def test_apply_user_roles_409_when_a_cut_is_already_applied(client, db):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    assert (await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})).status_code == 200
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [2]})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_apply_roles_validation(client, db):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    url = f"/api/cv/{rec.id}/page-overflow"
    assert (await client.post(url, json={"choice": "keep", "roles": [1]})).status_code == 422
    assert (await client.post(url, json={"choice": "cut", "roles": []})).status_code == 422
    assert (await client.post(url, json={"choice": "cut", "roles": [1, 1]})).status_code == 422
    assert (await client.post(url, json={"choice": "cut", "roles": [0]})).status_code == 422


@pytest.mark.asyncio
async def test_apply_user_roles_reaudits_keywords(db, monkeypatch):
    """Review Focus 5: a posting keyword only in the shortened role's bullets is missing
    from the ATS report after the cut — the REAL re-audit runs over the measured text."""
    from datetime import datetime, timezone

    import applire.services.cv as cv
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.routers.cv import router as cv_router
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    rec, _ = _record(user_id=HARNESS_USER_ID)
    rec.tailored_data["work_history"][2]["bullets"] = ["Ran assays with Zymurgy."]
    rec.page_overflow["basis_hash"] = cv._subject_hash(rec.tailored_data)
    job = JobAnalysis(
        id=rec.job_analysis_id, raw_text_hash="h", raw_text="Brewer wanted", role_title="Brewer",
        required_skills=["Zymurgy"], nice_to_have_skills=[], keywords=["Zymurgy"],
        seniority_level="mid", company_culture_signals=[], language_requirement="en",
    )
    profile = make_master_profile(
        id=rec.profile_id, profile_json={"work_experience": [], "education": [], "skills": []},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([job, profile, rec])
    await db.commit()

    def _text(data):
        return " ".join(b for w in data["work_history"] for b in w.get("bullets") or [])

    async def _measure(record, session):
        return cv.MeasuredRender(_text(record.tailored_data), 1, False, 1, "DACH")

    monkeypatch.setattr(cv, "_measure_current", _measure)
    app = FastAPI()
    app.include_router(cv_router)
    app.dependency_overrides[get_db] = lambda: db
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [2]})
    assert r.status_code == 200, r.text
    await db.refresh(rec)
    kw = rec.ats_report["keywords"]
    assert "Zymurgy" in [m if isinstance(m, str) else m.get("keyword") or m.get("term") for m in kw["missing"]], kw
    assert "Zymurgy" not in kw["present"]


@pytest.mark.asyncio
async def test_keep_after_user_cut_restores_full_data(client, db):
    rec, full = _record()
    db.add(rec)
    await db.commit()
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1, 3]})
    assert r.status_code == 200, r.text
    assert r.json()["uncut_available"] is True
    await db.refresh(rec)
    assert rec.tailored_data != full

    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    assert r.json()["choice"] is None, "an undo of the person's cut returns to the undecided card"
    await db.refresh(rec)
    assert rec.tailored_data == full
    assert r.json()["actual"] == _pages(full)
    # the card can offer the picks again after the undo
    assert [x["index"] for x in r.json()["shortenable_roles"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_keep_after_a_later_edit_reinserts_the_shortened_bullets(client, db):
    """_page_undo_plan reads cut_data/full_data like any ladder cut: a later edit stays."""
    rec, full = _record()
    db.add(rec)
    await db.commit()
    assert (await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})).status_code == 200
    await db.refresh(rec)
    edited = copy.deepcopy(rec.tailored_data)
    edited["summary"] = "Edited summary."
    rec.tailored_data = edited
    await db.commit()
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    await db.refresh(rec)
    assert rec.tailored_data["summary"] == "Edited summary."
    assert rec.tailored_data["work_history"][1]["bullets"] == full["work_history"][1]["bullets"]


_LADDER_FIELDS = ("cut_data", "basis_hash", "proposed", "cut_pages", "reaches_target")


def _ladder(rec) -> dict:
    return {k: rec.page_overflow.get(k) for k in _LADDER_FIELDS}


@pytest.mark.asyncio
async def test_undo_of_a_person_cut_returns_to_the_open_card(client, db):
    """D2: the person never chose "keep" — the undo restores the ladder's pre-pick state."""
    rec, full = _record()
    db.add(rec)
    await db.commit()
    before = copy.deepcopy(_ladder(rec))
    assert (await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1, 3]})).status_code == 200
    await db.refresh(rec)
    assert rec.page_overflow["ladder_before_pick"], "the pre-pick ladder state is stored for the undo"

    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    body = r.json()
    await db.refresh(rec)
    assert body["choice"] is None and rec.page_overflow["choice"] is None
    assert _ladder(rec) == before
    assert "ladder_before_pick" not in rec.page_overflow
    assert rec.tailored_data == full
    assert body["cut_available"] is False and body["uncut_available"] is False
    assert [x["index"] for x in body["shortenable_roles"]] == [1, 2, 3]
    assert "ladder_before_pick" not in body
    assert body["actual"] == _pages(full) and body["proposed"] == []
    assert client.audits[-1] == full, "re-audited on the restored content"


@pytest.mark.asyncio
async def test_undo_of_a_person_cut_after_a_later_edit_returns_to_the_open_card(client, db):
    """D2, the re-insert path: a later edit stays, the card still re-opens undecided."""
    rec, full = _record()
    db.add(rec)
    await db.commit()
    before = copy.deepcopy(_ladder(rec))
    assert (await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})).status_code == 200
    await db.refresh(rec)
    edited = copy.deepcopy(rec.tailored_data)
    edited["summary"] = "Edited summary."
    rec.tailored_data = edited
    await db.commit()

    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    body = r.json()
    await db.refresh(rec)
    assert rec.tailored_data["summary"] == "Edited summary."
    assert body["choice"] is None and rec.page_overflow["choice"] is None
    assert _ladder(rec) == before
    assert "ladder_before_pick" not in rec.page_overflow and "ladder_before_pick" not in body
    assert body["proposed"] == [] and body["cut_available"] is False


@pytest.mark.asyncio
async def test_a_person_cut_can_be_picked_again_after_its_undo(client, db):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    for roles in ([1, 3], [2]):
        assert (await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": roles})).status_code == 200
        r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "keep"})
        assert r.json()["choice"] is None


# A ladder cut's undo still stores "keep" (the person did choose the length there):
# tests/unit/test_cv_page_overflow.py::test_cut_swaps_in_the_proposal_and_keep_restores_it


def _without(data: dict, *indices: int) -> dict:
    out = copy.deepcopy(data)
    for i in indices:
        out["work_history"][i]["bullets"] = []
        out["work_history"][i]["projects"] = []
    return out


def _break_pins(monkeypatch):
    import applire.services.application as application

    async def _boom(*_a, **_k):
        raise RuntimeError("pin store down")

    monkeypatch.setattr(application, "get_application_for_job", _boom)


def _with_pins(monkeypatch, pins):
    import applire.services.application as application

    app = SimpleNamespace(pinned_facts=[p.model_dump(mode="json") for p in pins])

    async def _get(*_a, **_k):
        return app

    monkeypatch.setattr(application, "get_application_for_job", _get)


@pytest.mark.asyncio
async def test_apply_refuses_when_pins_cannot_load(client, db, monkeypatch):
    rec, full = _record()
    db.add(rec)
    await db.commit()
    before = copy.deepcopy(rec.page_overflow)
    _break_pins(monkeypatch)
    r = await client.post(f"/api/cv/{rec.id}/page-overflow", json={"choice": "cut", "roles": [1]})
    assert r.status_code == 409, r.text
    await db.refresh(rec)
    assert rec.tailored_data == full and rec.page_overflow == before
    assert client.renders == [] and client.audits == []


@pytest.mark.asyncio
async def test_preview_refuses_when_pins_cannot_load(client, db, monkeypatch):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    _break_pins(monkeypatch)
    r = await client.post(f"/api/cv/{rec.id}/page-overflow/preview", json={"roles": [1]})
    assert r.status_code == 409, r.text
    assert client.renders == []


@pytest.mark.asyncio
async def test_status_lists_no_roles_when_pins_cannot_load(client, db, monkeypatch):
    import applire.services.signature as signature

    monkeypatch.setattr(signature, "resolve_signature_effective", AsyncMock(return_value=False))
    monkeypatch.setattr(signature, "resolve_signature_available", AsyncMock(return_value=False))
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    _break_pins(monkeypatch)
    r = await client.get(f"/api/cv/{rec.id}/status")
    assert r.status_code == 200, r.text
    assert r.json()["page_overflow"]["shortenable_roles"] == []


@pytest.mark.asyncio
async def test_choice_response_lists_roles_with_pins(client, db, monkeypatch):
    rec, _ = _record()
    db.add(rec)
    await db.commit()
    _with_pins(monkeypatch, [
        _pin("w3", "Calibrated analysers."),       # w3 fully pinned
        _pin("w1", "Cut cost by a third."),        # w1 partly pinned
    ])
    url = f"/api/cv/{rec.id}/page-overflow"
    r = await client.post(url, json={"choice": "cut", "roles": [1, 2]})
    assert r.status_code == 200, r.text
    r = await client.post(url, json={"choice": "keep"})
    assert r.status_code == 200, r.text
    roles = {x["index"]: x for x in r.json()["shortenable_roles"]}
    assert set(roles) == {1, 2}, "the fully pinned role must not be offered"
    assert roles[1]["bullets"] == 2, "the pinned bullet is not counted"
    # the early-return door (same choice again) carries the same list
    r = await client.post(url, json={"choice": "keep"})
    assert {x["index"] for x in r.json()["shortenable_roles"]} == {1, 2}
