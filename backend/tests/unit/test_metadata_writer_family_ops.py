# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#480 PR 7 / ADR-063 amended 2026-08-09 (third entry) — the metadata-writer
family: `SetProfileMeta`, `MarkProbeAsked`, `EscalateDenialLevel`.

Three ops, one shared question: **how much of `metadata` does an op get to
reach?** The design's answer has been the same since §4.4 — as little as the act
needs, expressed as a type rather than as a convention:

* `SetProfileMeta` cannot reach `metadata` AT ALL. Its `key` is
  `Literal["na_fields"]`, which addresses the `_meta` sidecar (#505), not the
  `metadata` block that holds `denied_concepts` and `enrichment_history`. A
  free-form metadata-path op could release a denial or forge its own audit
  trail; the enum is what makes that unsayable.
* `MarkProbeAsked` reaches ONE boolean on an ALREADY EXISTING `DeniedConcept`.
  It cannot create one, delete one, or move its level.
* `EscalateDenialLevel` reaches ONE monotonic transition, `direct → partial`,
  plus its `date` stamp. Not the reverse, not `statement`, not `source`.

The invariant those three add up to, and which this file pins as one statement:
**no op can release, mint or downgrade a denial, and no op reaches
`enrichment_history`.** `denied_concepts` is reachable by exactly two ops (the
flag and the monotonic upgrade) plus the committer's own `record_denials`
invariant path — and nothing else in the vocabulary.

Also pinned here: the **user-confirmed skill capability** (ruling 5). The
interview's resolved skill-dedupe confirmation needs `_apply_upsert_skill`'s
stateless containment guard BYPASSED — the candidate has already answered the
question the guard would ask, so re-running it re-asks forever (#187). That
capability travels on the commit/apply CALL PATH, never as a field on the
model-emittable `UpsertSkill` schema: widening an op the model emits with a more
powerful parameter is the one thing ADR-063 clause 1's governing rule forbids.
"""
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import UserConfirmedSkill, apply_ops
from applire.services.profile.reconcile.ops import (
    CommitOp,
    DecisionOp,
    EscalateDenialLevel,
    MarkProbeAsked,
    ReconcileOp,
    SetProfileMeta,
    UpsertSkill,
)

_HALLUCINATED_SET_META: dict[str, Any] = {
    "op": "set_profile_meta",
    "key": "na_fields",
    "value": "personal_info.phone",
}
_HALLUCINATED_MARK_PROBE: dict[str, Any] = {
    "op": "mark_probe_asked",
    "concept": "TOGAF",
}
_HALLUCINATED_ESCALATE: dict[str, Any] = {
    "op": "escalate_denial_level",
    "concept": "TOGAF",
}


def _profile_with_denial(**overrides) -> MasterProfileData:
    """A vault holding ONE durable denial, at `direct`, unprobed."""
    denial: dict[str, Any] = {
        "concept": "GCP-Zertifizierung",
        "statement": "Eine GCP-Zertifizierung habe ich nicht.",
        "source": "interview",
        "date": "2020-01-01",
        "denial_level": "direct",
        "probe_asked": False,
    }
    denial.update(overrides)
    return MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Daniel Kovač"},
            "skills": [
                {"name": "Terraform", "category": "technical", "status": "confirmed"}
            ],
            "metadata": {"denied_concepts": [denial], "enrichment_history": []},
        }
    )


def _denial(profile: MasterProfileData):
    assert profile.metadata is not None
    return profile.metadata.denied_concepts[0]


# ── The union split — all three are adapter-only ──────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [_HALLUCINATED_SET_META, _HALLUCINATED_MARK_PROBE, _HALLUCINATED_ESCALATE],
    ids=["set_profile_meta", "mark_probe_asked", "escalate_denial_level"],
)
def test_reconcile_op_union_refuses_every_metadata_family_op(payload):
    """None of the three may be in the model's own vocabulary. Two of them write
    the denial record — the vault's record of what the candidate ruled out — and
    the third suppresses a completeness gap the candidate said does not apply.
    All three are statements the SYSTEM or the CANDIDATE makes, never the
    reconciler."""
    adapter: TypeAdapter = TypeAdapter(ReconcileOp)
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_HALLUCINATED_SET_META, SetProfileMeta),
        (_HALLUCINATED_MARK_PROBE, MarkProbeAsked),
        (_HALLUCINATED_ESCALATE, EscalateDenialLevel),
    ],
    ids=["set_profile_meta", "mark_probe_asked", "escalate_denial_level"],
)
def test_decision_and_commit_unions_accept_every_metadata_family_op(payload, expected):
    assert isinstance(TypeAdapter(DecisionOp).validate_python(payload), expected)
    assert isinstance(TypeAdapter(CommitOp).validate_python(payload), expected)


@pytest.mark.parametrize(
    "payload",
    [_HALLUCINATED_SET_META, _HALLUCINATED_MARK_PROBE, _HALLUCINATED_ESCALATE],
    ids=["set_profile_meta", "mark_probe_asked", "escalate_denial_level"],
)
def test_hallucinated_metadata_family_op_in_model_output_is_dropped(payload):
    """The parse seam, where raw model JSON becomes ops (the M1b pin)."""
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [payload, {"op": "upsert_skill", "name": "Go", "category": "technical"}]
    )

    assert [type(o) for o in ops] == [UpsertSkill]


# ── `SetProfileMeta` — the key enum IS the guard ──────────────────────────────


@pytest.mark.parametrize(
    "key",
    ["denied_concepts", "enrichment_history", "metadata", "pending_confirmations", ""],
)
def test_set_profile_meta_refuses_every_key_but_na_fields(key):
    """The load-bearing guard (design §4.4). A free-form metadata-path op could
    release a denial or forge its own audit trail; a one-member enum cannot
    express either, and the refusal happens at construction — before anything
    reaches the applier."""
    with pytest.raises(ValidationError):
        SetProfileMeta(key=key, value="x")  # type: ignore[arg-type]


def test_set_profile_meta_refuses_a_removal_mode():
    """`mode` is a second one-member enum. Un-suppressing a gap the candidate
    marked N/A is a real act, but it is not THIS one, and an op that can both
    add and remove is an op whose reach has to be argued at every call site."""
    with pytest.raises(ValidationError):
        SetProfileMeta(key="na_fields", value="x", mode="remove")  # type: ignore[arg-type]


def test_set_profile_meta_appends_the_suppression_under_the_meta_sidecar():
    profile = _profile_with_denial()
    applied = apply_ops(
        profile, [SetProfileMeta(key="na_fields", value="personal_info.phone")], "manual_edit"
    )

    assert applied.profile.meta is not None
    assert applied.profile.meta.na_fields == ["personal_info.phone"]
    # #505 — it has to SERIALIZE under `_meta`, which is the key the readers
    # (`completeness.field_gaps`, `health`) index by off the raw JSON.
    assert applied.profile.model_dump(mode="json")["_meta"]["na_fields"] == [
        "personal_info.phone"
    ]


def test_set_profile_meta_creates_the_meta_block_when_the_profile_has_none():
    """A vault that never had a `_meta` key must grow one on the first N/A —
    and only then (the #505 serializer omits an absent block entirely)."""
    profile = _profile_with_denial()
    assert profile.meta is None

    applied = apply_ops(
        profile, [SetProfileMeta(key="na_fields", value="education")], "manual_edit"
    )

    assert applied.profile.meta is not None
    assert applied.profile.meta.na_fields == ["education"]


def test_set_profile_meta_is_idempotent_append_if_absent():
    """Marking the same gap N/A twice must not litter the list — nor the trail:
    the second act produces no receipt at all."""
    profile = _profile_with_denial()
    once = apply_ops(
        profile, [SetProfileMeta(key="na_fields", value="languages")], "manual_edit"
    )
    twice = apply_ops(
        once.profile, [SetProfileMeta(key="na_fields", value="languages")], "manual_edit"
    )

    assert twice.profile.meta is not None
    assert twice.profile.meta.na_fields == ["languages"]
    assert once.changes and twice.changes == []


def test_set_profile_meta_preserves_unknown_meta_keys():
    """`ProfileMetaBlock` is `extra="allow"` on purpose (#505). A write to one
    key may not drop a sibling it does not know about."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Daniel Kovač"},
            "_meta": {"na_fields": ["languages"], "future_key": {"a": 1}},
        }
    )

    applied = apply_ops(
        profile, [SetProfileMeta(key="na_fields", value="education")], "manual_edit"
    )

    dumped = applied.profile.model_dump(mode="json")["_meta"]
    assert dumped["na_fields"] == ["languages", "education"]
    assert dumped["future_key"] == {"a": 1}


def test_set_profile_meta_never_touches_the_metadata_block():
    """`_meta` and `metadata` are one letter apart (#509) and hold completely
    different things. The op that writes the sidecar must leave the denial
    record and the audit trail exactly as it found them."""
    profile = _profile_with_denial()

    applied = apply_ops(
        profile, [SetProfileMeta(key="na_fields", value="languages")], "manual_edit"
    )

    assert applied.profile.metadata is not None
    assert applied.profile.metadata.enrichment_history == []
    after = _denial(applied.profile)
    assert after.concept == "GCP-Zertifizierung"
    assert after.denial_level == "direct"
    assert after.probe_asked is False
    assert after.statement == "Eine GCP-Zertifizierung habe ich nicht."


def test_set_profile_meta_receipt_is_gap_addressing_content():
    """Unlike the two bookkeeping ops, marking a gap N/A is the CANDIDATE
    resolving that gap — it belongs on `changes`, the list the "what changed"
    surface renders."""
    applied = apply_ops(
        _profile_with_denial(),
        [SetProfileMeta(key="na_fields", value="languages")],
        "manual_edit",
    )

    assert [(c.section, c.field, c.action) for c in applied.changes] == [
        ("_meta", "na_fields", "added")
    ]
    assert applied.changes[0].new_value == "languages"
    assert applied.denials == []


def test_set_profile_meta_ignores_a_blank_value():
    """A blank suppression would permanently exclude nothing while looking like
    a real act on the trail."""
    applied = apply_ops(
        _profile_with_denial(), [SetProfileMeta(key="na_fields", value="   ")], "manual_edit"
    )

    assert applied.profile.meta is None
    assert applied.changes == []


# ── `MarkProbeAsked` — one boolean, on an EXISTING record only ────────────────


def test_mark_probe_asked_requires_a_concept():
    with pytest.raises(ValidationError):
        MarkProbeAsked()  # type: ignore[call-arg]


def test_mark_probe_asked_sets_the_flag_on_the_existing_denial():
    applied = apply_ops(
        _profile_with_denial(), [MarkProbeAsked(concept="GCP-Zertifizierung")], "interview"
    )

    assert _denial(applied.profile).probe_asked is True


def test_mark_probe_asked_addresses_by_the_ats_audit_normaliser():
    """The concept text reaching this op and the text on the durable record come
    from independently generated LLM output, so byte-identical spelling cannot
    be assumed. `ats_audit._norm` folds case AND hyphens — the same normaliser
    `record_denials` dedupes with, so the two can never disagree about identity.
    """
    applied = apply_ops(
        _profile_with_denial(),
        [MarkProbeAsked(concept="  gcp zertifizierung ")],
        "interview",
    )

    assert _denial(applied.profile).probe_asked is True


def test_mark_probe_asked_never_mints_a_denial_it_cannot_find():
    """The fail-safe half of the ADR-064 M4 agreement, preserved verbatim
    through the routing: inventing a `DeniedConcept` from bookkeeping alone
    would durably attribute testimony the candidate never gave. An unrecorded
    "we asked" is the cheaper failure."""
    applied = apply_ops(
        _profile_with_denial(), [MarkProbeAsked(concept="Kubernetes")], "interview"
    )

    assert applied.profile.metadata is not None
    assert [d.concept for d in applied.profile.metadata.denied_concepts] == [
        "GCP-Zertifizierung"
    ]
    assert applied.changes == [] and applied.denials == []


def test_mark_probe_asked_on_a_profile_without_metadata_is_a_no_op():
    profile = MasterProfileData.model_validate({"personal_info": {"full_name": "D"}})

    applied = apply_ops(profile, [MarkProbeAsked(concept="Kubernetes")], "interview")

    assert applied.changes == [] and applied.denials == []


def test_mark_probe_asked_touches_nothing_but_the_flag():
    """Not the level, not the testimony, not the date, not the record count —
    the flag is elicitation bookkeeping ("we asked"), never testimony ("they
    denied")."""
    before = _profile_with_denial()
    applied = apply_ops(
        before, [MarkProbeAsked(concept="GCP-Zertifizierung")], "interview"
    )

    after = _denial(applied.profile)
    assert after.denial_level == "direct"
    assert after.statement == "Eine GCP-Zertifizierung habe ich nicht."
    assert after.source == "interview"
    assert after.date == "2020-01-01"
    assert applied.profile.metadata is not None
    assert len(applied.profile.metadata.denied_concepts) == 1


def test_mark_probe_asked_is_idempotent():
    applied = apply_ops(
        _profile_with_denial(probe_asked=True),
        [MarkProbeAsked(concept="GCP-Zertifizierung")],
        "interview",
    )

    assert _denial(applied.profile).probe_asked is True
    assert applied.denials == [] and applied.changes == []


def test_mark_probe_asked_receipts_onto_denials_never_changes():
    """Receipt separation (committer invariant 7), extended to bookkeeping: a
    probe being ISSUED is not gap-addressing content, and `changes` is the only
    list an `addressed`/ledger-upgrade gate may read."""
    applied = apply_ops(
        _profile_with_denial(), [MarkProbeAsked(concept="GCP-Zertifizierung")], "interview"
    )

    assert applied.changes == []
    assert [(c.section, c.field, c.action) for c in applied.denials] == [
        ("metadata", "denied_concepts", "updated")
    ]


# ── `EscalateDenialLevel` — one monotonic transition ──────────────────────────


def test_escalate_denial_level_requires_a_concept():
    with pytest.raises(ValidationError):
        EscalateDenialLevel()  # type: ignore[call-arg]


def test_escalate_denial_level_has_no_level_parameter_at_all():
    """The monotonic rule is not a validated argument — it is the ABSENCE of
    one. There is no spelling of this op that requests `partial → direct`."""
    assert set(EscalateDenialLevel.model_fields) == {"op", "concept"}


def test_escalate_denial_level_moves_direct_to_partial_and_restamps_the_date():
    applied = apply_ops(
        _profile_with_denial(), [EscalateDenialLevel(concept="GCP-Zertifizierung")], "interview"
    )

    after = _denial(applied.profile)
    assert after.denial_level == "partial"
    assert after.date != "2020-01-01"


def test_escalate_denial_level_is_a_no_op_when_already_partial():
    applied = apply_ops(
        _profile_with_denial(denial_level="partial"),
        [EscalateDenialLevel(concept="GCP-Zertifizierung")],
        "interview",
    )

    after = _denial(applied.profile)
    assert after.denial_level == "partial"
    assert after.date == "2020-01-01"
    assert applied.denials == [] and applied.changes == []


def test_escalate_denial_level_never_mints_a_missing_concept():
    """Same fail-safe as the probe flag: there is nothing to escalate FROM."""
    applied = apply_ops(
        _profile_with_denial(), [EscalateDenialLevel(concept="Kubernetes")], "interview"
    )

    assert applied.profile.metadata is not None
    assert [d.concept for d in applied.profile.metadata.denied_concepts] == [
        "GCP-Zertifizierung"
    ]
    assert applied.denials == [] and applied.changes == []


def test_escalate_denial_level_never_rewrites_the_write_once_testimony():
    """#348 — `statement`/`source` are the candidate's verbatim words. The level
    is bookkeeping; escalating it may not touch them."""
    applied = apply_ops(
        _profile_with_denial(), [EscalateDenialLevel(concept="GCP-Zertifizierung")], "interview"
    )

    after = _denial(applied.profile)
    assert after.statement == "Eine GCP-Zertifizierung habe ich nicht."
    assert after.source == "interview"


def test_escalate_denial_level_receipts_onto_denials_never_changes():
    """Ruling 3, #231's rule extended to the committer: an escalation is the
    candidate ruling MORE out. It must never read as "gap addressed"."""
    applied = apply_ops(
        _profile_with_denial(), [EscalateDenialLevel(concept="GCP-Zertifizierung")], "interview"
    )

    assert applied.changes == []
    assert [(c.section, c.field, c.action) for c in applied.denials] == [
        ("metadata", "denied_concepts", "updated")
    ]


def test_escalate_denial_level_addresses_by_the_ats_audit_normaliser():
    applied = apply_ops(
        _profile_with_denial(),
        [EscalateDenialLevel(concept="gcp zertifizierung")],
        "interview",
    )

    assert _denial(applied.profile).denial_level == "partial"


# ── The invariant, restated over the whole new family ─────────────────────────


def test_no_metadata_family_op_can_release_or_delete_a_denial():
    """The one statement the three ops add up to. Applied together, twice, a
    denial the candidate gave still stands: same concept, same testimony, never
    fewer records, never a level moving back."""
    profile = _profile_with_denial()
    ops = [
        SetProfileMeta(key="na_fields", value="languages"),
        MarkProbeAsked(concept="GCP-Zertifizierung"),
        EscalateDenialLevel(concept="GCP-Zertifizierung"),
    ]

    once = apply_ops(profile, list(ops), "interview")
    twice = apply_ops(once.profile, list(ops), "interview")

    after = _denial(twice.profile)
    assert twice.profile.metadata is not None
    assert len(twice.profile.metadata.denied_concepts) == 1
    assert after.concept == "GCP-Zertifizierung"
    assert after.statement == "Eine GCP-Zertifizierung habe ich nicht."
    assert after.denial_level == "partial"  # never back to "direct"
    assert after.probe_asked is True


def test_no_metadata_family_op_reaches_enrichment_history():
    """The trail is the committer's (invariant 3). An op that could append to it
    could forge its own audit record — which is the other half of why
    `SetProfileMeta` carries an enum instead of a path."""
    applied = apply_ops(
        _profile_with_denial(),
        [
            SetProfileMeta(key="na_fields", value="languages"),
            MarkProbeAsked(concept="GCP-Zertifizierung"),
            EscalateDenialLevel(concept="GCP-Zertifizierung"),
        ],
        "interview",
    )

    assert applied.profile.metadata is not None
    assert applied.profile.metadata.enrichment_history == []


def test_apply_ops_never_mutates_the_profile_it_was_handed():
    before = _profile_with_denial()

    apply_ops(
        before,
        [
            SetProfileMeta(key="na_fields", value="languages"),
            MarkProbeAsked(concept="GCP-Zertifizierung"),
            EscalateDenialLevel(concept="GCP-Zertifizierung"),
        ],
        "interview",
    )

    assert before.meta is None
    assert _denial(before).probe_asked is False
    assert _denial(before).denial_level == "direct"


# ── The user-confirmed skill capability (ruling 5) ────────────────────────────


def _profile_with_skill(name: str) -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Daniel Kovač"},
            "skills": [{"name": name, "category": "technical", "status": "confirmed"}],
            "metadata": {},
        }
    )


def test_upsert_skill_schema_carries_no_user_confirmed_field():
    """The governing rule (ADR-063 clause 1): never widen an op the MODEL can
    emit with a more powerful parameter. The bypass is not spellable in the
    schema, so it is not spellable in model output either."""
    assert "user_confirmed" not in UpsertSkill.model_fields
    assert "decision" not in UpsertSkill.model_fields


def test_a_hallucinated_user_confirmed_field_is_dropped_and_the_guard_still_fires():
    """Model output claiming the candidate already answered: the extra key is
    ignored by the schema, and the containment guard raises its question as it
    always would."""
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [
            {
                "op": "upsert_skill",
                "name": "React Native",
                "category": "technical",
                "user_confirmed": "merge",
            }
        ]
    )

    assert len(ops) == 1 and not hasattr(ops[0], "user_confirmed")
    applied = apply_ops(_profile_with_skill("React"), list(ops), "interview")
    assert applied.pending_confirmations  # the guard asked, exactly as before
    assert [s.name for s in applied.profile.skills] == ["React"]


def test_user_confirmed_merge_bypasses_the_containment_guard():
    """#187 — the candidate has ANSWERED. Re-running the stateless guard would
    surface the identical question forever."""
    applied = apply_ops(
        _profile_with_skill("React"),
        [UpsertSkill(name="React Native", category="technical")],
        "interview",
        user_confirmed_skill=UserConfirmedSkill(name="React Native", decision="merge"),
    )

    assert applied.pending_confirmations == []
    assert [s.name for s in applied.profile.skills] == ["React Native"]


def test_user_confirmed_distinct_bypasses_the_containment_guard():
    applied = apply_ops(
        _profile_with_skill("React"),
        [UpsertSkill(name="React Native", category="technical")],
        "interview",
        user_confirmed_skill=UserConfirmedSkill(name="React Native", decision="distinct"),
    )

    assert applied.pending_confirmations == []
    assert sorted(s.name for s in applied.profile.skills) == ["React", "React Native"]


def test_the_capability_is_keyed_to_the_skill_the_candidate_answered_about():
    """A batch-wide bypass would let ONE answered confirmation wave every other
    skill in the batch past the guard. The capability names its skill, and every
    other `UpsertSkill` in the same batch is adjudicated normally."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Daniel Kovač"},
            "skills": [
                {"name": "React", "category": "technical", "status": "confirmed"},
                {"name": "Docker", "category": "technical", "status": "confirmed"},
            ],
            "metadata": {},
        }
    )

    applied = apply_ops(
        profile,
        [
            UpsertSkill(name="React Native", category="technical"),
            UpsertSkill(name="Docker Compose", category="technical"),
        ],
        "interview",
        user_confirmed_skill=UserConfirmedSkill(name="React Native", decision="distinct"),
    )

    # The answered one landed; the unanswered one asked.
    assert "React Native" in [s.name for s in applied.profile.skills]
    assert "Docker Compose" not in [s.name for s in applied.profile.skills]
    assert [
        c.context.get("incoming_skill") for c in applied.pending_confirmations
    ] == ["Docker Compose"]


def test_the_capability_defaults_to_absent_so_no_other_intake_changes():
    """Every existing caller passes nothing, and the guard behaves exactly as it
    did before the parameter existed."""
    applied = apply_ops(
        _profile_with_skill("React"),
        [UpsertSkill(name="React Native", category="technical")],
        "interview",
    )

    assert applied.pending_confirmations
    assert [s.name for s in applied.profile.skills] == ["React"]
