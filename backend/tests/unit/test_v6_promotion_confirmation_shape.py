# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling V-6 = B: a dated position that only partly covers an existing
entry with a different title is ASKED about with the existing merge / add-separate
choices. The reconcile prompt tells the model the context shape the V-5
engagement applier reads (`section`, `incoming`, `existing_ids`). This seam test
proves that shape, as the model emits it, survives the model-output strip and
resolves both ways: "add_separate" creates the dated position, "merge" folds it
into the named id.
"""
from applire.schemas.profile import MasterProfileData, ProfileMetadata
from applire.services.profile.reconcile.apply import UserConfirmedEngagement, apply_ops
from applire.services.profile.reconcile.engine import strip_confirmation_extras
from applire.services.profile.reconcile.ops import RequestConfirmation, UpsertWork
from applire.services.session import _engagement_decision

EXISTING = "e1"
MODEL_ASK = {
    "question": "Is 'Senior Designer, 2014-03 – 2015-12' a separate position, or part of "
                "'Senior Designer → Art Director, 2014-03 – 2018-07'?",
    "options": ["merge", "add_separate"],
    "context": {
        "section": "work_experience",
        "incoming": {"op": "upsert_work", "ref": "w2", "target": None, "company": "Hafenblick Agentur",
                     "role": "Senior Designer", "start_date": "2014-03", "end_date": "2015-12"},
        "existing_ids": [EXISTING],
    },
}


def _profile() -> MasterProfileData:
    return MasterProfileData.model_validate({
        "personal_info": {"name": "Milan Novak"},
        "metadata": ProfileMetadata().model_dump(mode="json"),
        "work_experience": [{"id": EXISTING, "company": "Hafenblick Agentur",
                             "role": "Senior Designer → Art Director",
                             "start_date": "2014-03", "end_date": "2018-07"}],
    })


def _resolve(answer: str) -> MasterProfileData:
    conf = RequestConfirmation.model_validate(strip_confirmation_extras(MODEL_ASK))
    ctx = conf.context
    payload = {k: v for k, v in ctx["incoming"].items() if k != "op"}
    decision = _engagement_decision(answer, None)
    target = ctx["existing_ids"][0] if decision == "merge" else None
    return apply_ops(_profile(), [UpsertWork(**payload)], "interview",
                     user_confirmed_engagement=UserConfirmedEngagement(
                         ref=payload["ref"], decision=decision, target_id=target)).profile


def test_add_separate_creates_the_dated_position():
    work = _resolve("add_separate").work_experience
    assert [(w.role, w.start_date) for w in work if w.id != EXISTING] == [("Senior Designer", "2014-03")]


def test_merge_folds_it_into_the_named_entry():
    work = _resolve("merge").work_experience
    assert len(work) == 1 and work[0].id == EXISTING
