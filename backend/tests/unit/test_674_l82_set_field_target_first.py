# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#674 line 82 — a translated flat entry the model correlated through a
`set_field` is CARRIED, not listed as not applied.

The FALSE not-carried callout the founder saw on both of his 2026-09-18 edge
imports (`ec2e6af5`, luna, DE LinkedIn export onto an EN profile): the
reconciler emitted `set_field(target=<existing id>, …)`, the applier wrote it —
`education|updated|field`, `languages|updated|level` in the SAME receipt — and
the witness still booked the incoming twin as `no_op_carried_entry`, because
arm (c) sub-clause 2 bound only by natural-key EQUALITY after `_norm`. The
receipt contradicted itself, `merge_status` went `partial`, the Health hub said
items "did not reach your profile", and the agent door was obliged to report a
loss that had not happened.

Synthetic twins only: the SHAPE is a DE export whose institution differs by a
city suffix and whose degree differs by translation, and a languages pair whose
names differ by translation while the written level is the incoming one.
"""
from __future__ import annotations

from applire.schemas.profile import (
    EducationEntry,
    Language,
    MasterProfileData,
)
from applire.services.profile.reconcile.import_witness import (
    compute_import_not_applied,
)
from applire.services.profile.reconcile.ops import SetField

JMU_ID = "e-jmu"
PROVADIS_ID = "e-provadis"
EN_LANG_ID = "l-english"


def _merged_education() -> MasterProfileData:
    """The EN vault as it stands AFTER the merge wrote the set_fields."""
    return MasterProfileData(
        education=[
            # Institution differs by a city suffix, degree by the TRANSLATION —
            # the real shape, and the one no natural-key field can match. The
            # `set_field(degree=…)` the model emitted for it wrote nothing
            # (`_apply_set_field` refuses a populated slot), which is why the
            # binder keys on the OP and not on the receipt's `changes`: on the
            # real record this entry produced no change at all.
            EducationEntry(id=JMU_ID, institution="JMU Würzburg",
                           degree="Diploma (equivalent to M.Sc.)",
                           field="Biologie, allgemein", start_date="1998", end_date="2004"),
            EducationEntry(id=PROVADIS_ID, institution="Provadis Hochschule",
                           degree="Fachinformatiker", field="Anwendungsentwicklung"),
        ]
    )


def _incoming_education() -> MasterProfileData:
    """The DE LinkedIn export: same two entries, translated / suffixed names."""
    return MasterProfileData(
        education=[
            EducationEntry(institution="Julius-Maximilians-Universität Würzburg",
                           degree="Diplom", field="Biologie, allgemein"),
            EducationEntry(institution="Provadis Hochschule für Wirtschaft",
                           degree="Fachinformatiker", field="Anwendungsentwicklung"),
        ]
    )


def _education_ops() -> list:
    return [
        SetField(target=JMU_ID, field="degree", value="Diplom"),
        SetField(target=PROVADIS_ID, field="field", value="Anwendungsentwicklung"),
    ]


def test_a_set_field_against_an_existing_id_carries_its_translated_twin():
    items = compute_import_not_applied(
        _incoming_education(), _merged_education(), _education_ops()
    )
    assert [i.label for i in items] == [], (
        "a receipt that books a change on an entry may not list that entry's "
        "own twin as not carried"
    )


def test_the_same_pair_without_the_binder_is_the_defect_it_fixes():
    """The mutation this test exists to kill, stated as data: strip the ops and
    the JMU entry lands `no_op_carried_entry` — so the BINDER, not the fixture's
    similarity, is what carries it.

    The Provadis twin is deliberately NOT part of this assertion: arm (b)'s
    `classify_education_dupe` already folds "Provadis Hochschule für Wirtschaft"
    onto "Provadis Hochschule", so it would be carried either way. Only the
    entry no other arm reaches proves the new one fires (the guard-mutation
    rule: build the test on a shape ONLY the guard rescues)."""
    items = compute_import_not_applied(
        _incoming_education(), _merged_education(), []
    )
    assert [i.label for i in items] == [
        "Julius-Maximilians-Universität Würzburg / Diplom"
    ]
    assert items[0].reason == "no_op_carried_entry"


def test_an_incoming_entry_no_op_touched_is_still_not_applied():
    """The negative control. A third DE entry nothing correlates keeps its
    `no_op_carried_entry` item — the binder may not blanket-rescue a section."""
    incoming = _incoming_education()
    incoming.education.append(
        EducationEntry(institution="Fernuniversität Hagen", degree="Zertifikat",
                       field="Wirtschaftsinformatik")
    )
    items = compute_import_not_applied(
        incoming, _merged_education(), _education_ops()
    )
    assert [i.label for i in items] == ["Fernuniversität Hagen / Zertifikat"]
    assert items[0].reason == "no_op_carried_entry"


def test_a_set_field_targeting_another_section_rescues_nothing():
    items = compute_import_not_applied(
        _incoming_education(),
        _merged_education(),
        [SetField(target=EN_LANG_ID, field="degree", value="Diplom")],
    )
    assert [i.label for i in items] == [
        "Julius-Maximilians-Universität Würzburg / Diplom"
    ]


def test_a_set_field_whose_target_resolves_to_nothing_rescues_nothing():
    items = compute_import_not_applied(
        _incoming_education(),
        _merged_education(),
        [SetField(target="no-such-id", field="degree", value="Diplom")],
    )
    assert [i.label for i in items] == [
        "Julius-Maximilians-Universität Würzburg / Diplom"
    ]


def test_an_ambiguous_written_value_rescues_nothing():
    """Sub-clause 3's cardinality discipline, borrowed verbatim: two incoming
    entries carry the same value for the written field and neither shares a
    further natural-key field with the target, so neither is bound."""
    incoming = MasterProfileData(
        education=[
            EducationEntry(institution="Hochschule A", degree="Diplom", field="Informatik"),
            EducationEntry(institution="Hochschule B", degree="Magister", field="Informatik"),
        ]
    )
    merged = MasterProfileData(
        education=[EducationEntry(id="e-x", institution="Uni X", degree="Dipl.",
                                  field="Informatik")]
    )
    items = compute_import_not_applied(
        incoming, merged, [SetField(target="e-x", field="field", value="Informatik")]
    )
    assert len(items) == 2


def test_the_target_s_own_fields_break_a_tie():
    """Several incoming entries fit the written value — the TARGET decides, the
    way #707's two-M.Sc. case does."""
    incoming = MasterProfileData(
        education=[
            EducationEntry(institution="Uni X", degree="Diplom", field="Informatik"),
            EducationEntry(institution="Hochschule B", degree="Magister", field="Informatik"),
        ]
    )
    merged = MasterProfileData(
        education=[EducationEntry(id="e-x", institution="Uni X", degree="Dipl.",
                                  field="Informatik")]
    )
    items = compute_import_not_applied(
        incoming, merged, [SetField(target="e-x", field="field", value="Informatik")]
    )
    assert [i.label for i in items] == ["Hochschule B / Magister"]


def test_the_languages_shape_of_the_same_run():
    """The other half of the founder's run 1: `Englisch`/`Deutsch` against an EN
    vault, correlated by a `set_field` that writes the incoming level."""
    merged = MasterProfileData(
        languages=[
            Language(id=EN_LANG_ID, language="English", level="Professional Working"),
            Language(id="l-german", language="German", level="Native"),
        ]
    )
    incoming = MasterProfileData(
        languages=[
            Language(language="Englisch", level="Professional Working"),
            Language(language="Deutsch", level="Native"),
        ]
    )
    ops = [
        SetField(target=EN_LANG_ID, field="level", value="Professional Working"),
        SetField(target="l-german", field="level", value="Native"),
    ]
    assert compute_import_not_applied(incoming, merged, ops) == []
    # …and without the ops the same pair is the `partial` the founder saw.
    assert len(compute_import_not_applied(incoming, merged, [])) == 2
