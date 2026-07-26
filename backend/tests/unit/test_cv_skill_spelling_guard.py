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

"""Tiramisu wave-6 follow-up — blind hiring-panel run #6 ground truth.

The ADR-038 language pass (``review_cv_language.py``, chain_id ``cv_language``) treats
a skill name as ordinary translatable prose. ``GxP`` is not on its narrow proper-noun
allow-list (product/tool/company names only), so the model "translated" it by
expanding the acronym: ``GxP Compliance & Computer System Validation`` shipped as
``Good Practice Compliance & Computer System Validation``. Confirmed by
``ats_audit.skills_near_dupe`` returning False on every pinned pair (Jaccard
0.25-0.57, well under the 0.75 near-dupe bar) -- that instrument is calibrated for a
different job (safe auto-merge across a whole profile) and does not, and should not,
catch this. This is a probe of the NEW, narrower ``_restore_skill_spelling`` guard,
the deterministic post-pass that is the real protection (prompt wording alone does
not hold).
"""

import logging

from applire.schemas.cv import TailoredCVData, TailoredContact
from applire.services.cv import _restore_skill_spelling

# Ground truth pinned live in Tiramisu wave-6 charter run #6 (2026-07-26).
_VAULT_SKILLS = [
    "GxP Compliance & Computer System Validation",
    "GxP System Ownership & Governance",
    "GxP Environments",
    "Python",
]

_PROFILE = {
    "skills": [
        {"name": n, "category": "technical", "experience_refs": ["w1"]} for n in _VAULT_SKILLS
    ]
}

_MANGLED = [
    "Good Practice Compliance & Computer System Validation",
    "Good Practice System Ownership & Governance",
    "Good Practice Environments",
]


def _tailored(skills: list[str]) -> TailoredCVData:
    return TailoredCVData(contact=TailoredContact(name="Test Candidate"), skills=skills)


class TestAcronymExpansionRestored:
    def test_all_three_pinned_mangled_names_are_restored(self):
        writer_output = _tailored(list(_MANGLED) + ["Python"])

        result = _restore_skill_spelling(writer_output, _PROFILE)

        for original in _VAULT_SKILLS:
            assert original in result.skills, f"{original!r} was not restored"
        for mangled in _MANGLED:
            assert mangled not in result.skills, f"{mangled!r} should have been rewritten"

    def test_object_shaped_profile_skills(self):
        """The DB shape (skills as objects, not bare strings) must still match."""
        writer_output = _tailored(["Good Practice Environments"])

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result.skills == ["GxP Environments"]

    def test_single_mangled_entry_restored_in_place_no_reorder(self):
        writer_output = _tailored(["Python", "Good Practice Environments"])

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result.skills == ["Python", "GxP Environments"]


class TestConservativeNonMatching:
    def test_genuinely_different_skill_is_left_alone_and_logged(self, caplog):
        """A skill present in the tailored CV but genuinely absent from the vault
        (no unambiguous vault original) must survive untouched -- never deleted,
        never guessed at."""
        writer_output = _tailored(["Kubernetes Administration"])

        with caplog.at_level(logging.INFO):
            result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result.skills == ["Kubernetes Administration"]
        assert any(
            "Kubernetes Administration" in r.message for r in caplog.records
        ), "an unmatched skill must be logged, not silently dropped"

    def test_ambiguous_correspondence_is_left_alone(self):
        """Two vault skills that both plausibly correspond to the same mangled name
        must never be guessed between -- leave the entry exactly as the writer left it."""
        profile = {
            "skills": [
                {"name": "GxP Environments", "category": "technical"},
                {"name": "GMP Environments", "category": "technical"},
            ]
        }
        # "Good Practice Environments" is one acronym-residual token away from BOTH
        # vault entries -- ambiguous, must not be restored to either.
        writer_output = _tailored(["Good Practice Environments"])

        result = _restore_skill_spelling(writer_output, profile)

        assert result.skills == ["Good Practice Environments"]

    def test_legitimately_dropped_vault_skill_stays_dropped(self):
        """The tailoring step may select a SUBSET of vault skills. A vault skill the
        writer chose not to include must never be re-added by this guard -- it only
        restores the spelling of an entry that is already there."""
        writer_output = _tailored(["Python"])  # the 3 GxP skills were dropped on purpose

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result.skills == ["Python"]
        for original in _VAULT_SKILLS[:-1]:
            assert original not in result.skills

    def test_exact_vault_match_is_a_no_op(self):
        """Pure function, no-op guard: nothing to restore returns the SAME object,
        mirroring the sibling deterministic CV passes' no-op contract."""
        writer_output = _tailored(["Python"])

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result is writer_output


class TestReorderingAndSubsettingPreserved:
    def test_selection_and_order_from_upstream_passes_is_untouched(self):
        """The guard runs after skill selection/capping/reordering -- it must not
        fight that, only rewrite a spelling in place."""
        writer_output = _tailored(
            ["Good Practice Environments", "Python", "Good Practice Compliance & Computer System Validation"]
        )

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert result.skills == [
            "GxP Environments",
            "Python",
            "GxP Compliance & Computer System Validation",
        ]
        assert len(result.skills) == 3  # no entry added or removed


class TestMalformedInputTolerated:
    def test_none_profile_json(self):
        writer_output = _tailored(["Good Practice Environments"])
        result = _restore_skill_spelling(writer_output, None)
        assert result.skills == ["Good Practice Environments"]

    def test_profile_with_no_skills_key(self):
        writer_output = _tailored(["Good Practice Environments"])
        result = _restore_skill_spelling(writer_output, {})
        assert result.skills == ["Good Practice Environments"]

    def test_malformed_profile_skill_entries_are_skipped(self):
        profile = {"skills": [None, 42, {"no_name_key": "oops"}, {"name": ""}, {"name": "   "}]}
        writer_output = _tailored(["Good Practice Environments"])
        result = _restore_skill_spelling(writer_output, profile)
        assert result.skills == ["Good Practice Environments"]

    def test_tailored_with_no_skills(self):
        writer_output = _tailored([])
        result = _restore_skill_spelling(writer_output, _PROFILE)
        assert result.skills == []

    def test_tailored_skills_with_blank_entries_are_left_in_place(self):
        """Blank/whitespace-only tags are schema-valid strings (pydantic already
        rejects genuinely non-string entries) -- the guard must not choke on them
        and must not invent a restoration for blank text."""
        writer_output = _tailored(["Python", "  ", "Good Practice Environments"])

        result = _restore_skill_spelling(writer_output, _PROFILE)

        assert "GxP Environments" in result.skills
        assert "Python" in result.skills
        assert "  " in result.skills
