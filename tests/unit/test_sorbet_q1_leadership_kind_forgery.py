# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q5 security review of 3808819d: a role's leadership kind is written ONLY by
``RecordLeadershipKind`` (the person's answer to its own question).

The review found model-emittable and other doors that could still set
``leadership_kind`` / ``leadership_kind_record``: ``_apply_set_field`` fills any
empty field on the resolved entity, and dispute resolution, import merge, entry
merge and the section door write whole entries. That is the MD-4 class (a model
forging a confirmed fact): "Disziplinarische Führung von N" with no answer.

Two controls, pinned here per door:

* the engine's raw-op parse drops a model-emitted ``set_field`` /
  ``flag_conflict`` naming either field (interview, agent, testimony);
* the committer's class guard (``apply._restore_leadership_kind``) restores
  both fields from the stored profile for every entry no
  ``RecordLeadershipKind`` op in the batch wrote, drops the receipt and leaves
  a ``not_applied`` item ``leadership_kind_unasked`` — whatever op did it.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    ApplyImportMerge,
    MergeEntries,
    ReplaceSection,
    ResolveField,
    SetField,
    UpsertWork,
)

ANSWERED = {"option": "functional", "answer": "Fachliche Anleitung",
            "recorded_at": "2026-11-14T10:00:00+00:00", "source": "interview"}


def _profile(**w1) -> MasterProfileData:
    return MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Weberit", "role": "Produktionsleiter", "team_size": 38,
         "responsibilities": ["Direkte Führung von 38 Mitarbeitenden."], **w1},
        {"id": "w2", "company": "Rasselstein", "role": "Meister", "team_size": 14},
    ]})


def _kinds(profile: MasterProfileData):
    return [(e.id, e.leadership_kind, e.leadership_kind_record.option if e.leadership_kind_record else None)
            for e in profile.work_experience]


def _assert_refused(before: MasterProfileData, result) -> None:
    assert _kinds(result.profile) == _kinds(before)
    assert not [c for c in result.changes if c.field in ("leadership_kind", "leadership_kind_record")]
    assert [i.reason for i in result.not_applied].count("leadership_kind_unasked") >= 1


# ── the committer's class guard, op by op ────────────────────────────────────

@pytest.mark.parametrize("w1", [{}, {"leadership_kind": "functional", "leadership_kind_record": ANSWERED}])
@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony", "cv_upload", "manual_edit"])
def test_a_set_field_on_either_field_is_undone_on_every_source(w1, source):
    before = _profile(**w1)
    for field, value in (("leadership_kind", "disciplinary"),
                         ("leadership_kind_record", {"option": "both", "answer": "x"})):
        result = apply_ops(before, [SetField(target="w2", field=field, value=value)], source)
        _assert_refused(before, result)


def test_an_upsert_work_carrying_the_kind_is_ignored():
    before = _profile()
    op = UpsertWork.model_validate({"ref": "r1", "target": "w2", "company": "Rasselstein",
                                    "role": "Meister", "leadership_kind": "disciplinary"})
    result = apply_ops(before, [op], "interview")
    assert _kinds(result.profile) == _kinds(before)


def test_an_import_merge_installing_a_kind_is_undone():
    before = _profile(leadership_kind="functional", leadership_kind_record=ANSWERED)
    merged = before.model_dump(mode="json")
    merged["work_experience"][0]["leadership_kind"] = "disciplinary"
    merged["work_experience"][1]["leadership_kind"] = "disciplinary"
    merged["work_experience"].append({"id": "w3", "company": "Neu", "role": "R", "team_size": 5,
                                      "leadership_kind": "disciplinary"})
    result = apply_ops(before, [ApplyImportMerge(merged=MasterProfileData.model_validate(merged))], "cv_upload")
    assert _kinds(result.profile) == [*_kinds(before), ("w3", None, None)]
    assert [i.reason for i in result.not_applied].count("leadership_kind_unasked") == 3


def test_a_dispute_resolution_on_the_kind_is_undone():
    raw = _profile().model_dump(mode="json")
    raw["metadata"] = {"pending_conflicts": [{
        "conflict_id": "c1", "section": "work_experience", "field": "leadership_kind",
        "entity_id": "w1", "existing_value": None, "incoming_value": "disciplinary", "source": "cv_upload",
    }]}
    before = MasterProfileData.model_validate(raw)
    op = ResolveField(conflict_id="c1", target="w1", section="work_experience",
                      field="leadership_kind", resolution="incoming")
    result = apply_ops(before, [op], "manual_edit")
    assert _kinds(result.profile) == _kinds(before)


@pytest.mark.parametrize("intake", ["section_edit", "mcp_update_profile"])
def test_the_section_door_can_neither_set_nor_wipe_the_kind(intake):
    before = _profile(leadership_kind="functional", leadership_kind_record=ANSWERED)
    payload = before.model_dump(mode="json")["work_experience"]
    for e in payload:
        e.pop("leadership_kind", None)
        e.pop("leadership_kind_record", None)
    payload[1]["leadership_kind"] = "disciplinary"
    payload[1]["leadership_kind_record"] = {"option": "both", "answer": "forged"}
    result = apply_ops(before, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    assert _kinds(result.profile) == _kinds(before)


def test_an_entry_merge_does_not_move_an_answer_to_another_role():
    # The answer was given for w2; w2 is merged into w1. w1 keeps its own (none)
    # and is asked — the safe side, not a carried kind.
    before = MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Weberit", "role": "Leiter", "team_size": 38},
        {"id": "w2", "company": "Weberit GmbH", "role": "Leiter", "team_size": 38,
         "leadership_kind": "disciplinary", "leadership_kind_record": {**ANSWERED, "option": "both"}},
    ]})
    op = MergeEntries(section="work_experience", target_id="w1", source_id="w2")
    result = apply_ops(before, [op], "manual_edit")
    assert [(e.id, e.leadership_kind) for e in result.profile.work_experience] == [("w1", None)]


def test_the_persons_answer_in_the_same_batch_still_lands():
    from applire.services.profile.leadership_kind import leadership_kind_op

    before = _profile()
    result = apply_ops(before, [
        SetField(target="w1", field="leadership_kind", value="functional"),
        leadership_kind_op("w2", "both", answer="Beides"),
    ], "interview")
    assert _kinds(result.profile) == [("w1", None, None), ("w2", "disciplinary", "both")]
    assert [c.entity_id for c in result.changes if c.field == "leadership_kind"] == ["w2"]


# ── the engine's raw-op parse: interview, agent channel, testimony ───────────

class _Stub:
    """A provider double returning one fixed reconcile payload."""

    def __init__(self, ops):
        self.payload = {"ops": ops, "ambiguities": [], "denials": []}

    async def aparse_json(self, prompt, **kwargs):
        return self.payload


def test_the_parse_drops_a_model_emitted_kind_write():
    from applire.services.profile.reconcile.engine import _parse_ops

    raw = [
        {"op": "set_field", "target": "w1", "field": "leadership_kind", "value": "disciplinary"},
        {"op": "set_field", "target": "w1", "field": " leadership_kind_record ", "value": {}},
        {"op": "flag_conflict", "target": "w1", "field": "leadership_kind",
         "existing": None, "incoming": "disciplinary"},
        {"op": "set_field", "target": "w1", "field": "team_size", "value": 38},
    ]
    rejected: list[str] = []
    ops = _parse_ops(raw, rejected=rejected)
    assert [(o.op, o.field) for o in ops] == [("set_field", "team_size")]
    assert rejected == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony"])
async def test_a_reconcile_turn_on_every_statement_door_writes_no_kind(source):
    from applire.services.profile.reconcile.engine import reconcile

    before = _profile()
    result = await reconcile(
        before,
        {"answer": "Ich hatte Personalverantwortung.", "gap": "Führungsart",
         "question": "War deine Führung disziplinarisch oder fachlich?"},
        source,
        _Stub([{"op": "set_field", "target": "w1", "field": "leadership_kind", "value": "disciplinary"}]),
    )
    assert not [o for o in result.ops if getattr(o, "field", None) == "leadership_kind"]
    applied = apply_ops(before, list(result.ops), source)
    assert _kinds(applied.profile) == _kinds(before)


def test_an_editor_that_does_not_round_trip_the_kind_is_not_a_refused_write():
    # The section door carries the stored kind before validation, so an editor
    # payload that simply omits the fields neither wipes it nor reads as a forgery.
    before = _profile(leadership_kind="functional", leadership_kind_record=ANSWERED)
    payload = before.model_dump(mode="json")["work_experience"]
    for e in payload:
        e.pop("leadership_kind", None)
        e.pop("leadership_kind_record", None)
    result = apply_ops(before, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    assert _kinds(result.profile) == _kinds(before)
    assert "leadership_kind_unasked" not in [i.reason for i in result.not_applied]


# ── MD-Q6 R1: the exemption is the exact value the answer wrote ──────────────

def _merge_installing(before: MasterProfileData, kind: str) -> ApplyImportMerge:
    merged = before.model_dump(mode="json")
    merged["work_experience"][0]["leadership_kind"] = kind
    merged["work_experience"][0].pop("leadership_kind_record", None)
    return ApplyImportMerge(merged=MasterProfileData.model_validate(merged))


def test_R1_a_later_import_merge_in_the_same_batch_cannot_replace_the_answer():
    """adv/findings.md "Third pass" R1, probe r1.py: [RecordLeadershipKind(w1,
    functional), ApplyImportMerge(w1 disciplinary)] left w1 disciplinary with no
    record. The person's answer is what stands."""
    from applire.services.profile.leadership_kind import leadership_kind_op

    before = _profile()
    result = apply_ops(before, [
        leadership_kind_op("w1", "functional", answer="Fachliche Anleitung"),
        _merge_installing(before, "disciplinary"),
    ], "interview")
    assert _kinds(result.profile)[0] == ("w1", "functional", "functional")
    assert result.profile.work_experience[0].leadership_kind_record.answer == "Fachliche Anleitung"
    assert "leadership_kind_unasked" in [i.reason for i in result.not_applied]
    # The answer's own receipt survives; nothing else about the kind does.
    assert [(c.entity_id, c.rationale_key) for c in result.changes if c.field == "leadership_kind"] == [
        ("w1", "leadership_kind_recorded"),
    ]


def test_R1_a_later_set_field_on_the_answered_entry_is_put_back():
    from applire.services.profile.leadership_kind import leadership_kind_op

    before = _profile()
    result = apply_ops(before, [
        leadership_kind_op("w1", "own_words", answer="eher beides"),
        SetField(target="w1", field="leadership_kind", value="disciplinary"),
    ], "interview")
    assert _kinds(result.profile)[0] == ("w1", None, "own_words")
    assert "leadership_kind_unasked" in [i.reason for i in result.not_applied]
