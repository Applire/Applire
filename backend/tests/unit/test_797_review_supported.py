# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bug #797 half 2 — founder ruling D2-5 = A (ADR-090 cl. 6 amended 2026-10-21).

*Stimmt, in mein Profil aufnehmen* on a finding the profile already holds closes
it in one click: no testimony ask, a ``supported`` decision with a receipt that
names the profile line, the row closed even while the keyword check still lists
it; a matching UNCONFIRMED skill is confirmed by the same click (ADR-061 cl. 3).
Both vault shapes the UX audit left open are covered: the interview's skill
merge folded/renamed into confirmed skills, and skills written ``unconfirmed``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

import applire.services.review_actions as ra
import applire.services.review_state as rs
from tests.support.profile_factory import set_profile_json
from tests.unit.test_review_endpoints import (  # noqa: F401 — `db` is a fixture
    FakeReaudit,
    _ats_report,
    _client,
    db,
    seed_cv,
)

_TERM = "PP/MM"
_KEY = rs.finding_key("ats", _TERM)
_MATCHES = {_TERM: [{"form": "PP/MM", "stem": False}, {"form": "SAP PP", "stem": False},
                    {"form": "SAP MM", "stem": False}]}


def _role(bullets):
    return {"id": "11111111-1111-1111-1111-111111111111", "company": "Weberit", "title": "Produktionsleiter",
            "start_date": "2017-04", "end_date": None, "responsibilities": list(bullets)}


#: Shape 1 — the interview merge renamed "SAP" to "SAP PP" and added "SAP MM";
#: the answers landed as experience lines.
MERGED = {
    "personal_info": {"name": "Stefan Probe"},
    "work_experience": [_role(["Key-User für SAP PP beim SAP-Rollout.", "Tägliche PP/MM-Praxis."])],
    "skills": [{"name": "SAP PP", "status": "confirmed"}, {"name": "SAP MM", "status": "confirmed"}],
}
#: Shape 2 — the two modules exist only as UNCONFIRMED skills.
UNCONFIRMED = {
    "personal_info": {"name": "Stefan Probe"},
    "work_experience": [_role(["Mitarbeit bei der Einführung von SAP in der Fertigung."])],
    "skills": [{"name": "SAP", "status": "confirmed"},
               {"name": "SAP PP", "status": "unconfirmed"}, {"name": "SAP MM", "status": "unconfirmed"}],
}


# ── the pure predicate ───────────────────────────────────────────────────────

def test_support_in_profile_reads_both_vault_shapes():
    receipt, unconfirmed = ra.support_in_profile(["PP/MM", "SAP PP", "SAP MM"], MERGED)
    assert receipt and unconfirmed == []
    receipt, unconfirmed = ra.support_in_profile(["PP/MM", "SAP PP", "SAP MM"], UNCONFIRMED)
    assert receipt and sorted(unconfirmed) == ["SAP MM", "SAP PP"]


def test_support_in_profile_refuses_a_missing_or_denied_wording():
    assert ra.support_in_profile(["SAP QM"], MERGED) is None
    denied = dict(MERGED, skills=[{"name": "SAP PP", "status": "denied"}, {"name": "SAP MM", "status": "confirmed"}],
                  work_experience=[_role(["Tägliche Disposition."])])
    assert ra.support_in_profile(["SAP PP"], denied) is None
    with_concept = dict(MERGED, metadata={"denied_concepts": [{"concept": "SAP PP", "statement": "nie"}]})
    assert ra.support_in_profile(["SAP PP"], with_concept) is None


def test_a_supported_decision_no_longer_closes_a_still_listed_row():
    """ADR-099 cl. 6 (UAT-2, WP-C) retired D2-5's label closure: "Stimmt"
    without a vault change no longer closes a row — the live report decides."""
    ats = _ats_report("cv", [_TERM], _MATCHES)
    state = rs.with_decision(rs.load_state(None), _KEY, _TERM, "supported",
                             receipt=[{"path": "skills[0]", "text": "SAP PP"}])
    out = rs.derive_review(state, ats, None)
    assert out["open_count"] == 1 and out["decided_count"] == 0
    # control: any other action on a listed finding leaves it open (cl. 6 unchanged)
    kept = rs.with_decision(rs.load_state(None), _KEY, _TERM, "edited")
    assert rs.derive_review(kept, ats, None)["open_count"] == 1


# ── the endpoint ─────────────────────────────────────────────────────────────

async def _seed(db, profile_json):
    from applire.models.cv import GeneratedCV
    from applire.models.profile import MasterProfile

    cv_id = await seed_cv(db, introduction="Erfahrung mit SAP PP/MM.",
                          ats_report=_ats_report("cv", [_TERM], _MATCHES))
    cv = await db.get(GeneratedCV, cv_id)
    profile = await db.get(MasterProfile, cv.profile_id)
    set_profile_json(profile, profile_json)
    await db.commit()
    return cv_id, cv.profile_id


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["merged", "unconfirmed"])
async def test_supported_confirms_the_skill_without_asking_but_does_not_close_a_listed_finding(db, shape):
    from applire.models.profile import MasterProfile

    cv_id, profile_id = await _seed(db, MERGED if shape == "merged" else UNCONFIRMED)
    client = _client(db)
    still = FakeReaudit([(_ats_report("cv", [_TERM], _MATCHES), None)])  # the check still lists it
    with patch.object(ra, "reaudit", new=still), \
         patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as testimony:
        response = client.post(f"/api/cv/{cv_id}/review/supported", json={"finding_key": _KEY})
    assert response.status_code == 200, response.text
    assert testimony.call_count == 0, "no testimony ask"
    data = response.json()
    assert data["still_listed"] is True
    [decision] = data["review_state"]["decisions"]
    assert decision["action"] == "supported"
    assert decision["receipt"] and all(r["text"] for r in decision["receipt"])
    # ADR-099 cl. 6: the decision is recorded, but a still-listed finding stays open
    assert rs.derive_review(data["review_state"], _ats_report("cv", [_TERM], _MATCHES), None)["open_count"] == 1
    profile = (await db.execute(select(MasterProfile).where(MasterProfile.id == profile_id))).scalar_one()
    await db.refresh(profile)
    statuses = {s["name"]: s.get("status") for s in profile.profile_json["skills"]}
    assert statuses.get("SAP PP") == "confirmed" and statuses.get("SAP MM") == "confirmed"


@pytest.mark.asyncio
async def test_supported_on_a_wording_the_profile_lacks_returns_409_and_writes_nothing(db):
    cv_id, _ = await _seed(db, {"personal_info": {"name": "S"}, "work_experience": [_role(["Disposition."])],
                                "skills": [{"name": "SAP", "status": "confirmed"}]})
    client = _client(db)
    reaudit = FakeReaudit()
    with patch.object(ra, "reaudit", new=reaudit):
        response = client.post(f"/api/cv/{cv_id}/review/supported", json={"finding_key": _KEY})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "not_in_profile"
    assert reaudit.calls == 0
