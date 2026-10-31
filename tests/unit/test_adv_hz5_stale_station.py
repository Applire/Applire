# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADV-HZ5 — "Wohin gehört das?" offers stations by the persisted Oracle evidence
INDEX (``work_experience[N]``) resolved against the profile as it is NOW. After a
U-11 entry merge (or any reorder) between the audit and the click, N names
another entry: the card offers the wrong employer and the move writes the fact
under it."""
from __future__ import annotations

import copy
import uuid

import pytest

from applire.services import delivery_actions as da
from applire.services.oracle.selfaudit import build_self_audit_report
import importlib.util, pathlib, sys
_spec = importlib.util.spec_from_file_location("_adr099da", pathlib.Path(__file__).with_name("test_adr099_delivery_actions.py"))
_m = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_m)
WRONG_PLACE, _key, _Twin, VAULT, A, B = _m.WRONG_PLACE, _m._key, _m._Twin, _m.VAULT, _m.A, _m.B
# WRONG_PLACE, _key, _Twin, VAULT, A, B
from applire.schemas.cv import TailoredCVData

C = "33333333-3333-3333-3333-333333333333"
D = "44444444-4444-4444-4444-444444444444"


def _vault_at_audit():
    v = copy.deepcopy(VAULT)
    a, b = v["work_experience"]
    dup = {**copy.deepcopy(a), "id": D, "company": "Muster Logistik", "responsibilities": []}
    c = {"id": C, "company": "Süd Transport KG", "title": "Sachbearbeiterin",
         "start_date": "2010-01", "end_date": "2013-12", "responsibilities": ["Rechnungen geprüft"]}
    v["work_experience"] = [a, dup, b, c]
    return v


def _tailored():
    return TailoredCVData.model_validate({
        "contact": {"name": "Erika Muster", "email": "erika@example.com"},
        "summary": "Finanzbuchhalterin.",
        "work_history": [
            {"id": A, "company": "Muster Logistik GmbH", "role": "Finanzbuchhalterin",
             "start_date": "2019-01", "bullets": ["Monatsabschlüsse nach HGB vorbereitet", WRONG_PLACE]},
            {"id": B, "company": "Nord Spedition AG", "role": "Buchhalterin",
             "start_date": "2014-01", "end_date": "2018-12", "bullets": []},
            {"id": C, "company": "Süd Transport KG", "role": "Sachbearbeiterin",
             "start_date": "2010-01", "end_date": "2013-12", "bullets": ["Rechnungen geprüft"]},
        ],
        "skills": ["DATEV"],
    })


@pytest.mark.asyncio
async def test_station_answer_after_a_merge_names_the_owner(monkeypatch):
    at_audit = _vault_at_audit()
    twin = _Twin(_tailored())
    twin.install(monkeypatch)
    twin.record.truthfulness_report = await build_self_audit_report(
        at_audit, tailored_data=twin.current().model_dump(mode="json"), document_language="de")
    key = _key(twin, "misattributed")
    # the candidate merges the duplicate D into A on the profile page (U-11) — D leaves
    now = copy.deepcopy(at_audit)
    now["work_experience"] = [e for e in now["work_experience"] if e["id"] != D]

    async def _profile_json(record, db):
        return now

    monkeypatch.setattr(da, "_profile_json", _profile_json)
    stations = await da.owning_stations("cv", twin.record.id, key, None)
    assert [s.experience_id for s in stations] == [B], (
        f"offered {[ (s.experience_id, s.organization) for s in stations]} — the evidence is B's"
    )


# ── WP-C fix (ADV-4): resolve by what the entry HOLDS, refuse when unsure ─────


async def _audited_twin(monkeypatch):
    at_audit = _vault_at_audit()
    twin = _Twin(_tailored())
    twin.install(monkeypatch)
    twin.record.truthfulness_report = await build_self_audit_report(
        at_audit, tailored_data=twin.current().model_dump(mode="json"), document_language="de")
    return twin, at_audit, _key(twin, "misattributed")


def _profile_now(monkeypatch, entries):
    async def _profile_json(record, db):
        return {**_vault_at_audit(), "work_experience": entries}

    monkeypatch.setattr(da, "_profile_json", _profile_json)


def _by_id(vault, *ids):
    pool = {e["id"]: e for e in vault["work_experience"]}
    return [copy.deepcopy(pool[i]) for i in ids]


@pytest.mark.asyncio
async def test_unchanged_profile_still_offers_the_owner(monkeypatch):
    twin, at_audit, key = await _audited_twin(monkeypatch)
    _profile_now(monkeypatch, copy.deepcopy(at_audit["work_experience"]))
    stations = await da.owning_stations("cv", twin.record.id, key, None)
    assert [s.experience_id for s in stations] == [B]


@pytest.mark.asyncio
async def test_owner_gone_from_the_profile_is_refused_never_guessed(monkeypatch):
    twin, at_audit, key = await _audited_twin(monkeypatch)
    _profile_now(monkeypatch, _by_id(at_audit, A, D, C))  # B deleted; index 2 now names C
    with pytest.raises(da.ActionNotApplicable, match="profile changed"):
        await da.owning_stations("cv", twin.record.id, key, None)


@pytest.mark.asyncio
async def test_two_holders_neither_at_the_index_is_refused(monkeypatch):
    twin, at_audit, key = await _audited_twin(monkeypatch)
    b2 = {**_by_id(at_audit, B)[0], "id": "55555555-5555-5555-5555-555555555555", "company": "Nord Spedition (Kopie)"}
    _profile_now(monkeypatch, _by_id(at_audit, A, D, C, B) + [b2])
    with pytest.raises(da.ActionNotApplicable, match="ambiguous"):
        await da.owning_stations("cv", twin.record.id, key, None)


@pytest.mark.asyncio
async def test_a_move_on_stale_evidence_writes_nothing(monkeypatch):
    twin, at_audit, key = await _audited_twin(monkeypatch)
    _profile_now(monkeypatch, _by_id(at_audit, A, C))  # B gone: no station can be trusted
    c_pos = next(p for p in twin.record.content_snapshot["positions"] if p["work_id"] == C)
    model = _m._Model(WRONG_PLACE)
    before = dict(twin.record.section_overrides)
    with pytest.raises(da.ActionNotApplicable):
        await da.move_sentence("cv", twin.record.id, key, f"position::{c_pos['id']}", None, model)
    assert twin.record.section_overrides == before and model.prompts == []
