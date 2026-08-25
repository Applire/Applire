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

"""#480 PR 3 — `ReplaceSection`, the typed manual section edit.

Three properties, tested here in isolation from any door:

1. **The section vocabulary is the guard.** `metadata` is not in
   `VAULT_SECTIONS`, so `denied_concepts`, `enrichment_history` and the parked
   lists are unreachable by a section replace — a manual edit can neither
   release a persisted denial (ADR-059) nor forge its own audit trail.
2. **The semantics are today's PATCH contract, unchanged.** Object sections
   merge-patch (#178); list sections replace wholesale.
3. **The diff is the receipt.** Each entry that appeared, changed or
   disappeared earns its own `FieldChange`, instead of the one opaque
   before/after blob the intake used to mint (ADR-063 amended 2026-08-09
   clause 8 / §7.7 ruling — deletion is not new capability, the receipt is).
"""
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.schemas.profile import (
    OBJECT_SECTIONS,
    VAULT_SECTIONS,
    MasterProfileData,
)
from applire.services.profile.field_edit import build_replace_section_op
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    CommitOp,
    ReconcileOp,
    ReplaceSection,
)

_WORK_A = "11111111-1111-1111-1111-111111111111"
_WORK_B = "22222222-2222-2222-2222-222222222222"


def _profile(**overrides: Any) -> MasterProfileData:
    base: dict[str, Any] = {
        "personal_info": {
            "name": "Anna Bauer",
            "email": "anna@example.invalid",
            "phone": "+49 30 000000",
        },
        "professional_summary": {"de": "Deutsche Zusammenfassung.", "en": None},
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "advanced"},
            {"name": "Kafka", "category": "technical", "proficiency": "basic"},
        ],
        "work_experience": [
            {
                "id": _WORK_A,
                "company": "Acme GmbH",
                "role": "Engineer",
                "start_date": "2020-01",
                "responsibilities": ["Ran the build"],
            },
            {
                "id": _WORK_B,
                "company": "Beta AG",
                "role": "Analyst",
                "start_date": "2016-01",
                "end_date": "2019-12",
            },
        ],
        "languages": [{"language": "German", "level": "C2"}],
        "metadata": {},
    }
    base.update(overrides)
    return MasterProfileData.model_validate(base)


def _sections_of(profile: MasterProfileData, section: str) -> Any:
    return profile.model_dump(mode="json").get(section)


# ── 1. The section vocabulary guard ───────────────────────────────────────────


def test_metadata_is_not_an_editable_section():
    """The structural reason a manual edit can never touch `denied_concepts`,
    `enrichment_history` or the parked lists."""
    assert "metadata" not in VAULT_SECTIONS
    assert "_meta" not in VAULT_SECTIONS
    assert "meta" not in VAULT_SECTIONS


def test_replace_section_op_refuses_metadata():
    """The guard lives on the OP, so no adapter can route around it."""
    with pytest.raises(ValidationError):
        ReplaceSection(section="metadata", value={"denied_concepts": []})


def test_replace_section_op_refuses_an_unknown_section():
    with pytest.raises(ValidationError):
        ReplaceSection(section="bank_details", value=[])


def test_adapter_refuses_metadata_with_a_user_facing_message():
    with pytest.raises(ValueError, match="Invalid section 'metadata'"):
        build_replace_section_op("metadata", {"denied_concepts": []})


def test_every_editable_section_is_a_real_profile_field():
    """A typo in the vocabulary would write a stray key into the JSONB dump."""
    fields = set(MasterProfileData.model_fields)
    assert VAULT_SECTIONS <= fields
    assert OBJECT_SECTIONS <= VAULT_SECTIONS


def test_replace_section_is_adapter_only():
    """A hallucinated `replace_section` never becomes an object: a section
    replace can DROP entries, so model-emittability would be a silent delete."""
    payload: dict[str, Any] = {"op": "replace_section", "section": "skills", "value": []}
    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python(payload)
    assert isinstance(TypeAdapter(CommitOp).validate_python(payload), ReplaceSection)


def test_hallucinated_replace_section_is_dropped_at_the_parse_seam():
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [
            {"op": "replace_section", "section": "skills", "value": []},
            {"op": "upsert_skill", "name": "Go", "category": "technical"},
        ]
    )
    assert not any(isinstance(o, ReplaceSection) for o in ops)
    assert len(ops) == 1


# ── 2. The semantics: merge-patch for objects, wholesale for lists ────────────


def test_object_section_merge_patches_and_keeps_unsupplied_keys():
    """#178 — a partial object must never wipe what it did not mention."""
    profile = _profile()
    op = build_replace_section_op("personal_info", {"address": "Musterweg 1"})

    result = apply_ops(profile, [op], "manual_edit")

    info = result.profile.personal_info
    assert info.address == "Musterweg 1"
    assert info.name == "Anna Bauer"
    assert info.email == "anna@example.invalid"


def test_object_section_explicit_null_clears_and_receipts_a_removal():
    profile = _profile()
    op = build_replace_section_op("personal_info", {"phone": None})

    result = apply_ops(profile, [op], "manual_edit")

    assert result.profile.personal_info.phone is None
    assert result.profile.personal_info.name == "Anna Bauer"
    cleared = [c for c in result.changes if c.field == "phone"]
    assert [c.action for c in cleared] == ["removed"]
    assert cleared[0].old_value == "+49 30 000000"


def test_professional_summary_other_language_slot_survives():
    profile = _profile()
    op = build_replace_section_op("professional_summary", {"en": "English summary."})

    result = apply_ops(profile, [op], "manual_edit")

    assert result.profile.professional_summary.en == "English summary."
    assert result.profile.professional_summary.de == "Deutsche Zusammenfassung."


def test_object_section_refuses_a_list_payload():
    with pytest.raises(ValueError, match="expects an object"):
        build_replace_section_op("personal_info", ["not", "a", "dict"])


def test_list_section_is_replaced_wholesale():
    profile = _profile()
    op = build_replace_section_op("languages", [{"language": "French", "level": "B1"}])

    result = apply_ops(profile, [op], "manual_edit")

    assert [lang.language for lang in result.profile.languages] == ["French"]


def test_json_string_payloads_are_decoded():
    """A door that double-encodes its body still lands the real value."""
    profile = _profile()
    op = build_replace_section_op("languages", '[{"language": "Spanish"}]')

    result = apply_ops(profile, [op], "manual_edit")

    assert [lang.language for lang in result.profile.languages] == ["Spanish"]


def test_skill_status_is_whatever_the_payload_says():
    """The write path must not RE-DECIDE a skill's standing: the payload's own
    status wins, and an absent one takes the schema default — exactly as the
    PATCH intake behaved before it was a `ReplaceSection` (#336's
    `unconfirmed` saves depend on this, and so does the profile page)."""
    profile = _profile()
    op = build_replace_section_op(
        "skills",
        [
            {"name": "Python", "category": "technical"},
            {"name": "Rust", "category": "technical", "status": "unconfirmed"},
        ],
    )

    result = apply_ops(profile, [op], "manual_edit")

    by_name = {s.name: s for s in result.profile.skills}
    assert by_name["Python"].status == "confirmed"  # schema default
    assert by_name["Rust"].status == "unconfirmed"  # as supplied


# ── 3. The diff IS the receipt ────────────────────────────────────────────────


def test_removing_a_list_entry_receipts_that_removal():
    """The capability that closes the opaque-blob defect: the trail now names
    WHICH entry went, not merely that the section changed."""
    profile = _profile()
    op = build_replace_section_op(
        "skills", [{"name": "Python", "category": "technical", "proficiency": "advanced"}]
    )

    result = apply_ops(profile, [op], "manual_edit")

    assert [s.name for s in result.profile.skills] == ["Python"]
    removals = [c for c in result.changes if c.action == "removed"]
    assert [c.field for c in removals] == ["Kafka"]
    assert removals[0].section == "skills"
    assert removals[0].old_value["name"] == "Kafka"
    assert removals[0].new_value is None


def test_a_pure_removal_is_not_reported_as_an_update_of_the_whole_section():
    """The pre-PR receipt was one `updated` blob carrying both section dumps —
    unusable, and the reason `discarded_later_edits` cannot tell an edit from a
    deletion."""
    profile = _profile()
    op = build_replace_section_op(
        "skills", [{"name": "Python", "category": "technical", "proficiency": "advanced"}]
    )

    result = apply_ops(profile, [op], "manual_edit")

    assert len(result.changes) == 1
    assert result.changes[0].action == "removed"


def test_removing_a_work_entry_receipts_it_by_role_and_company():
    profile = _profile()
    remaining = [e for e in _sections_of(profile, "work_experience") if e["id"] == _WORK_A]
    op = build_replace_section_op("work_experience", remaining)

    result = apply_ops(profile, [op], "manual_edit")

    removals = [c for c in result.changes if c.action == "removed"]
    assert [c.field for c in removals] == ["Analyst @ Beta AG"]


def test_editing_an_entry_is_an_update_not_a_remove_plus_add():
    """Entity identity follows the ids the appliers key on — the CV section
    editor rewrites `responsibilities` on an entry it located by `work_id`
    (#336), and that must read as one edited role."""
    profile = _profile()
    entries = _sections_of(profile, "work_experience")
    entries[0]["responsibilities"] = ["Ran the build", "Owned the release train"]
    op = build_replace_section_op("work_experience", entries)

    result = apply_ops(profile, [op], "manual_edit")

    assert [c.action for c in result.changes] == ["updated"]
    assert result.changes[0].field == "Engineer @ Acme GmbH"
    assert result.changes[0].new_value["responsibilities"][-1] == "Owned the release train"


def test_an_id_less_payload_matches_on_labels_instead_of_inventing_deletions():
    """A hand-built agent payload carries no ids — id-only matching would read
    every edit as a deletion plus an addition, i.e. would invent deletions the
    candidate never made. Since ADR-077's id preservation, an id-less incoming
    entry inherits its natural-key match's stored id, so the receipt is
    precise: only the actually-edited entry reports, nothing is reported gone,
    and the stored ids survive (a re-mint would mass-stale the fact pins
    addressing this section — SF-PIN.8's shape through this door)."""
    profile = _profile()
    stored_ids = [e["id"] for e in _sections_of(profile, "work_experience")]
    entries = _sections_of(profile, "work_experience")
    for entry in entries:
        entry.pop("id", None)
    entries[0]["location"] = "Berlin"
    op = build_replace_section_op("work_experience", entries)

    result = apply_ops(profile, [op], "manual_edit")

    assert {c.action for c in result.changes} == {"updated"}
    assert [c.field for c in result.changes] == ["Engineer @ Acme GmbH"]
    assert [
        e.id for e in result.profile.work_experience
    ] == stored_ids  # ids preserved, pins stay resolvable


def test_renaming_a_role_on_a_kept_id_is_an_update():
    profile = _profile()
    entries = _sections_of(profile, "work_experience")
    entries[0]["role"] = "Senior Engineer"
    op = build_replace_section_op("work_experience", entries)

    result = apply_ops(profile, [op], "manual_edit")

    assert [c.action for c in result.changes] == ["updated"]
    assert result.changes[0].field == "Senior Engineer @ Acme GmbH"


def test_adding_an_entry_receipts_only_the_addition():
    profile = _profile()
    entries = _sections_of(profile, "skills")
    entries.append({"name": "Terraform", "category": "technical"})
    op = build_replace_section_op("skills", entries)

    result = apply_ops(profile, [op], "manual_edit")

    assert [(c.action, c.field) for c in result.changes] == [("added", "Terraform")]


def test_an_identical_payload_receipts_nothing():
    """Re-saving an untouched section is not a change, and must not read as
    one on the "what changed & why" surface."""
    profile = _profile()
    op = build_replace_section_op("skills", _sections_of(profile, "skills"))

    result = apply_ops(profile, [op], "manual_edit")

    assert result.changes == []


def test_clearing_a_whole_list_section_receipts_every_entry():
    profile = _profile()
    op = build_replace_section_op("skills", [])

    result = apply_ops(profile, [op], "manual_edit")

    assert result.profile.skills == []
    assert sorted(c.field for c in result.changes) == ["Kafka", "Python"]
    assert {c.action for c in result.changes} == {"removed"}


def test_a_schema_rejecting_payload_raises_a_value_error():
    """Both doors translate `ValueError` into 422 / invalid_input; pydantic's
    `ValidationError` IS one, so the door contract is unchanged."""
    profile = _profile()
    op = ReplaceSection(section="skills", value=[{"category": "technical"}])

    with pytest.raises(ValueError):
        apply_ops(profile, [op], "manual_edit")


def test_basis_updated_at_is_carried_on_the_op_and_ignored_by_the_applier():
    """ADR-063 amended 2026-08-25 (E055): the basis is the profile's
    `updated_at` the edit was composed against. The APPLIER never consults it
    — refusing a stale edit is the committer's job (see
    test_commit_ops_stale_edit.py), so a pure `apply_ops` stays pure."""
    from datetime import datetime, timezone

    profile = _profile()
    basis = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    op = build_replace_section_op("skills", [], basis_updated_at=basis)

    assert op.basis_updated_at == basis
    assert apply_ops(profile, [op], "manual_edit").profile.skills == []
