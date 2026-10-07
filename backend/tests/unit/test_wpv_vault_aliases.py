# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WP-V (Strawberry build 2) — alternate names, the DE/EN language-name table,
`match_existing` on engagements, and the alias-aware import witness.

ADR-046 / ADR-063 amended 2026-10-07 (#709 #715 #716), rulings V-1 (an alias
counts only when exactly one entry carries it, plus equal stated start months
for jobs) and V-2. Synthetic cases only (`tests/files/strawberry_v_aliases/`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from applire.schemas.profile import (
    EducationEntry,
    Language,
    MasterProfileData,
    MatchReceipt,
    Skill,
    VolunteerActivity,
    WorkEntry,
)
from applire.services.profile.language_names import (
    LANGUAGE_NAME_DE_EN,
    canonical_language,
    same_language,
)
from applire.services.profile.reconcile import aliases as A
from applire.services.profile.reconcile.alias_writer import record_bound_aliases
from applire.services.profile.reconcile.apply import _norm, apply_ops
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.services.profile.reconcile.ops import (
    MatchExisting,
    UpsertEducation,
    UpsertLanguage,
    UpsertSkill,
    UpsertVolunteer,
    UpsertWork,
)

FX = Path(__file__).resolve().parents[3] / "tests" / "files" / "strawberry_v_aliases"


def _load(name: str) -> MasterProfileData:
    return MasterProfileData.model_validate(json.loads((FX / name).read_text(encoding="utf-8")))


def _labels(items) -> list[str]:
    return [i.label for i in items]


# ── schema ────────────────────────────────────────────────────────────────────


def test_empty_alias_lists_are_omitted_from_every_dump():
    profile = MasterProfileData(
        work_experience=[WorkEntry(company="A")],
        education=[EducationEntry(institution="U", degree="D")],
        skills=[Skill(name="Python")],
        languages=[Language(language="Deutsch")],
        volunteer_activities=[VolunteerActivity(organization="V", role="R")],
    )
    dumped = json.dumps(profile.model_dump(mode="json"))
    for key in ('"aliases"', "company_aliases", "organization_aliases",
                "institution_aliases", "degree_aliases"):
        assert key not in dumped
    # role_aliases keeps its always-present form (the #688 goldens carry it).
    assert "role_aliases" in dumped


def test_alias_lists_round_trip_and_legacy_load():
    skill = Skill(name="Maschinelles Lernen", aliases=["Machine Learning", "", 3])
    assert skill.aliases == ["Machine Learning"]
    again = Skill.model_validate(skill.model_dump(mode="json"))
    assert again.aliases == ["Machine Learning"]
    assert Skill.model_validate({"name": "x"}).aliases == []
    assert WorkEntry.model_validate({"company": "x", "company_aliases": "bad"}).company_aliases == []


def test_match_receipt_new_fields_are_optional_for_old_records():
    old = MatchReceipt.model_validate(
        {"section": "skills", "entity_id": "e", "incoming": "a", "existing": "b"}
    )
    assert old.basis == "model" and old.aliases_added == {} and old.incoming_entry is None
    assert old.undone_at is None


def test_alias_module_norm_equals_the_committers():
    for value in ("Café", "Café", "  ROCHE ", None, 3):
        assert A.norm(value) == _norm(value)


# ── the language-name table ───────────────────────────────────────────────────


def test_language_table_is_whole_string_only():
    assert canonical_language("Englisch") == "english"
    assert canonical_language(" english ") == "english"
    assert same_language("Deutsch", "German")
    # never a partial fold (adversarial finding 5, 2026-10-07)
    assert canonical_language("Chinese (Cantonese)") is None
    assert canonical_language("Deutsch (Muttersprache)") is None
    assert canonical_language("Deutsche Gebärdensprache") is None
    assert not same_language("Schweizerdeutsch", "Deutsch")
    assert not same_language(None, None)


def test_render_side_table_is_the_same_table():
    from applire.services.cv import _LANGUAGE_NAME_CANON

    assert _LANGUAGE_NAME_CANON == LANGUAGE_NAME_DE_EN


# ── appliers ──────────────────────────────────────────────────────────────────


def _vault_b() -> MasterProfileData:
    return _load("case_b_vault_en.json")


def test_targeted_upsert_work_records_the_other_employer_name():
    """Writer (b) runs on the IMPORT door only, and records the DOCUMENT's name
    (adversarial finding 6, 2026-10-07) — the shared applier writes none."""
    ops = [UpsertWork(
        ref="w1", target="w-nov", company="Novartis", role="Systemanalytiker",
        start_date="2011-06",
    )]
    alone = apply_ops(_vault_b(), ops, "linkedin_import")
    assert next(w for w in alone.profile.work_experience if w.id == "w-nov").company_aliases == []
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Novartis", role="Systemanalytiker", start_date="2011-06",
    )])
    applied = apply_ops(_vault_b(), ops, "linkedin_import")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    entry = next(w for w in applied.profile.work_experience if w.id == "w-nov")
    assert entry.company == "Novartis Diagnostics GmbH"
    assert entry.company_aliases == ["Novartis"]
    assert entry.role_aliases == ["Systemanalytiker"]
    assert any(c.field == "company_aliases" and c.new_value == "Novartis" for c in applied.changes)


def test_targeted_upsert_with_contradicting_start_month_records_no_company_alias():
    applied = apply_ops(_vault_b(), [UpsertWork(
        ref="w1", target="w-nov", company="Novartis", start_date="2015-03",
    )], "linkedin_import")
    entry = next(w for w in applied.profile.work_experience if w.id == "w-nov")
    assert entry.company_aliases == []


def test_guard_adoption_without_a_model_target_records_no_alias():
    vault = _vault_b()
    applied = apply_ops(vault, [UpsertWork(
        ref="w1", target=None, company="Labvantage Solutions", role="LIMS Consultant",
        start_date="2020-01",
    )], "linkedin_import")
    assert len(applied.profile.work_experience) == 3  # adopted by the #177 guard
    assert all(w.company_aliases == [] for w in applied.profile.work_experience)


def test_case_only_company_variant_is_not_an_alias():
    applied = apply_ops(_vault_b(), [UpsertWork(
        ref="w1", target="w-nov", company="NOVARTIS DIAGNOSTICS GMBH", start_date="2011-06",
    )], "cv_upload")
    entry = next(w for w in applied.profile.work_experience if w.id == "w-nov")
    assert entry.company_aliases == []


def test_no_target_upsert_on_a_recorded_company_alias_and_same_month_merges():
    vault = _vault_b()
    vault.work_experience[1].company_aliases = ["Novartis"]
    applied = apply_ops(vault, [UpsertWork(
        ref="w1", target=None, company="Novartis", role="Systemanalytiker", start_date="2011-06",
    )], "linkedin_import")
    assert len(applied.profile.work_experience) == 3
    assert not applied.pending_confirmations


def test_alias_with_a_year_only_date_never_merges_silently():
    vault = _vault_b()
    vault.work_experience[1].company_aliases = ["Novartis"]
    applied = apply_ops(vault, [UpsertWork(
        ref="w1", target=None, company="Novartis", role="Systemanalytiker", start_date="2011",
    )], "linkedin_import")
    # the alias does not fire; the pre-existing guard decides as it always did
    assert all(
        not (w.company == "Novartis Diagnostics GmbH" and w.role_aliases)
        for w in applied.profile.work_experience
    )


def test_targeted_upsert_volunteer_records_organization_alias():
    vault = MasterProfileData(volunteer_activities=[VolunteerActivity(
        id="v1", organization="Deutsches Rotes Kreuz e.V.", role="Sanitäter", start_date="2015-01",
    )])
    ops = [UpsertVolunteer(ref="v", target="v1", organization="German Red Cross", role="Sanitäter")]
    incoming = MasterProfileData(volunteer_activities=[VolunteerActivity(
        organization="DRK Kreisverband", role="Sanitäter", start_date="2015-01",
    )])
    applied = apply_ops(vault, ops, "cv_upload")
    assert applied.profile.volunteer_activities[0].organization_aliases == []  # not in the applier
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    # the document's name, never the model's translation (finding 6)
    assert applied.profile.volunteer_activities[0].organization_aliases == ["DRK Kreisverband"]


def test_skill_on_a_recorded_alias_merges_with_an_alias_receipt():
    vault = MasterProfileData(skills=[Skill(id="s1", name="Maschinelles Lernen", aliases=["Machine Learning"])])
    applied = apply_ops(vault, [UpsertSkill(name="Machine Learning")], "cv_upload")
    assert [s.name for s in applied.profile.skills] == ["Maschinelles Lernen"]
    assert [(m.basis, m.incoming, m.existing) for m in applied.matched] == [
        ("alias", "Machine Learning", "Maschinelles Lernen")
    ]


def test_skill_alias_carried_by_two_entries_matches_neither():
    vault = MasterProfileData(skills=[
        Skill(id="s1", name="Testautomatisierung", aliases=["Testing"]),
        Skill(id="s2", name="Manuelles Testen", aliases=["Testing"]),
    ])
    applied = apply_ops(vault, [UpsertSkill(name="Testing")], "cv_upload")
    assert not applied.matched
    assert "Testing" in [s.name for s in applied.profile.skills] or applied.pending_confirmations


def test_language_through_the_name_table_merges_and_says_so():
    vault = MasterProfileData(languages=[Language(id="l1", language="Englisch", level=None)])
    applied = apply_ops(vault, [UpsertLanguage(language="English", level="C1")], "cv_upload")
    assert [(l.language, l.level) for l in applied.profile.languages] == [("Englisch", "C1")]
    assert [(m.basis, m.incoming) for m in applied.matched] == [("name_table", "English")]
    assert applied.profile.languages[0].aliases == []  # the table MATCHES, never aliases


def test_language_table_never_folds_a_qualified_name():
    vault = MasterProfileData(languages=[Language(id="l1", language="Chinesisch")])
    applied = apply_ops(vault, [UpsertLanguage(language="Chinese (Cantonese)")], "cv_upload")
    assert len(applied.profile.languages) == 2
    assert not applied.matched


def test_education_on_recorded_aliases_merges():
    vault = MasterProfileData(education=[EducationEntry(
        id="e1", institution="Universität Leipzig", degree="German Diploma",
        degree_aliases=["Diplom"],
    )])
    # Ruling adv-vault-1 = B (MD2-15): the alias never overrides the guard's
    # question. Here the guard ASKS (same institution, other degree name), so
    # the upsert path asks; the witness still carries a no-op re-import.
    applied = apply_ops(vault, [UpsertEducation(
        institution="Universität Leipzig", degree="Diplom", grade="1,3",
    )], "linkedin_import")
    assert len(applied.profile.education) == 1
    assert len(applied.pending_confirmations) == 1
    assert not applied.matched


# ── witness: #715 match_existing on engagements ───────────────────────────────


def _labvantage_case():
    vault = _vault_b()
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Labvantage", role="LIMS Consultant", start_date="2020-01", is_current=True,
    )])
    return vault, incoming


def test_engagement_match_existing_carries_the_position():
    vault, incoming = _labvantage_case()
    ops = [MatchExisting(target="w-lims", incoming="Labvantage / LIMS Consultant")]
    applied = apply_ops(vault, ops, "linkedin_import")
    assert compute_import_not_applied(incoming, applied.profile, ops) == []
    assert applied.matched[0].section == "work_experience"
    assert applied.changes == []  # a match writes nothing on the turn path


def test_engagement_match_existing_without_any_op_lists_the_position():
    vault, incoming = _labvantage_case()
    applied = apply_ops(vault, [], "linkedin_import")
    assert _labels(compute_import_not_applied(incoming, applied.profile, [])) == [
        "Labvantage / LIMS Consultant / 2020-01"
    ]


def test_engagement_match_existing_refuses_another_start_month():
    vault, _ = _labvantage_case()
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Labvantage", role="LIMS Consultant", start_date="2023-04",
    )])
    ops = [MatchExisting(target="w-lims", incoming="Labvantage / LIMS Consultant")]
    applied = apply_ops(vault, ops, "linkedin_import")
    assert _labels(compute_import_not_applied(incoming, applied.profile, ops)) == [
        "Labvantage / LIMS Consultant / 2023-04"
    ]


def test_engagement_match_existing_two_stints_target_decides_by_month():
    vault, _ = _labvantage_case()
    incoming = MasterProfileData(work_experience=[
        WorkEntry(company="Labvantage", role="LIMS Consultant", start_date="2020-01"),
        WorkEntry(company="Labvantage", role="LIMS Consultant", start_date="2016-02"),
    ])
    ops = [MatchExisting(target="w-lims", incoming="Labvantage / LIMS Consultant")]
    applied = apply_ops(vault, ops, "linkedin_import")
    # the 2016 stint is a different CV line and stays listed
    assert _labels(compute_import_not_applied(incoming, applied.profile, ops)) == [
        "Labvantage / LIMS Consultant / 2016-02"
    ]


# ── witness: recorded names under arm (a) (ruling V-1 = B) ────────────────────


def test_reimport_after_aliases_lists_nothing_without_any_model_op():
    """#716 acceptance: the further re-import lists nothing as not carried, with
    no match_existing needed."""
    vault = _vault_b()
    incoming = _load("case_b_incoming_linkedin_de.json")
    # first import: the model's bindings (as luna emitted them in the replay)
    ops = [
        UpsertWork(ref="w1", target="w-bsd", company="Blutspendedienst des Hessischen Roten Kreuzes gGmbH",
                   role="Systementwickler", start_date="2012-08"),
        UpsertWork(ref="w2", target="w-nov", company="Novartis", role="Systemanalytiker", start_date="2011-06"),
        MatchExisting(target="w-lims", incoming="Labvantage / LIMS Consultant"),
        UpsertWork(ref="w3", target=None, company="Novartis Pharma AG", role="Data Scientist",
                   start_date="2009-03", end_date="2010-09"),
        MatchExisting(target="e-uni", incoming="Universität Leipzig / Diplom"),
        MatchExisting(target="e-kol2", incoming="Kolping Hochschule / Fachinformatiker"),
        MatchExisting(target="sb1", incoming="Computervalidierung"),
        MatchExisting(target="lb1", incoming="Deutsch"),
        MatchExisting(target="lb2", incoming="Englisch"),
    ]
    first = apply_ops(vault, ops, "linkedin_import")
    record_bound_aliases(incoming, first.profile, ops, first.matched, first.changes)
    assert compute_import_not_applied(incoming, first.profile, ops) == []
    # second import of the same document: NO op at all
    second = apply_ops(first.profile, [], "linkedin_import")
    assert compute_import_not_applied(incoming, second.profile, []) == []


def test_recorded_name_carried_by_two_entries_carries_nothing():
    merged = MasterProfileData(skills=[
        Skill(name="Testautomatisierung", aliases=["Testing"]),
        Skill(name="Manuelles Testen", aliases=["Testing"]),
    ])
    incoming = MasterProfileData(skills=[Skill(name="Testing")])
    assert _labels(compute_import_not_applied(incoming, merged, [])) == ["Testing"]


def test_recorded_company_alias_needs_a_stated_equal_month():
    merged = MasterProfileData(work_experience=[WorkEntry(
        company="Roche Diagnostics GmbH", role="System Analyst", start_date="2011-06",
        company_aliases=["Roche"], role_aliases=["Systemanalytiker"],
    )])
    same = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker", start_date="2011-06")])
    year_only = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker", start_date="2011")])
    other = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker", start_date="2019-06")])
    assert compute_import_not_applied(same, merged, []) == []
    assert len(compute_import_not_applied(year_only, merged, [])) == 1
    assert len(compute_import_not_applied(other, merged, [])) == 1


def test_witness_reads_the_language_table():
    merged = MasterProfileData(languages=[Language(language="Englisch"), Language(language="Deutsch")])
    incoming = MasterProfileData(languages=[Language(language="English"), Language(language="German")])
    assert compute_import_not_applied(incoming, merged, []) == []
    unknown = MasterProfileData(languages=[Language(language="Chinese (Cantonese)")])
    assert len(compute_import_not_applied(unknown, merged, [])) == 1


# ── the import path's alias writer ────────────────────────────────────────────


def test_alias_text_comes_from_the_document_entry_not_the_ops_free_text():
    vault = MasterProfileData(education=[EducationEntry(
        id="e1", institution="TU München", degree="Master of Science",
    )])
    incoming = MasterProfileData(education=[EducationEntry(institution="TU München", degree="M.Sc.")])
    # the model swapped the order in its free text — the binder still finds the
    # ONE entry via a single key field, and the alias is copied from that entry
    ops = [MatchExisting(target="e1", incoming="M.Sc.")]
    applied = apply_ops(vault, ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    entry = applied.profile.education[0]
    assert entry.degree_aliases == ["M.Sc."]
    assert entry.institution_aliases == []
    assert applied.matched[0].aliases_added == {"degree_aliases": "M.Sc."}
    assert applied.matched[0].incoming_entry["degree"] == "M.Sc."
    assert "id" not in applied.matched[0].incoming_entry
    assert any(c.field == "degree_aliases" for c in applied.changes)


def test_alias_writer_positional_sanity():
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Acme GmbH", role="Engineer", start_date="2020-01",
    )])
    # an incoming entry whose COMPANY slot carries the target's role
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Engineer", role="Engineer", start_date="2020-01")])
    ops = [MatchExisting(target="w1", incoming="Engineer")]
    applied = apply_ops(vault, ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    assert applied.profile.work_experience[0].company_aliases == []


def test_alias_writer_needs_equal_stated_months_for_engagements():
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Roche Diagnostics GmbH", role="System Analyst", start_date="2011-06",
    )])
    # an undated LinkedIn-text entry: the BINDING treats a missing date as a
    # wildcard (#715), the ALIAS writer does not (it needs both months stated)
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="System Analyst", start_date=None)])
    ops = [MatchExisting(target="w1", incoming="Roche / System Analyst")]
    applied = apply_ops(vault, ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    assert applied.profile.work_experience[0].company_aliases == []
    # but the binding itself carries (month wildcard for the BINDING, #715)
    assert compute_import_not_applied(incoming, applied.profile, ops) == []


def test_every_alias_traces_to_a_matched_receipt_or_a_targeted_upsert_change():
    """#709 acceptance: every alias on the profile traces to a receipt."""
    vault = _vault_b()
    incoming = _load("case_b_incoming_linkedin_de.json")
    ops = [
        UpsertWork(ref="w2", target="w-nov", company="Novartis", role="Systemanalytiker", start_date="2011-06"),
        MatchExisting(target="w-lims", incoming="Labvantage / LIMS Consultant"),
        MatchExisting(target="e-uni", incoming="Universität Leipzig / Diplom"),
        MatchExisting(target="sb1", incoming="Computervalidierung"),
    ]
    applied = apply_ops(vault, ops, "linkedin_import")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    receipted = {(c.field, c.new_value) for c in applied.changes if c.field.endswith("aliases")}
    for section, alias_fields in A.ALIAS_FIELDS.items():
        for entry in getattr(applied.profile, section):
            for alias_field in set(alias_fields.values()) - {"role_aliases"}:
                for alias in getattr(entry, alias_field, []):
                    assert (alias_field, alias) in receipted, (section, alias_field, alias)
    assert next(s for s in applied.profile.skills if s.id == "sb1").aliases == ["Computervalidierung"]
    lims = next(w for w in applied.profile.work_experience if w.id == "w-lims")
    assert lims.company_aliases == ["Labvantage"]


# ── adversarial fixes 2026-10-07 (MD2-15 = adv-vault-1 B): guard-specific pins ─


def _tum_undated() -> MasterProfileData:
    # Undated on BOTH sides, so the years rule allows the alias: only the
    # empty-key rule keeps an unstated degree from reaching the M.Sc.
    return MasterProfileData(education=[EducationEntry(
        id="e1", institution="Technische Universität München", degree="M.Sc. Informatik",
        institution_aliases=["TU München"],
    )])


def test_empty_degree_never_reaches_an_entry_through_the_institution_alias_applier():
    applied = apply_ops(_tum_undated(), [UpsertEducation(institution="TU München", degree="")], "cv_upload")
    assert len(applied.profile.education) == 2
    assert not applied.matched


def test_empty_degree_never_carried_through_the_institution_alias_witness():
    incoming = MasterProfileData(education=[EducationEntry(institution="TU München", degree="")])
    assert [i.label for i in compute_import_not_applied(incoming, _tum_undated(), [])] == ["TU München"]


def test_two_document_lines_reading_as_one_vault_entry_are_not_alias_carried():
    """Incoming-side exactly-one: the document lists the entry under its own
    name AND under the alias — two lines, so the alias line is not carried."""
    vault = MasterProfileData(skills=[Skill(id="s1", name="Testautomatisierung", aliases=["Testing"])])
    incoming = MasterProfileData(skills=[Skill(name="Testautomatisierung"), Skill(name="Testing")])
    assert [i.label for i in compute_import_not_applied(incoming, vault, [])] == ["Testing"]


def test_engagement_alias_match_in_the_applier_leaves_an_alias_receipt():
    """Finding 2 (ADR-046 am. cl. 7): the applier's own engagement alias match
    is receipted, independent of the import bridge."""
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Roche Diagnostics GmbH", role="Data Scientist", start_date="2019-03",
        company_aliases=["Roche"],
    )])
    applied = apply_ops(vault, [UpsertWork(ref="w", company="Roche", role="Data Scientist",
                                           start_date="2019-03")], "cv_upload")
    assert [(m.basis, m.entity_id) for m in applied.matched] == [("alias", "w1")]


def test_a_table_language_pair_is_never_recorded_as_an_alias():
    """Finding 5 (ADR-046 am. cl. 5): the table MATCHES, it does not alias."""
    entry = Language(id="l1", language="Englisch")
    assert A.add_alias(entry, "language", "languages", "English") is False
    assert entry.aliases == []


def test_a_model_matched_table_pair_is_receipted_as_name_table():
    vault = MasterProfileData(languages=[Language(id="l1", language="Englisch", level="C1")])
    incoming = MasterProfileData(languages=[Language(language="English", level="C1")])
    ops = [MatchExisting(target="l1", incoming="English")]
    applied = apply_ops(vault, ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    assert [m.basis for m in applied.matched] == ["name_table"]
    assert applied.profile.languages[0].aliases == []


def test_volunteer_alias_match_in_the_applier_leaves_an_alias_receipt():
    vault = MasterProfileData(volunteer_activities=[VolunteerActivity(
        id="v1", organization="Deutsches Rotes Kreuz e.V.", role="Sanitäter", start_date="2015-01",
        organization_aliases=["DRK"],
    )])
    applied = apply_ops(vault, [UpsertVolunteer(ref="v", organization="DRK", role="Sanitäter",
                                                start_date="2015-01")], "cv_upload")
    assert [(m.basis, m.entity_id) for m in applied.matched] == [("alias", "v1")]
