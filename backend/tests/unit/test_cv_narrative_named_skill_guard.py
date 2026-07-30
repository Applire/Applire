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

"""#376 -- a skill named in a generated bullet but missing from the generated
skills list.

Ground truth (ADR-064 charter run, section 4 finding F3, 2026-07-29): the
tailored CV's own work-experience bullet reads "Tägliche Arbeit mit SAP PP
und SAP MM (Disposition und Bestellanforderungen)" -- both module names
spelled out in full, no ellipsis. The Kenntnisse (skills) section lists only
``SAP PP``; ``SAP MM`` never appears in it, in the very document that names
it verbatim.

This is the STRUCTURAL half only (per the brief): for a name we already have
-- a vault ``Skill`` row, or a claimable Keyword Ledger concept/surface form
-- literal presence in a narrative bullet is a FACT (ADR-062 clause 1), and a
name known-true but missing from the skills list is added. This deliberately
does NOT resolve an elided compound ("SAP PP und MM" implying "SAP MM") --
that is a JUDGEMENT (reading what a sentence means), out of scope here and
left to the interview-testimony-seam LLM stance adjudication ADR-061 already
uses for that exact ellipsis shape.
"""

from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredWorkEntry
from applire.services.cv import _restore_narrative_named_skills


def _tailored(skills: list[str], bullets: list[str]) -> TailoredCVData:
    return TailoredCVData(
        contact=TailoredContact(name="Test Candidate"),
        skills=skills,
        work_history=[
            TailoredWorkEntry(
                id="w1", company="Rheinwerk", role="Leiter Operations",
                start_date="2018-01", end_date=None, bullets=bullets,
            )
        ],
    )


class TestSAPMMReportedCase:
    """#376's own reported shape, reproduced verbatim (synthetic candidate)."""

    _PROFILE = {
        "skills": [
            {"name": "SAP PP", "category": "technical"},
            {"name": "SAP MM", "category": "technical"},
        ]
    }
    _BULLET = (
        "Tägliche Arbeit mit SAP PP und SAP MM (Disposition und "
        "Bestellanforderungen)."
    )

    def test_sap_mm_is_added_to_the_skills_list(self):
        writer_output = _tailored(["SAP PP"], [self._BULLET])

        result = _restore_narrative_named_skills(writer_output, self._PROFILE, None)

        assert "SAP MM" in result.skills
        assert "SAP PP" in result.skills

    def test_pre_existing_skill_is_never_duplicated(self):
        writer_output = _tailored(["SAP PP"], [self._BULLET])

        result = _restore_narrative_named_skills(writer_output, self._PROFILE, None)

        assert result.skills.count("SAP PP") == 1

    def test_noop_when_both_already_listed(self):
        writer_output = _tailored(["SAP PP", "SAP MM"], [self._BULLET])

        result = _restore_narrative_named_skills(writer_output, self._PROFILE, None)

        assert result is writer_output

    def test_noop_when_bullet_never_names_the_second_module(self):
        """No elision resolution: a bullet reading only 'SAP PP und MM' (the
        elided cover-letter shape from the SAME issue) must NOT trigger the
        addition -- 'SAP MM' is not a literal substring of that sentence."""
        writer_output = _tailored(["SAP PP"], ["Täglich arbeite ich mit SAP PP und MM"])

        result = _restore_narrative_named_skills(writer_output, self._PROFILE, None)

        assert "SAP MM" not in result.skills


class TestClaimableLedgerSurfaceFormsAlsoQualify:
    """The brief allows either source of a known name: a vault Skill row, OR
    a claimable Keyword Ledger concept/surface form."""

    def test_claimable_ledger_concept_named_in_bullet_is_added(self):
        ledger = [{
            "concept": "Kubernetes", "surface_forms": ["Kubernetes", "K8s"],
            "claimable": True, "status": "direct", "sources": ["required"],
            "fit_weight": 1.0, "evidence": "vault evidence",
        }]
        writer_output = _tailored(
            [], ["Migrated the platform onto Kubernetes across three regions."]
        )

        result = _restore_narrative_named_skills(writer_output, {}, ledger)

        assert "Kubernetes" in result.skills

    def test_honest_gap_ledger_concept_is_never_added(self):
        """A non-claimable (honest-gap) ledger entry must never be surfaced as
        a skill, even if its bare name happens to appear in prose (e.g. inside
        a DO-NOT-CLAIM sentence) -- truthfulness floor, ADR-048."""
        ledger = [{
            "concept": "Azure", "surface_forms": ["Azure"],
            "claimable": False, "status": "gap", "sources": ["required"],
            "fit_weight": 1.0, "evidence": "",
        }]
        writer_output = _tailored([], ["No hands-on Azure experience yet."])

        result = _restore_narrative_named_skills(writer_output, {}, ledger)

        assert "Azure" not in result.skills


class TestConservativeGuardrails:
    def test_unconfirmed_vault_skill_never_added(self):
        """ADR-061 clause 3: an unconfirmed skill cannot back a CV line, even
        if a bullet happens to name it."""
        profile = {"skills": [{"name": "SAP MM", "status": "unconfirmed"}]}
        writer_output = _tailored(
            ["SAP PP"], ["Tägliche Arbeit mit SAP PP und SAP MM."]
        )

        result = _restore_narrative_named_skills(writer_output, profile, None)

        assert "SAP MM" not in result.skills

    def test_near_dupe_already_present_is_not_added_again(self):
        """A near-duplicate already on the list (different phrasing) must not
        produce a second entry for the same skill."""
        profile = {"skills": [{"name": "SAP Materials Management (SAP MM)"}]}
        writer_output = _tailored(
            ["SAP Materials Management (SAP MM)"],
            ["Tägliche Arbeit mit SAP PP und SAP MM."],
        )

        result = _restore_narrative_named_skills(writer_output, profile, None)

        assert result.skills == ["SAP Materials Management (SAP MM)"]

    def test_no_narrative_mention_never_adds_a_vault_skill(self):
        """This guard only ever corrects a document CONTRADICTING itself --
        it never re-adds a vault skill the writer simply chose to omit and
        never mentioned anywhere."""
        profile = {"skills": [{"name": "SAP MM"}]}
        writer_output = _tailored(["SAP PP"], ["Generic bullet with no ledger hit."])

        result = _restore_narrative_named_skills(writer_output, profile, None)

        assert "SAP MM" not in result.skills

    def test_pure_no_mutation_of_input(self):
        writer_output = _tailored(["SAP PP"], [TestSAPMMReportedCase._BULLET])
        before = list(writer_output.skills)

        _restore_narrative_named_skills(
            writer_output, TestSAPMMReportedCase._PROFILE, None
        )

        assert writer_output.skills == before

    def test_malformed_profile_and_ledger_tolerated(self):
        writer_output = _tailored(["SAP PP"], [TestSAPMMReportedCase._BULLET])
        result = _restore_narrative_named_skills(writer_output, None, None)
        assert result.skills == ["SAP PP"]
