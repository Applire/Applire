# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UAT-3d (Hazelnut build 5, WP-A1) — the profile-health hub's delivery classes,
the unit input, "Übernehmen" for a not-applied item, and the duplicate_entry
thread."""
from __future__ import annotations

import typing
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import (
    EnrichmentRecord,
    HealthIssue,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.finding_registry import classify
from applire.services.profile.health import assess_health
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.utils.budget_unit import budget_needs_unit
from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

THREADS = typing.get_args(HealthIssue.model_fields["thread"].annotation)


# ── builders: one profile per thread ─────────────────────────────────────────


def _work(i="w1", **kw):
    return {
        "id": i, "company": "Talwerk GmbH", "role": "Controllerin",
        "start_date": "2019-02", "end_date": "2021-03", **kw,
    }


def _blob(**kw):
    base = {"personal_info": {"name": "Lena Brandt"}, "work_experience": [_work()]}
    meta = kw.pop("metadata", None)
    base.update(kw)
    if meta:
        base["metadata"] = meta
    return base


def _not_applied_item(**kw):
    kw.setdefault("section", "education")
    kw.setdefault("label", "Hochschule Nord / M.Sc.")
    kw.setdefault("reason", "no_op_carried_entry")
    return kw


def _build(thread: str) -> MasterProfileData:
    if thread == "conflict":
        return MasterProfileData.model_validate(_blob(metadata={"pending_conflicts": [{
            "section": "work_experience", "field": "end_date", "entity_id": "w1",
            "existing_value": "2021-03", "incoming_value": "2021-06", "source": "cv"}]}))
    if thread == "confirmation":
        return MasterProfileData.model_validate(_blob(metadata={"pending_confirmations": [
            {"question": "Which role?", "options": ["a", "b"]}]}))
    if thread == "unit":
        return MasterProfileData.model_validate(_blob(work_experience=[_work(budget_managed="6000000")]))
    if thread == "span_provenance":
        return MasterProfileData.model_validate(_blob(
            work_experience=[_work(responsibilities=["Monatsabschluss mit SAP CO verantwortet."], end_date=None)],
            skills=[{"id": "s1", "name": "SAP CO", "years_experience": 8, "source": "transcribed"}],
        ))
    if thread == "suspect_binding":
        rec = EnrichmentRecord(
            timestamp=datetime.now(timezone.utc), source="cv_upload",
            matched=[{
                "section": "skills", "entity_id": "s-soft", "incoming": "REST APIs",
                "existing": "Mentoring", "incoming_entry": {"name": "REST APIs", "category": "technical"},
            }],
        )
        return MasterProfileData.model_validate(_blob(
            skills=[{"id": "s-soft", "name": "Mentoring", "category": "soft"}],
            metadata=ProfileMetadata(enrichment_history=[rec]).model_dump(mode="json"),
        ))
    if thread == "duplicate_entry":
        return MasterProfileData.model_validate(_blob(
            work_experience=[_work("w1"), _work("w2", company="Talwerk Feinmechanik GmbH")]))
    if thread == "not_applied":
        return _with_not_applied([ImportNotApplied(**_not_applied_item(item_key="education:x", entry={"institution": "Hochschule Nord", "degree": "M.Sc."}))])
    if thread == "aspiration":  # RULING B-1
        from applire.services.profile.aspiration import aspiration_key

        text = "Aufbau eines Risiko-Frameworks"
        return MasterProfileData.model_validate(_blob(
            work_experience=[_work(end_date=None, responsibilities=[text])],
            metadata={"aspiration_verdicts": [{
                "entry_id": "w1", "key": aspiration_key(text), "text": text, "aspiration": True}]},
        ))
    if thread == "org_figure":  # FIG-2
        from applire.services.profile.aspiration import aspiration_key

        text = "Built the order platform for a €2.1 billion retailer."
        return MasterProfileData.model_validate(_blob(
            work_experience=[_work(responsibilities=[text])],
            metadata={"org_figure_verdicts": [{
                "entry_id": "w1", "key": aspiration_key(text), "text": text,
                "figure": "€2.1 billion retailer", "figure_key": "currency-2100000000"}]},
        ))
    if thread == "accuracy":
        rec = EnrichmentRecord(
            timestamp=datetime.now(timezone.utc), source="cv_upload", confidence=0.2)
        return MasterProfileData.model_validate(_blob(
            metadata=ProfileMetadata(enrichment_history=[rec]).model_dump(mode="json")))
    raise AssertionError(f"no builder for thread {thread!r} — add one with its class")


def _with_not_applied(items) -> MasterProfileData:
    rec = EnrichmentRecord(timestamp=datetime.now(timezone.utc), source="cv_upload", not_applied=items)
    return MasterProfileData.model_validate(_blob(
        metadata=ProfileMetadata(enrichment_history=[rec]).model_dump(mode="json")))


_EXPECTED = {
    "conflict": ("A", {"conflict_scalar"}),
    "confirmation": ("A", {"confirmation"}),
    "unit": ("A", {"unit_input"}),
    "span_provenance": ("A", {"recompute_span"}),
    "suspect_binding": ("A", {"confirm_match"}),
    "duplicate_entry": ("A", {"confirm_match"}),
    "not_applied": ("A", {"adopt_not_applied"}),
    "aspiration": ("A", {"ask_achieved"}),
    "org_figure": ("A", {"ask_org_figure"}),
    "accuracy": ("S", {None}),
}


@pytest.mark.parametrize("thread", THREADS)
def test_every_thread_has_a_class_and_resolver(thread):
    assert thread in _EXPECTED, f"thread {thread!r} has no expected class in this test"
    issues = [i for i in assess_health(_build(thread)).issues if i.thread == thread]
    assert issues, f"builder for {thread!r} produced no issue"
    cls, resolvers = _EXPECTED[thread]
    for issue in issues:
        assert issue.delivery_class is not None, thread
        assert (issue.delivery_class, issue.resolver) in {(cls, r) for r in resolvers}, thread
        if issue.resolver is not None:
            from applire.services.finding_registry import RESOLVERS

            assert issue.resolver in RESOLVERS


def test_no_issue_comes_out_unclassified():
    for thread in THREADS:
        for issue in assess_health(_build(thread)).issues:
            assert issue.delivery_class is not None, (thread, issue.id)


def test_registry_rows_are_the_contract():
    assert classify("health:not_applied_unadoptable").cls == "A"
    assert classify("health:completeness_no_field_gaps").cls == "S"
    assert classify("health:conflict_list").resolver == "conflict_list"


# ── conflicts: list vs scalar ────────────────────────────────────────────────


def _conflict_issue(field, entity_id="w1"):
    p = MasterProfileData.model_validate(_blob(metadata={"pending_conflicts": [{
        "section": "work_experience", "field": field, "entity_id": entity_id,
        "existing_value": ["a"] if field == "achievements" else "x",
        "incoming_value": ["b"] if field == "achievements" else "y", "source": "cv"}]}))
    (issue,) = [i for i in assess_health(p).issues if i.thread == "conflict"]
    return issue


def test_list_conflict_has_four_outcomes_and_the_list_resolver():
    issue = _conflict_issue("achievements")
    assert (issue.delivery_class, issue.resolver) == ("A", "conflict_list")
    assert issue.outcomes == ["keep_current", "take_new", "keep_both", "merge"]


def test_scalar_conflict_outcomes():
    plain = _conflict_issue("location")
    assert (plain.delivery_class, plain.resolver) == ("A", "conflict_scalar")
    assert plain.outcomes == ["keep_current", "take_new"]
    identity = _conflict_issue("end_date")
    assert identity.resolver == "conflict_scalar"
    assert identity.outcomes == ["keep_current", "take_new", "keep_both"]


# ── completeness ─────────────────────────────────────────────────────────────


def test_completeness_with_field_gaps_is_class_a():
    p = MasterProfileData.model_validate(_blob(work_experience=[_work(end_date=None)]))
    block = assess_health(p).completeness
    assert block.field_gaps
    assert (block.delivery_class, block.resolver) == ("A", "complete_section")


def test_completeness_without_field_gaps_is_silent():
    p = MasterProfileData.model_validate({"personal_info": {"name": "Lena Brandt"}})
    block = assess_health(p).completeness
    assert not block.field_gaps
    assert (block.delivery_class, block.resolver) == ("S", None)


# ── duplicate_entry ──────────────────────────────────────────────────────────


def _dupes(profile):
    return [i for i in assess_health(profile).issues if i.thread == "duplicate_entry"]


def test_duplicate_entry_issue_shape():
    (issue,) = _dupes(_build("duplicate_entry"))
    assert issue.id == "duplicate_entry:work_experience:w1:w2"
    assert issue.entry_ids == ["w1", "w2"]
    assert issue.section == "work_experience"
    assert issue.profile_mismatch_severity == "review"
    assert issue.entity_label and issue.other_entity_label
    assert issue.entity_label != issue.other_entity_label
    assert (issue.delivery_class, issue.resolver) == ("A", "confirm_match")


def _dupe_with_meta(meta):
    return MasterProfileData.model_validate(_blob(
        work_experience=[_work("w1"), _work("w2", company="Talwerk Feinmechanik GmbH")],
        metadata=meta))


def test_duplicate_entry_gone_when_kept_separate():
    p = _dupe_with_meta({"separate_entry_pairs": [{"section": "work_experience", "ids": ["w2", "w1"]}]})
    assert _dupes(p) == []


def test_duplicate_entry_gone_when_model_said_different_but_stays_for_unsure():
    ids = {"section": "work_experience", "ids": ["w1", "w2"]}
    assert _dupes(_dupe_with_meta({"entry_identity_verdicts": [{**ids, "verdict": "different"}]})) == []
    assert len(_dupes(_dupe_with_meta({"entry_identity_verdicts": [{**ids, "verdict": "unsure"}]}))) == 1


# ── not-applied capture and legacy ───────────────────────────────────────────


def _import_item():
    incoming = MasterProfileData.model_validate(_blob(education=[{
        "id": "inc-1", "institution": "Hochschule Nord", "degree": "M.Sc.", "end_date": "2012"}]))
    merged = MasterProfileData.model_validate(_blob())
    items = compute_import_not_applied(incoming, merged, [])
    (item,) = [i for i in items if i.section == "education"]
    return item


def test_capture_is_a_whole_record_for_list_sections_only():
    item = _import_item()
    assert item.item_key and item.item_key.startswith("education:")
    assert item.entry["institution"] == "Hochschule Nord" and "id" not in item.entry
    assert _import_item().item_key == item.item_key  # deterministic


def test_legacy_item_without_entry_is_not_adoptable_and_does_not_crash():
    p = _with_not_applied([ImportNotApplied(**_not_applied_item())])
    (issue,) = [i for i in assess_health(p).issues if i.thread == "not_applied"]
    assert [i.adoptable for i in issue.not_applied_items] == [False]
    assert (issue.delivery_class, issue.resolver) == ("A", "complete_section")  # legacy: go-to-section fallback stays visible


def test_summary_item_has_no_entry():
    p = _with_not_applied([ImportNotApplied(section="professional_summary", label="de", reason="summary_populated")])
    (issue,) = [i for i in assess_health(p).issues if i.thread == "not_applied"]
    assert issue.not_applied_items[0].entry is None and issue.not_applied_items[0].adoptable is False


# ── endpoints ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    import applire.models  # noqa: F401  (registers every table: the committer reads pinned facts)
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed(db_session, profile: MasterProfileData):
    db_session.add(make_master_profile(profile_json=profile.model_dump(mode="json")))
    await db_session.commit()


async def _health(client):
    return (await client.get("/api/profile/health")).json()


@pytest.mark.asyncio
async def test_unit_endpoint_round_trip(client, db_session):
    await _seed(db_session, _build("unit"))
    (issue,) = [i for i in (await _health(client))["issues"] if i["thread"] == "unit"]
    assert issue["budget_value"] == "6000000" and issue["entry_id"] == "w1"
    assert issue["delivery_class"] == "A" and issue["resolver"] == "unit_input"

    resp = await client.post("/api/profile/health/unit", json={"entry_id": "w1", "unit": "EUR"})
    assert resp.status_code == 200
    budget = resp.json()["profile"]["work_experience"][0]["budget_managed"]
    assert budget == "6000000 EUR" and not budget_needs_unit(budget)
    assert [i for i in (await _health(client))["issues"] if i["thread"] == "unit"] == []
    history = resp.json()["profile"]["metadata"]["enrichment_history"]
    assert [(c["section"], c["field"]) for c in history[-1]["changes"]] == [
        ("work_experience", "Controllerin @ Talwerk GmbH")  # one per-entry receipt
    ]
    assert history[-1]["source"] == "manual_edit"


@pytest.mark.asyncio
async def test_unit_endpoint_refusals(client, db_session):
    p = MasterProfileData.model_validate(_blob(work_experience=[
        _work("w1", budget_managed="2 Mio. CHF"), _work("w2", company="Other AG"), ]))
    await _seed(db_session, p)
    assert (await client.post("/api/profile/health/unit", json={"entry_id": "nope", "unit": "EUR"})).status_code == 404
    assert (await client.post("/api/profile/health/unit", json={"entry_id": "w1", "unit": "EUR"})).status_code == 422
    assert (await client.post("/api/profile/health/unit", json={"entry_id": "w2", "unit": "EUR"})).status_code == 422
    assert (await client.post("/api/profile/health/unit", json={"entry_id": "w2", "unit": "JPY"})).status_code == 422


@pytest.mark.asyncio
async def test_adopt_round_trip_409_422_404(client, db_session):
    item = _import_item()
    legacy = ImportNotApplied(**_not_applied_item(label="Alte Schule / Abitur"))
    profile = _with_not_applied([item, legacy])
    await _seed(db_session, profile)

    (issue,) = [i for i in (await _health(client))["issues"] if i["thread"] == "not_applied"]
    assert issue["delivery_class"] == "A" and issue["resolver"] == "adopt_not_applied"
    by_label = {i["label"]: i for i in issue["not_applied_items"]}
    assert by_label[item.label]["adoptable"] is True and by_label[item.label]["item_key"] == item.item_key
    assert by_label["Alte Schule / Abitur"]["adoptable"] is False

    url = "/api/profile/not-applied/adopt"
    assert (await client.post(url, json={"issue_id": issue["id"], "item_key": "education:nope"})).status_code == 404
    assert (await client.post(url, json={"issue_id": "not_applied:nope", "item_key": item.item_key})).status_code == 404
    assert (await client.post(url, json={"issue_id": "bogus", "item_key": item.item_key})).status_code == 404

    resp = await client.post(url, json={"issue_id": issue["id"], "item_key": item.item_key})
    assert resp.status_code == 200
    assert "Hochschule Nord" in resp.text

    after = await _health(client)
    (still,) = [i for i in after["issues"] if i["thread"] == "not_applied"]
    assert [i["label"] for i in still["not_applied_items"]] == ["Alte Schule / Abitur"]
    assert still["not_applied_count"] == 1

    assert (await client.post(url, json={"issue_id": issue["id"], "item_key": item.item_key})).status_code == 409


@pytest.mark.asyncio
async def test_adopt_not_adoptable_is_422_and_last_item_clears_the_issue(client, db_session):
    keyed_no_entry = ImportNotApplied(**_not_applied_item(item_key="education:abc"))
    await _seed(db_session, _with_not_applied([keyed_no_entry]))
    (issue,) = [i for i in (await _health(client))["issues"] if i["thread"] == "not_applied"]
    r = await client.post("/api/profile/not-applied/adopt", json={"issue_id": issue["id"], "item_key": "education:abc"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_adopting_every_item_removes_the_issue(client, db_session):
    item = _import_item()
    await _seed(db_session, _with_not_applied([item]))
    (issue,) = [i for i in (await _health(client))["issues"] if i["thread"] == "not_applied"]
    assert (await client.post("/api/profile/not-applied/adopt",
                              json={"issue_id": issue["id"], "item_key": item.item_key})).status_code == 200
    assert [i for i in (await _health(client))["issues"] if i["thread"] == "not_applied"] == []
