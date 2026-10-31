# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""D-10 (Hazelnut build 5, delivery re-run 2026-10-28; U-13 regression) — a
skill chip the writer COMPOSED from attested parts ("Software development with
Delphi and .NET") read ``unbacked``/skillNoEvidence because the whole phrase
occurs nowhere; "Ja, und zwar bei …" changed nothing (the fact IS in the
profile) and the card's only exit deleted a true skill. Synthetic analog of the
delivery-run vault; no Docker, no LLM."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import applire.services.oracle.matchers.grounding as grounding
import applire.services.review_actions as ra
import applire.services.review_state as rs
from applire.services.oracle.audit import _claimable_index
from applire.services.oracle.matchers.grounding import ground_skill_claim
from tests.unit.test_review_endpoints import (  # noqa: F401 — `db` is a fixture
    _ats_report,
    _canned_testimony,
    _client,
    _set_vault_work,
    db,
    seed_cv,
)

SKILL = "Software development with Delphi and .NET"
BULLET = "Spearheaded transition of software development from legacy Delphi 5 to .NET with Visual Studio"
WORK = [
    {"id": "w1", "company": "Nordlicht Software GmbH", "role": "Teamleiter Entwicklung",
     "start_date": "2009-01", "end_date": "2014-12", "achievements": [BULLET]},
    {"id": "w2", "company": "Acme Cloud AG", "role": "Platform Engineer",
     "start_date": "2015-01", "achievements": ["Built Kubernetes clusters for 40 teams"]},
]
VAULT = {"work_experience": WORK, "skills": [{"id": "s1", "name": "Python", "status": "confirmed"}]}


def _ground(name: str):
    return ground_skill_claim(name, _claimable_index(VAULT), None)


# ── the rule: list = every part grounds; relation = all parts in ONE entry ───


@pytest.mark.parametrize("name", [
    SKILL,                                   # the delivery-run chip (relation, one bullet)
    "Delphi and .NET",                       # list
    "Delphi, .NET und Kubernetes",           # list across entries — each part attested
    "Python & Kubernetes",                   # list: a vault skill + a bullet
])
def test_a_compound_of_attested_parts_grounds(name):
    assert _ground(name) is not None


@pytest.mark.parametrize("name", [
    "Delphi and COBOL",                          # a list with one fabricated part
    "Software development with Kubernetes",      # a relation whose parts sit in two entries
    "Del and .NET",                              # a part present only inside a longer token
    "Software development with Delphi and COBOL",  # a relation with a fabricated part
])
def test_negative_controls_stay_unbacked(name):
    assert _ground(name) is None


def test_the_whole_phrase_alone_never_grounded_it__the_baseline():
    with patch.object(grounding, "_ground_compound", lambda *a, **k: None):
        assert _ground(SKILL) is None


# ── the producer: the Oracle grades the chip grounded on the same vault ──────


def _tailored(skills):
    return {"contact": {"name": "Erik"}, "summary": "Entwicklungsleiter.",
            "work_history": [{"company": "Nordlicht Software GmbH", "role": "Teamleiter Entwicklung",
                              "start_date": "2009-01", "bullets": [BULLET]}],
            "skills": skills}


def _verdict(report, text):
    for c in (report or {}).get("claims") or []:
        if (c.get("claim") or {}).get("text") == text:
            return c.get("verdict") or {}
    return None


@pytest.mark.asyncio
async def test_the_oracle_grades_the_composed_chip_grounded():
    from applire.services.oracle.selfaudit import build_self_audit_report

    report = await build_self_audit_report(VAULT, tailored_data=_tailored([SKILL, "Python"]))
    assert _verdict(report, SKILL)["verdict"] == "grounded"
    with patch.object(grounding, "_ground_compound", lambda *a, **k: None):
        before = await build_self_audit_report(VAULT, tailored_data=_tailored([SKILL, "Python"]))
    assert _verdict(before, SKILL)["detail_key"] == "skillNoEvidence"  # the delivery-run card


# ── the card: "Ja + wo" re-audits with the REAL producer and the card clears ─


@pytest.mark.asyncio
async def test_yes_where_clears_the_card_on_recompute(db):
    """The delivery-run end state, replayed: the card was raised by the old
    reading, the answer changes nothing in the vault (the fact is already
    there), and the awaited re-audit — here the REAL Oracle over the CV's
    persisted data and the live vault — no longer lists the claim."""
    from applire.services.oracle.selfaudit import build_self_audit_report

    with patch.object(grounding, "_ground_compound", lambda *a, **k: None):
        old_truth = await build_self_audit_report(VAULT, tailored_data=_tailored([SKILL, "Python"]))
    assert _verdict(old_truth, SKILL)["detail_key"] == "skillNoEvidence"
    cv_id = await seed_cv(db, skills=[SKILL, "Python"], position_bullets=[BULLET],
                          ats_report=_ats_report("cv", []), truthfulness_report=old_truth)
    record = await ra.load_document("cv", cv_id, db)
    await _set_vault_work(db, record, WORK)
    key = rs.finding_key("oracle", SKILL)
    assert rs.find_listed(ra.findings_of(record), key) is not None, "the card is listed before the answer"

    async def real_truth_reaudit(kind, rec, session):
        from applire.models.profile import MasterProfile

        profile = await session.get(MasterProfile, rec.profile_id)
        rec.truthfulness_report = await build_self_audit_report(
            {**(profile.profile_json or {}), "skills": VAULT["skills"]}, tailored_data=rec.tailored_data)
        await session.commit()
        await session.refresh(rec)

    with patch.object(ra, "reaudit", new=real_truth_reaudit), \
            patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as submit:
        submit.return_value = _canned_testimony("no_change")
        r = _client(db).post(f"/api/cv/{cv_id}/review/add-evidence",
                             json={"finding_key": key, "text": "Bei Nordlicht: Umstieg von Delphi 5 auf .NET geleitet."})
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    record = await ra.load_document("cv", cv_id, db)
    assert rs.find_listed(ra.findings_of(record), key) is None
