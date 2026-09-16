# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#707 — the reconciler can SAY "already there" (ADR-046 amended 2026-09-16) and
the import witness reads it (ADR-063 amended 2026-09-16, arm (c) sub-clause 3).

A DE-then-EN two-CV import listed nearly every entry of the second CV as
``no_op_carried_entry`` while the vault held every one of them in German. The
model had matched the translations and — correctly — emitted no op; silence was
its only channel for "already present" and the witness cannot tell silence from
dropping. These tests pin the channel: the ``match_existing`` op, the receipt it
leaves on ``matched`` (never on ``changes``), the witness's target-first binding,
and the turn doors' ``no_write_already_known`` outcome.

Mutation kills recorded in the PR body: drop sub-clause 3 from
``_flat_section_not_applied`` and the two witness tests go red; put the receipt
on ``changes`` and ``test_a_match_is_not_a_change`` goes red; drop the
``already_known`` mapping in ``apply_ops`` and the no-write test goes red.
"""
from applire.schemas.profile import (
    EducationEntry,
    Language,
    MasterProfileData,
    MatchReceipt,
    Skill,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import _parse_ops
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.services.profile.reconcile.ops import (
    ApplyImportMerge,
    MatchExisting,
    _MODEL_EMITTABLE,
)
from applire.services.profile.reconciliation import compute_merge_reconciliation

_ANSWER = (
    "I speak English at a professional working level and use it daily with our "
    "customers in Frankfurt."
)


def _vault_de() -> MasterProfileData:
    return MasterProfileData(
        languages=[
            Language(id="lang-en", language="Englisch", level="professional"),
            Language(id="lang-de", language="Deutsch", level="native"),
        ],
        skills=[Skill(id="sk-ml", name="Maschinelles Lernen")],
    )


def _incoming_en() -> MasterProfileData:
    return MasterProfileData(
        languages=[Language(language="English"), Language(language="German")],
        skills=[Skill(name="Machine Learning")],
    )


# ── the op ───────────────────────────────────────────────────────────────────


def test_match_existing_is_model_emittable_and_parses():
    assert MatchExisting in _MODEL_EMITTABLE
    ops = _parse_ops([{"op": "match_existing", "target": "lang-en", "incoming": "English"}])
    assert len(ops) == 1 and isinstance(ops[0], MatchExisting)
    assert ops[0].target == "lang-en" and ops[0].incoming == "English"


def test_match_existing_requires_both_fields():
    rejected: list[str] = []
    ops = _parse_ops([{"op": "match_existing", "target": "lang-en"}], rejected=rejected)
    assert ops == [] and rejected == ["match_existing"]


# ── the applier: a receipt on `matched`, nothing on `changes`, no mutation ───


def test_a_match_leaves_a_receipt_and_mutates_nothing():
    vault = _vault_de()
    result = apply_ops(vault, [MatchExisting(target="lang-en", incoming="English")], "cv_upload")
    assert result.matched == [
        MatchReceipt(section="languages", entity_id="lang-en", incoming="English", existing="Englisch")
    ]
    assert result.profile.model_dump(mode="json") == vault.model_dump(mode="json")


def test_a_match_is_not_a_change():
    """Five readers take `bool(changes)` as "the vault changed" — the interview
    bridge's `addressed`, the agent bridge's ledger upgrade, MCP `resolve_gap`'s
    status, `migrate`. A restatement must not close a gap (refuter BLOCKER,
    2026-09-16). MUTATION KILL: record the receipt as a FieldChange and this fails."""
    result = apply_ops(_vault_de(), [MatchExisting(target="lang-en", incoming="English")], "interview")
    assert result.changes == []
    assert result.conflicts == [] and result.pending_confirmations == []


def test_an_unresolvable_target_records_nothing():
    result = apply_ops(_vault_de(), [MatchExisting(target="ghost", incoming="English")], "cv_upload")
    assert result.matched == [] and result.changes == []


def test_the_receipt_names_the_existing_entry_by_its_natural_key():
    vault = MasterProfileData(
        education=[EducationEntry(id="edu-1", institution="TU München", degree="M.Sc.")]
    )
    result = apply_ops(vault, [MatchExisting(target="edu-1", incoming="TU Munich / M.Sc.")], "cv_upload")
    assert result.matched[0].section == "education"
    assert result.matched[0].existing == "TU München / M.Sc."


def test_the_import_act_carries_the_receipts_through():
    receipts = [MatchReceipt(section="skills", entity_id="sk-ml", incoming="Machine Learning", existing="Maschinelles Lernen")]
    result = apply_ops(
        MasterProfileData(),
        [ApplyImportMerge(merged=_vault_de(), changes=[], matched=receipts)],
        "cv_upload",
    )
    assert result.matched == receipts


# ── the turn doors: a match-only turn is "already known", not a lost turn ───


def test_a_match_only_turn_ends_as_already_known_not_as_no_write():
    """Before: the fail-safe `no_write` copy ("nothing was recorded — say it
    again"). MUTATION KILL: drop the `already_known` mapping in `apply_ops`'
    no-write gate and this fails."""
    result = apply_ops(
        _vault_de(),
        [MatchExisting(target="lang-en", incoming="English")],
        "interview",
        turn_text=_ANSWER,
    )
    assert [i.reason for i in result.not_applied] == ["no_write_already_known"]
    assert result.changes == []  # and therefore `addressed` stays False upstream


def test_a_turn_that_matched_and_wrote_leaves_no_no_write_receipt():
    from applire.services.profile.reconcile.ops import UpsertSkill

    result = apply_ops(
        _vault_de(),
        [MatchExisting(target="lang-en", incoming="English"), UpsertSkill(name="Kubernetes")],
        "interview",
        turn_text=_ANSWER,
    )
    assert not [i for i in result.not_applied if i.reason.startswith("no_write")]
    assert result.matched and result.changes


# ── the witness: arm (c) sub-clause 3, target-first ──────────────────────────


def test_the_707_shape_is_carried_when_the_model_binds_the_translations():
    """The incident, in miniature: `English`/`German`/`Machine Learning` incoming,
    `Englisch`/`Deutsch`/`Maschinelles Lernen` held — `classify_dupe` clears none
    of the pairs. MUTATION KILL: remove sub-clause 3 and all three come back."""
    incoming, merged = _incoming_en(), _vault_de()
    ops = [
        MatchExisting(target="lang-en", incoming="English"),
        MatchExisting(target="lang-de", incoming="German"),
        MatchExisting(target="sk-ml", incoming="Machine Learning"),
    ]
    assert compute_import_not_applied(incoming, merged, ops) == []
    counts = compute_merge_reconciliation(incoming, merged, [])
    assert counts["languages"] == {"extracted": 2, "stored": 2, "delta": 0}
    assert counts["skills"]["delta"] == 0


def test_without_the_binding_the_707_shape_is_listed():
    """The control group — the same merge with silent ops is what #707 saw."""
    items = compute_import_not_applied(_incoming_en(), _vault_de(), ops=[])
    assert sorted(i.label for i in items) == ["English", "German", "Machine Learning"]
    assert {i.reason for i in items} == {"no_op_carried_entry"}


def test_target_first_disambiguation_carries_exactly_the_targeted_entry():
    """Two incoming entries share the translated degree; the vault holds the TU
    one under the German abbreviation (no arm (a)/(b) match — `Dipl.-Inf.` vs
    `Diploma in Computer Science`); the model binds `incoming="Diploma in
    Computer Science"` to the TU id. A string-uniqueness rule alone would rescue
    neither (refuter MAJOR, 2026-09-16) — the target's own institution settles
    it. MUTATION KILL: disable the `len(candidates) > 1` branch and the TU entry
    comes back."""
    incoming = MasterProfileData(
        education=[
            EducationEntry(institution="TU München", degree="Diploma in Computer Science"),
            EducationEntry(institution="LMU München", degree="Diploma in Computer Science"),
        ]
    )
    merged = MasterProfileData(
        education=[EducationEntry(id="edu-tu", institution="TU München", degree="Dipl.-Inf.")]
    )
    baseline = compute_import_not_applied(incoming, merged, [])
    assert len(baseline) == 2  # neither is carried by arms (a)/(b) on its own
    items = compute_import_not_applied(
        incoming, merged, [MatchExisting(target="edu-tu", incoming="Diploma in Computer Science")]
    )
    assert [i.label for i in items] == ["LMU München / Diploma in Computer Science"]


def test_a_binding_that_fits_two_entries_and_agrees_with_neither_rescues_none():
    incoming = MasterProfileData(
        education=[
            EducationEntry(institution="TU München", degree="M.Sc."),
            EducationEntry(institution="LMU München", degree="M.Sc."),
        ]
    )
    merged = MasterProfileData(
        education=[EducationEntry(id="edu-fh", institution="FH Köln", degree="M.Sc.")]
    )
    items = compute_import_not_applied(
        incoming, merged, [MatchExisting(target="edu-fh", incoming="M.Sc.")]
    )
    assert sorted(i.label for i in items) == ["LMU München / M.Sc.", "TU München / M.Sc."]


def test_a_target_in_another_section_rescues_nothing():
    """A language bound to a SKILL id is a mis-binding; the language stays listed."""
    incoming = MasterProfileData(languages=[Language(language="English")])
    merged = MasterProfileData(
        skills=[Skill(id="sk-en", name="Englisch")],
        languages=[Language(id="lang-en", language="Englisch")],
    )
    items = compute_import_not_applied(
        incoming, merged, [MatchExisting(target="sk-en", incoming="English")]
    )
    assert [i.label for i in items] == ["English"]


def test_an_unresolvable_target_rescues_nothing_at_the_witness():
    items = compute_import_not_applied(
        MasterProfileData(languages=[Language(language="English")]),
        MasterProfileData(languages=[Language(id="lang-en", language="Englisch")]),
        [MatchExisting(target="ghost", incoming="English")],
    )
    assert [i.label for i in items] == ["English"]


def test_binding_by_the_formatted_label_works_for_two_field_keys():
    incoming = MasterProfileData(
        education=[EducationEntry(institution="TU München", degree="Master of Science")]
    )
    merged = MasterProfileData(
        education=[EducationEntry(id="edu-tu", institution="TU München", degree="M.Sc.")]
    )
    items = compute_import_not_applied(
        incoming, merged, [MatchExisting(target="edu-tu", incoming="TU München / Master of Science")]
    )
    assert items == []
