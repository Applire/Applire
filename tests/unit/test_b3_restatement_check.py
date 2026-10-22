# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut b3 WP-V, ruling V-3 = A (ADR-046 + ADR-063 amended 2026-10-22, D-B3V6).

A second source's bullets that restate a held bullet are checked by ONE extra
LLM call after the reconciler and are not written; they are receipted and can
be written on the candidate's word ("Trotzdem übernehmen"). The model judges;
code acts only on the ids it names, fails open, and never touches a held bullet.
"""
import pytest

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.ops import AddBullets, UpsertSkill
from applire.services.profile.reconcile.restatement import hold_back_restatements

HELD = ["Verantwortung für zwei Fertigungsbereiche (Spritzguss, Montage) mit 38 Mitarbeitenden.",
        "Einführung von Shopfloor-Management und KVP-Routinen."]


class _Provider:
    def __init__(self, *answers, fail=False):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.fail = fail

    async def aparse_json(self, prompt, *, system=None, **_kw):
        self.prompts.append(prompt)
        self.systems.append(system or "")
        if self.fail:
            raise RuntimeError("provider down")
        return self.answers.pop(0) if self.answers else {"ops": []}


def _profile():
    entry = WorkEntry(company="Weberit", role="Produktionsleiter", responsibilities=list(HELD))
    return MasterProfileData(work_experience=[entry]), entry


def _op(entry_id):
    return AddBullets(
        target=entry_id,
        responsibilities=["Shopfloor-Management und KVP eingeführt.",
                          "Budgetverantwortung von rund 6 Mio. € pro Jahr."],
        technologies=["SAP PP"],
    )


@pytest.mark.asyncio
async def test_named_restatement_is_held_back_and_receipted():
    profile, entry = _profile()
    provider = _Provider({"restated": [{"new": "e1.n1", "held": "e1.h2"}]})
    ops, receipts = await hold_back_restatements(profile, [_op(entry.id)], provider)
    assert ops[0].responsibilities == ["Budgetverantwortung von rund 6 Mio. € pro Jahr."]
    assert ops[0].technologies == ["SAP PP"]
    assert len(receipts) == 1
    r = receipts[0]
    assert (r.entity_id, r.field, r.text, r.restates) == (
        entry.id, "responsibilities", "Shopfloor-Management und KVP eingeführt.", HELD[1])
    assert r.entity_label and "Weberit" in r.entity_label
    # the call saw the held and the new bullets, nothing else
    assert "e1.h2: Einführung von Shopfloor-Management" in provider.prompts[0]
    assert "e1.n2: Budgetverantwortung" in provider.prompts[0]


@pytest.mark.asyncio
async def test_held_bullets_are_never_touched():
    profile, entry = _profile()
    provider = _Provider({"restated": [{"new": "e1.n1", "held": "e1.h2"}]})
    await hold_back_restatements(profile, [_op(entry.id)], provider)
    assert profile.work_experience[0].responsibilities == HELD


@pytest.mark.asyncio
async def test_an_op_left_empty_is_dropped_but_other_ops_pass_through():
    profile, entry = _profile()
    op = AddBullets(target=entry.id, responsibilities=["Shopfloor-Management und KVP eingeführt."])
    skill = UpsertSkill(name="OEE")
    provider = _Provider({"restated": [{"new": "e1.n1", "held": "e1.h2"}]})
    ops, receipts = await hold_back_restatements(profile, [op, skill], provider)
    assert ops == [skill] and len(receipts) == 1


@pytest.mark.parametrize("answer", [
    {"restated": [{"new": "e1.n9", "held": "e1.h1"}]},     # index out of range
    {"restated": [{"new": "e1.n1", "held": "e1.h9"}]},     # held out of range
    {"restated": [{"new": "e2.n1", "held": "e2.h1"}]},     # no such entry
    {"restated": [{"new": "e1.h1", "held": "e1.n1"}]},     # swapped kinds
    {"restated": "e1.n1"},                                 # wrong shape
    ["e1.n1"],                                             # not an object
])
@pytest.mark.asyncio
async def test_an_invalid_answer_writes_everything(answer):
    profile, entry = _profile()
    op = _op(entry.id)
    ops, receipts = await hold_back_restatements(profile, [op], _Provider(answer))
    assert ops == [op] and receipts == []


@pytest.mark.asyncio
async def test_fail_open_on_provider_error():
    profile, entry = _profile()
    op = _op(entry.id)
    ops, receipts = await hold_back_restatements(profile, [op], _Provider(fail=True))
    assert ops == [op] and receipts == []


@pytest.mark.asyncio
async def test_no_call_without_an_add_bullets_onto_a_held_entry():
    profile, entry = _profile()
    provider = _Provider()
    new_entity = AddBullets(target="w1", responsibilities=["x"])   # local ref, new entity
    empty_entry = WorkEntry(company="Acme", role="Dev")
    profile.work_experience.append(empty_entry)                    # holds no bullets
    onto_empty = AddBullets(target=empty_entry.id, responsibilities=["y"])
    ops, receipts = await hold_back_restatements(profile, [new_entity, onto_empty], provider)
    assert provider.prompts == [] and receipts == [] and len(ops) == 2


# ── the seams: both import paths run the check before apply_ops ──────────────

def _merge_fixture():
    existing, entry = _profile()
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Weberit", role="Produktionsleiter",
        responsibilities=["Shopfloor-Management und KVP eingeführt.",
                          "Budgetverantwortung von rund 6 Mio. € pro Jahr."])])
    reconcile_answer = {"ops": [{"op": "add_bullets", "target": entry.id,
                                  "responsibilities": ["Shopfloor-Management und KVP eingeführt.",
                                                       "Budgetverantwortung von rund 6 Mio. € pro Jahr."]}]}
    check_answer = {"restated": [{"new": "e1.n1", "held": "e1.h2"}]}
    return existing, incoming, entry, reconcile_answer, check_answer


def _bullets(merged, entry_id):
    return next(w for w in merged.work_experience if w.id == entry_id).responsibilities


@pytest.mark.asyncio
async def test_seam_fast_path_holds_back_and_receipts():
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    existing, incoming, entry, rec, chk = _merge_fixture()
    provider = _Provider(rec, chk)
    result = await reconcile_import(existing, incoming, "linkedin_import", provider)
    bullets = _bullets(result.merged_profile, entry.id)
    assert "Budgetverantwortung von rund 6 Mio. € pro Jahr." in bullets
    assert "Shopfloor-Management und KVP eingeführt." not in bullets
    assert [r.text for r in result.restated] == ["Shopfloor-Management und KVP eingeführt."]
    assert len(provider.prompts) == 2


@pytest.mark.asyncio
async def test_seam_segmented_path_holds_back_and_receipts():
    from applire.exceptions import LLMTruncatedError
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    existing, incoming, entry, rec, chk = _merge_fixture()

    class _Truncating(_Provider):
        async def aparse_json(self, prompt, **kw):
            if not self.prompts:
                self.prompts.append(prompt)
                raise LLMTruncatedError("cap")
            return await super().aparse_json(prompt, **kw)

    provider = _Truncating(rec, chk)
    result = await reconcile_import(existing, incoming, "linkedin_import", provider)
    bullets = _bullets(result.merged_profile, entry.id)
    assert "Shopfloor-Management und KVP eingeführt." not in bullets
    assert "Budgetverantwortung von rund 6 Mio. € pro Jahr." in bullets
    assert [r.text for r in result.restated] == ["Shopfloor-Management und KVP eingeführt."]


@pytest.mark.asyncio
async def test_receipt_rides_onto_the_trail_record():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ApplyImportMerge
    from applire.schemas.profile import RestatedReceipt

    existing, entry = _profile()
    receipt = RestatedReceipt(entity_id=entry.id, section="work_experience",
                              field="responsibilities", text="t", restates=HELD[0])
    applied = apply_ops(existing, [ApplyImportMerge(merged=existing, restated=[receipt])], "cv_upload")
    assert applied.restated == [receipt]


# ── "Trotzdem übernehmen": POST /api/profile/restated/apply ──────────────────

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def client_and_entry():
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.auth import get_auth_provider
    from applire.db.session import Base, get_db
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.routers.profile import router
    from applire.schemas.profile import EnrichmentRecord, ProfileMetadata, RestatedReceipt
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    profile, entry = _profile()
    profile.metadata = ProfileMetadata(enrichment_history=[EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="linkedin_import",
        restated=[RestatedReceipt(entity_id=entry.id, section="work_experience",
                                  field="responsibilities",
                                  text="Shopfloor-Management und KVP eingeführt.",
                                  restates=HELD[1])])])
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[MasterProfile.__table__, User.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(make_master_profile(profile_json=profile.model_dump(mode="json")))
        await session.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: session
        auth = MagicMock()
        auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
        app.dependency_overrides[get_auth_provider] = lambda: auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, entry
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_restated_writes_the_receipted_text_once(client_and_entry):
    client, entry = client_and_entry
    body = {"entity_id": entry.id, "field": "responsibilities",
            "text": "Shopfloor-Management und KVP eingeführt."}
    first = await client.post("/api/profile/restated/apply", json=body)
    assert first.status_code == 200
    bullets = first.json()["profile"]["work_experience"][0]["responsibilities"]
    assert bullets == HELD + ["Shopfloor-Management und KVP eingeführt."]
    again = await client.post("/api/profile/restated/apply", json=body)
    assert again.json()["profile"]["work_experience"][0]["responsibilities"] == bullets


@pytest.mark.asyncio
async def test_apply_restated_refuses_a_text_no_receipt_names(client_and_entry):
    client, entry = client_and_entry
    res = await client.post("/api/profile/restated/apply", json={
        "entity_id": entry.id, "field": "responsibilities", "text": "Erfundener Text."})
    assert res.status_code == 409 and res.json()["detail"] == "unknown"
