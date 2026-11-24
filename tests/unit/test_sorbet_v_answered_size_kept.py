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


# ── F-4 (adv/vault): an entry merge carries the ANSWERED size like the kind ──
# Live/offline repro: imported 14 (target) + answered 38 (source) merged → 14,
# while the kind record said 38. Every write path to ``team_size`` is listed in
# v/report.md "Fix round after adv/vault" with the test that pins it.

import pytest  # noqa: E402

from applire.services.profile.leadership_kind import (  # noqa: E402
    apply_leadership_kind_op,
    leadership_kind_op,
)
from applire.services.profile.reconcile.ops import MergeEntries  # noqa: E402
from applire.services.profile.role_facts import entries_needing_leadership_kind  # noqa: E402


def _two_weberit(answer_on="w_ans", size=38, kind="disciplinary", other=None):
    p = MasterProfileData.model_validate({"work_experience": [
        {"id": "w_imp", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "is_current": True, "team_size": 14,
         "responsibilities": ["Führung von 14 Mitarbeitenden"]},
        {"id": "w_ans", "company": "Weberit Kunststofftechnik", "role": "Produktionsleiter",
         "start_date": "2017-04", "is_current": True,
         "responsibilities": ["Leitung der Spritzgussfertigung"]},
    ]})
    apply_leadership_kind_op(leadership_kind_op(answer_on, kind, answer=f"– {size} Personen", team_size=size), p, [])
    if other is not None:
        o_kind, o_size = other
        o_id = "w_imp" if answer_on == "w_ans" else "w_ans"
        apply_leadership_kind_op(leadership_kind_op(o_id, o_kind, answer=f"– {o_size} Personen",
                                                    team_size=o_size), p, [])
    return p


def _merge(p, target, source):
    bullets = {"responsibilities": ["Führung von 14 Mitarbeitenden", "Leitung der Spritzgussfertigung"]}
    return apply_ops(p, [MergeEntries(section="work_experience", target_id=target, source_id=source,
                                      bullets=bullets)], source="manual_edit")


@pytest.mark.parametrize(("target", "source"), [("w_imp", "w_ans"), ("w_ans", "w_imp")])
def test_f4_a_merge_keeps_the_answered_size_whichever_entry_is_the_target(target, source):
    r = _merge(_two_weberit(), target, source)
    [entry] = r.profile.work_experience
    assert entry.team_size == 38, "the person's answer, never the imported 14"
    assert entry.leadership_kind == "disciplinary" and entry.leadership_kind_record.team_size == 38


def test_f4_the_carried_size_is_receipted():
    r = _merge(_two_weberit(), "w_imp", "w_ans")
    assert any(c.field == "team_size" and c.new_value == 38 for c in r.changes)


def test_f4_two_different_answered_sizes_ask_again_with_the_targets_size():
    p = _two_weberit(answer_on="w_ans", size=38, other=("disciplinary", 40))
    [entry] = _merge(p, "w_imp", "w_ans").profile.work_experience
    assert entry.leadership_kind is None and entry.leadership_kind_record.option == "merge_conflict"
    assert entry.team_size == 40, "target first; the re-ask puts it to the person"
    assert [e["entry_id"] for e in entries_needing_leadership_kind(
        {"work_experience": [entry.model_dump(mode="json")]})] == ["w_imp"]


def test_f4_the_same_answered_size_twice_is_no_conflict():
    p = _two_weberit(answer_on="w_ans", size=38, other=("disciplinary", 38))
    [entry] = _merge(p, "w_imp", "w_ans").profile.work_experience
    assert entry.leadership_kind == "disciplinary" and entry.team_size == 38


@pytest.mark.asyncio
async def test_f4_the_merge_preview_shows_the_size_the_merge_will_store():
    from applire.services.profile.entry_merge import preview_merge

    out = await preview_merge(_two_weberit(), "work_experience", "w_imp", "w_ans", provider=None)
    assert out.merged_entry["team_size"] == 38


def test_f4_an_unanswered_merge_keeps_the_fill_empties_behaviour():
    p = MasterProfileData.model_validate({"work_experience": [
        {"id": "a", "company": "X GmbH", "role": "R", "team_size": 14},
        {"id": "b", "company": "X", "role": "R", "team_size": 20},
    ]})
    [entry] = _merge(p, "a", "b").profile.work_experience
    assert entry.team_size == 14


