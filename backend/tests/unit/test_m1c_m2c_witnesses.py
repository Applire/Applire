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


# ── adversarial pass 2026-09-10 — M-2c false positives ───────────────────────
# Channel 2 has no ambiguity fail-open of its own (unlike #243's owning-sentence
# channel, which already lets a sentence naming two-or-more employers pass
# unguessed — `test_ambiguous_two_employers_in_one_clause_fails_open` in
# `test_reconcile_attribution.py`). Without `_RELATIONAL_MARKER_RE`, naming a
# CLIENT, an ACQUIRER or a PARENT by name — ordinary phrasing for anyone in
# sales, account management or post-M&A engineering — re-created exactly the
# wrong-slot confusion M-2c exists to catch: the candidate's own, correctly
# targeted bullet got pulled into a confirmation asking "did you mean the
# other company", which a confused candidate could answer "yes" to and thereby
# ACTUALLY misfile it — the very defect this witness exists to prevent.

_BOSCH_ID = "w-bosch"
_SIEMENS_ID = "w-siemens"


def _client_account_profile() -> MasterProfileData:
    return MasterProfileData(
        personal_info=PersonalInfo(full_name="Jonas Weber"),
        work_experience=[
            WorkEntry(id=_SIEMENS_ID, company="Siemens AG", role="Sales Engineer"),
            WorkEntry(id=_BOSCH_ID, company="Bosch", role="Key Account Manager", is_current=True),
        ],
    )


def test_a_bullet_naming_a_client_account_is_not_rerouted():
    """MUTATION KILL: delete the `_RELATIONAL_MARKER_RE` guard in
    `_foreign_employers` and this bullet — correctly targeting Bosch — is
    pulled out into a confirmation suggesting it belongs to Siemens instead,
    even though the answer names BOTH companies (#243's own ambiguity
    fail-open would have left it alone had channel 2 not overridden it)."""
    bullet = "Key account manager for the Siemens account, growing YoY revenue by 20%."
    ops = [AddBullets(target=_BOSCH_ID, achievements=[bullet])]
    out = enforce_attribution(
        ops,
        profile=_client_account_profile(),
        new_info={"answer": f"At Bosch, I was the key account manager for the Siemens "
                             f"account, growing YoY revenue by 20%."},
        source="interview",
    )
    assert out == ops, "a correctly targeted client-account bullet must not be rerouted"


def test_a_bullet_naming_an_acquirer_is_not_rerouted():
    """"Migrated the platform after the acquisition by X" names the buyer, not
    a second employer. MUTATION KILL: same guard as above."""
    former_id = "w-acme"
    nordpharm_id = "w-nordpharm"
    profile = MasterProfileData(
        personal_info=PersonalInfo(full_name="Jonas Weber"),
        work_experience=[
            WorkEntry(id=former_id, company="Acme Biotech", role="Platform Engineer"),
            WorkEntry(id=nordpharm_id, company="NordPharm", role="Senior Platform Engineer",
                      is_current=True),
        ],
    )
    bullet = "Migrated the legacy platform to the cloud after the acquisition by Acme Biotech."
    ops = [AddBullets(target=nordpharm_id, achievements=[bullet])]
    out = enforce_attribution(
        ops,
        profile=profile,
        new_info={"answer": f"At NordPharm, I {bullet[0].lower()}{bullet[1:]}"},
        source="interview",
    )
    assert out == ops


def test_set_field_naming_a_client_account_is_not_rerouted():
    """`_guard_set_field` shares `_foreign_employers` — same guard, same fix."""
    ops = [
        SetField(
            target=_BOSCH_ID,
            field="industry_context",
            value="Managed the Siemens account across the automotive sector.",
        )
    ]
    out = enforce_attribution(
        ops,
        profile=_client_account_profile(),
        new_info={"answer": "Managed the Siemens account across the automotive sector."},
        source="interview",
    )
    assert out == ops


def test_the_relational_marker_does_not_blunt_the_real_fold():
    """The guard must not swallow M-2c's own motivating case: a bullet naming
    several employers with NO relational marker (a genuine multi-role fold,
    not a client/acquisition mention) is still caught."""
    ops = [AddBullets(target=_NOVA_ID, achievements=[_FOLDED_BULLET])]
    out = enforce_attribution(
        ops, profile=_three_station_profile(), new_info=_turn(), source="interview"
    )
    assert any(isinstance(op, RequestConfirmation) for op in out)


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
        # adversarial pass 2026-09-10 — a question back to the interviewer is
        # not testimony; the reconciler correctly writes nothing for it.
        "What exactly do you mean by production experience in this context?",
        "Was genau meinst du mit Produktionserfahrung in diesem Zusammenhang?",
    ],
)
def test_an_answer_that_states_nothing_produces_no_receipt(answer):
    assert compute_no_write(answer) == []


def test_a_question_back_is_not_read_as_a_statement():
    """MUTATION KILL: delete the `clause.endswith("?")` guard in
    `positive_residue` and this fails — the candidate's clarifying question
    would read as "nothing was recorded from what you said", which is false:
    a question was never testimony to begin with."""
    answer = "What exactly do you mean by production experience in this context?"
    assert positive_residue(answer) == []
    assert compute_no_write(answer) == []


def test_a_question_does_not_swallow_a_real_answer_that_follows_it():
    """A rhetorical question ahead of the real statement must not eat it —
    over-drop discipline applies to the new guard too."""
    answer = "Have I worked with Kubernetes? Yes, for three years at my last job."
    residue = positive_residue(answer)
    assert residue == ["Yes, for three years at my last job."]


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
