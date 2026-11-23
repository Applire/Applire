# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V item 4 (open check from wave 2b): a later turn whose
bullets name another people count never overwrites a team size the person
answered (Q3-2 / Q8-2).

Read from the code: the role-facts projection (``role_facts.project_role_facts``)
never writes ``team_size`` (it records a projection beside it); ``upsert_work``
fills empties only; ``set_field`` writes an empty field only. Measured on the
captured delivery-q10 rec 182 replay (10 Luna responses) one ``upsert_work``
re-stated ``team_size: 14`` for a held role — the shape these tests pin.
Synthetic profile.
"""
from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import AddBullets, SetField, UpsertWork


def _profile():
    return MasterProfileData.model_validate({"work_experience": [{
        "id": "w-ras", "company": "Rasselstein Umformtechnik GmbH", "role": "Schichtleiter",
        "start_date": "2011-08", "end_date": "2017-03", "team_size": 12,
        "responsibilities": ["Führung einer Schicht mit 12 Mitarbeitenden."],
    }]})


def _size(ops):
    return apply_ops(_profile(), ops, source="interview").profile.work_experience[0].team_size


def test_a_bullet_naming_another_count_leaves_the_answered_size():
    assert _size([AddBullets(target="w-ras", responsibilities=["Einarbeitung von 14 Mitarbeitenden."])]) == 12


def test_an_upsert_with_another_size_fills_empties_only():
    op = UpsertWork(ref="w1", target="w-ras", company="Rasselstein Umformtechnik GmbH",
                    role="Schichtleiter", team_size=14)
    assert _size([op]) == 12


def test_a_set_field_never_overwrites_a_stored_size():
    assert _size([SetField(target="w-ras", field="team_size", value="14")]) == 12


def test_the_projection_records_beside_the_size_and_never_writes_it():
    result = apply_ops(_profile(), [AddBullets(target="w-ras", responsibilities=["14 Mitarbeitende."])],
                       source="interview")
    entry = result.profile.work_experience[0]
    assert entry.team_size == 12
    assert entry.role_fact_projections["team_size"].value == "12"
