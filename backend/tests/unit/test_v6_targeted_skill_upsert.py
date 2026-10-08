# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING V-6 = A (2026-10-08): `upsert_skill` carries an optional `target`.

E2E-3 (a): on the stack's own setting (reasoning OFF) luna linked translated
skills to their jobs with a TARGET-LESS `upsert_skill` under the incoming name
in 6/10 replays of the delivery prompt, and the real path did in 3/3. The op
could not name the existing skill, so every such link created a duplicate. The
target closes that schema gap (ADR-046 / ADR-063 amended 2026-10-08).
"""
from __future__ import annotations

from applire.schemas.profile import MasterProfileData, Skill, WorkEntry
from applire.services.profile.reconcile.alias_writer import record_bound_aliases
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.import_witness import (
    compute_import_not_applied,
    match_existing_bindings,
)
from applire.services.profile.reconcile.ops import UpsertSkill


def _vault() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[WorkEntry(id="w1", company="Finleap Build GmbH", role="Backend-Entwickler",
                                   start_date="2017-06")],
        skills=[
            Skill(id="s-rest", name="REST-Schnittstellenentwicklung", category="technical",
                  proficiency="intermediate"),
            Skill(id="s-py", name="Python", category="technical", proficiency="advanced"),
        ],
    )


def _op(**kw) -> UpsertSkill:
    base = dict(name="REST APIs", category="technical", proficiency="intermediate", evidence=["w1"])
    base.update(kw)
    return UpsertSkill(**base)


def test_targeted_upsert_merges_into_the_target_and_is_receipted():
    applied = apply_ops(_vault(), [_op(target="s-rest")], "cv_upload")
    names = [s.name for s in applied.profile.skills]
    assert names == ["REST-Schnittstellenentwicklung", "Python"]  # no duplicate
    rest = applied.profile.skills[0]
    assert rest.experience_refs == ["w1"]  # the evidence landed on the target
    assert [(m.section, m.entity_id, m.incoming, m.basis) for m in applied.matched] == [
        ("skills", "s-rest", "REST APIs", "model")
    ]


def test_untargeted_upsert_behaves_as_today():
    applied = apply_ops(_vault(), [_op()], "cv_upload")
    assert [s.name for s in applied.profile.skills] == ["REST-Schnittstellenentwicklung", "Python", "REST APIs"]
    assert applied.matched == []


def test_unknown_target_behaves_as_today():
    applied = apply_ops(_vault(), [_op(target="no-such-id")], "cv_upload")
    assert "REST APIs" in [s.name for s in applied.profile.skills]
    assert applied.matched == []


def test_a_target_never_overrides_an_entry_that_holds_the_name():
    """A fact check, not a judgement: "Python" is an entry of its own, so a
    target pointing elsewhere is ignored and the ordinary path runs."""
    applied = apply_ops(_vault(), [_op(name="Python", target="s-rest")], "cv_upload")
    rest = next(s for s in applied.profile.skills if s.id == "s-rest")
    py = next(s for s in applied.profile.skills if s.id == "s-py")
    assert rest.experience_refs == [] and py.experience_refs == ["w1"]
    assert applied.matched == []


def test_identical_name_target_leaves_no_receipt():
    applied = apply_ops(_vault(), [_op(name="Python", target="s-py")], "cv_upload")
    assert applied.matched == []
    assert len(applied.profile.skills) == 2


def test_import_bridge_records_the_document_name_as_alias():
    incoming = MasterProfileData(skills=[Skill(name="REST APIs", category="technical")])
    ops = [_op(target="s-rest")]
    applied = apply_ops(_vault(), ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    rest = applied.profile.skills[0]
    assert rest.aliases == ["REST APIs"]
    assert applied.matched[0].aliases_added == {"aliases": "REST APIs"}
    assert applied.matched[0].incoming_entry is not None


def test_the_applier_alone_records_no_alias():
    """Turn doors run the applier and never the bridge: no alias there."""
    applied = apply_ops(_vault(), [_op(target="s-rest")], "interview")
    assert applied.profile.skills[0].aliases == []


def test_binder_binds_a_targeted_skill_upsert_to_its_document_entry():
    incoming = MasterProfileData(skills=[Skill(name="REST APIs"), Skill(name="Django")])
    vault = _vault()
    bindings = match_existing_bindings("skills", incoming.skills, vault.skills, [_op(target="s-rest")])
    assert [(t.id, e.name) for _op_, t, e in bindings] == [("s-rest", "REST APIs")]


def test_witness_carries_the_targeted_entry():
    incoming = MasterProfileData(skills=[Skill(name="REST APIs")])
    ops = [_op(target="s-rest")]
    applied = apply_ops(_vault(), ops, "cv_upload")
    assert compute_import_not_applied(incoming, applied.profile, ops) == []
