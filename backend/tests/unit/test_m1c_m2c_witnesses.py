# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder rulings M-1c and M-2c — the two applier witnesses.

M-1c: a turn that STATED something and wrote nothing leaves a receipt.
M-2c: a bullet (or a free-text `set_field`) whose OWN words name an employer
other than its target is rerouted into a confirmation, never silently applied.

Both are deterministic FACTS (ADR-062 clause 1), and both are mutation-killed:
each has one test that fails if the new branch is removed, named in the test's
own docstring.
"""

from __future__ import annotations

import uuid

import pytest

from applire.schemas.profile import (
    MasterProfileData,
    PersonalInfo,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
    SetField,
    UpsertSkill,
    UpsertWork,
)
from applire.services.profile.reconcile.witness import (
    compute_no_write,
    positive_residue,
)

# The S7 shape the matrix measured: one station in the vault, an answer naming
# three employers (`o3/failure-taxonomy-2026-09-09.md` §3.3).
_NOVA_ID = "w-nova"
_ANSWER = (
    "While I have not worked with insulin in particular, I have 15+ years of "
    "experience with manufacturing in the pharmaceutical industry: monoclonal "
    "antibodies at Helvetia Pharma, blood bags at the Blutspendedienst, and now "
    "mRNA vaccines at NovaRNA Biotech."
)
_FOLDED_BULLET = (
    "15+ years of experience with manufacturing in the pharmaceutical industry "
    "across monoclonal antibodies at Helvetia Pharma, blood bags at the "
    "Blutspendedienst and mRNA vaccines"
)


def _one_station_profile() -> MasterProfileData:
    return MasterProfileData(
        personal_info=PersonalInfo(full_name="Lena Fischer"),
        work_experience=[
            WorkEntry(
                id=_NOVA_ID,
                company="NovaRNA Biotech GmbH",
                role="Associate Director Supply Chain Systems",
                is_current=True,
            )
        ],
    )


def _three_station_profile() -> MasterProfileData:
    return MasterProfileData(
        personal_info=PersonalInfo(full_name="Lena Fischer"),
        work_experience=[
            WorkEntry(id="w-helv", company="Helvetia Pharma AG", role="IT Lead"),
            WorkEntry(id="w-bsd", company="Blutspendedienst Nord gGmbH", role="Specialist"),
            WorkEntry(id=_NOVA_ID, company="NovaRNA Biotech GmbH", role="Director"),
        ],
    )


def _turn(answer: str = _ANSWER) -> dict[str, str]:
    return {"gap": "insulin manufacturing", "question": "Any insulin work?", "answer": answer}


# ── M-2c — the bullet's own words ────────────────────────────────────────────


def test_a_bullet_naming_two_absent_employers_is_rerouted_when_the_batch_creates_them():
    """The S7 fold. MUTATION KILL for the in-batch candidate set: drop
    `_batch_company_candidates` from the union in `enforce_attribution` and this
    test fails — the vault alone cannot name Helvetia or the Blutspendedienst."""
    profile = _one_station_profile()
    ops = [
        UpsertWork(ref="w1", company="Helvetia Pharma"),
        UpsertWork(ref="w2", company="Blutspendedienst Nord"),
        AddBullets(target=_NOVA_ID, achievements=[_FOLDED_BULLET]),
    ]
    out = enforce_attribution(ops, profile=profile, new_info=_turn(), source="interview")
    confirmations = [op for op in out if isinstance(op, RequestConfirmation)]
    assert confirmations, "the folded bullet was applied to NovaRNA silently"
    kept = [op for op in out if isinstance(op, AddBullets)]
    assert all(_FOLDED_BULLET not in op.achievements for op in kept)


def test_the_same_fold_is_caught_when_all_three_employers_are_already_in_the_vault():
    """S6/S8: no `upsert_work` in the batch, the vault carries all three."""
    ops = [AddBullets(target=_NOVA_ID, achievements=[_FOLDED_BULLET])]
    out = enforce_attribution(
        ops, profile=_three_station_profile(), new_info=_turn(), source="interview"
    )
    assert any(isinstance(op, RequestConfirmation) for op in out)


def test_a_bullet_that_names_only_its_own_employer_is_left_alone():
    """Over-drop discipline: naming the TARGET is not a mis-attribution."""
    bullet = "Rolled out the batch-release system at NovaRNA Biotech"
    ops = [AddBullets(target=_NOVA_ID, achievements=[bullet])]
    out = enforce_attribution(
        ops, profile=_one_station_profile(), new_info=_turn(), source="interview"
    )
    assert out == ops


def test_a_bullet_that_names_no_employer_at_all_is_left_alone():
    ops = [AddBullets(target=_NOVA_ID, responsibilities=["Ran the release board"])]
    out = enforce_attribution(
        ops, profile=_one_station_profile(), new_info=_turn(), source="interview"
    )
    assert out == ops


def test_set_field_industry_context_folding_three_employers_is_rerouted():
    """`ministral-8b`'s shape (taxonomy §3.3): the same fold through `set_field`.

    MUTATION KILL for `_guard_set_field`: delete the `SetField` branch in
    `enforce_attribution` and this test fails."""
    ops = [
        SetField(
            target=_NOVA_ID,
            field="industry_context",
            value="Pharmaceutical manufacturing at Helvetia Pharma and the Blutspendedienst",
        )
    ]
    out = enforce_attribution(
        ops, profile=_three_station_profile(), new_info=_turn(), source="interview"
    )
    assert not [op for op in out if isinstance(op, SetField)], "the value was written anyway"
    assert any(isinstance(op, RequestConfirmation) for op in out)


def test_the_documented_limit_a_lone_absent_employer_is_not_nameable():
    """The honest boundary of both channels, stated as a test rather than a hope.

    On a one-station vault, with NO `upsert_work` in the batch to declare them,
    "Helvetia Pharma" and "Blutspendedienst" are strings this module has no
    reason to believe are employers — inferring that would be a judgement, which
    ADR-062 clause 1 forbids the deterministic layer from making. The prompt
    rule (M-2) is what turns them into declared employers, and the moment it
    does the test above catches the fold. This is the fail-open half of #243's
    discipline, kept deliberately."""
    ops = [
        SetField(
            target=_NOVA_ID,
            field="industry_context",
            value="Pharmaceutical manufacturing at Helvetia Pharma and the Blutspendedienst",
        )
    ]
    out = enforce_attribution(
        ops, profile=_one_station_profile(), new_info=_turn(), source="interview"
    )
    assert out == ops


def test_a_set_field_on_a_date_is_never_guarded():
    """Only free-text employer-context fields are in scope."""
    ops = [SetField(target=_NOVA_ID, field="start_date", value="2021-01")]
    out = enforce_attribution(
        ops, profile=_one_station_profile(), new_info=_turn(), source="interview"
    )
    assert out == ops


def test_a_non_interview_source_is_untouched():
    """#243's interview-turn-only restriction still holds for both channels."""
    ops = [AddBullets(target=_NOVA_ID, achievements=[_FOLDED_BULLET])]
    out = enforce_attribution(
        ops, profile=_three_station_profile(), new_info=_turn(), source="cv_upload"
    )
    assert out == ops


# ── M-1c — the no-write witness ──────────────────────────────────────────────


def test_the_denial_opening_answer_has_a_positive_residue():
    residue = positive_residue(_ANSWER)
    assert residue and "15+ years" in residue[0]


@pytest.mark.parametrize(
    "answer",
    [
        "I have never used Azure.",
        "Nein, damit hatte ich nie zu tun.",
        # A LIST of denied items must not read as a statement.
        "I have not worked with insulin, Azure or Kubernetes in production systems.",
        "Just saying hello",
        "",
    ],
)
def test_an_answer_that_states_nothing_produces_no_receipt(answer):
    assert compute_no_write(answer) == []


def test_a_german_concessive_opening_still_yields_its_statement():
    residue = positive_residue(
        "Obwohl ich kein Insulin gemacht habe, leite ich seit 2021 die Fertigungs-IT bei NovaRNA."
    )
    assert residue == ["leite ich seit 2021 die Fertigungs-IT bei NovaRNA."]


def test_a_silent_turn_leaves_a_receipt_on_the_not_applied_channel():
    """The mechanism 8 of 11 models produced. MUTATION KILL for the witness:
    remove the `compute_no_write` block at the end of `apply_ops` and this fails."""
    result = apply_ops(_one_station_profile(), [], "interview", turn_text=_ANSWER)
    assert [i.reason for i in result.not_applied] == ["no_write"]
    assert "15+ years" in result.not_applied[0].label


def test_a_turn_that_wrote_something_leaves_no_no_write_receipt():
    result = apply_ops(
        _one_station_profile(),
        [UpsertSkill(name="Sterile manufacturing", category="domain")],
        "interview",
        turn_text=_ANSWER,
    )
    assert not [i for i in result.not_applied if i.reason == "no_write"]


def test_a_turn_that_raised_a_confirmation_leaves_no_no_write_receipt():
    """The loss is already visible to the candidate — one worry, not two."""
    result = apply_ops(
        _one_station_profile(),
        [RequestConfirmation(question="Which employer?", options=["A", "B"])],
        "interview",
        turn_text=_ANSWER,
    )
    assert not [i for i in result.not_applied if i.reason == "no_write"]


def test_no_turn_text_means_no_witness():
    """An import merge has no single human utterance behind it."""
    result = apply_ops(_one_station_profile(), [], "cv_upload")
    assert result.not_applied == []


def test_the_receipt_reaches_the_health_hub_thread():
    """V-6's reader must recognise the new reason rather than print the raw key."""
    from applire.services.profile.health import _NOT_APPLIED_REASON

    assert "no_write" in _NOT_APPLIED_REASON
