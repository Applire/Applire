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

"""ADR-069 clause 4 — the deterministic level guard for the JD review loop.

The pinned failure is charter run 12 (operations_marcus_de, LLM log
2026-07-31 18:05:15–18:05:25): across correction rounds "SAP" — required by
the posting ("Sicherer Umgang mit SAP") — was silently demoted to
nice_to_have_skills and the qualifier concepts were deleted. The guard
reverts level moves the corrector performed but did not declare in
``level_changes``; declared moves stand.
"""

from applire.services.jd_level_guard import apply_jd_level_guard


def _draft(required, nice, level_changes=None):
    d = {
        "required_skills": list(required),
        "nice_to_have_skills": list(nice),
        "keywords": ["Produktionsmanagement"],
    }
    if level_changes is not None:
        d["level_changes"] = level_changes
    return d


class TestUndeclaredMovesAreReverted:
    def test_run12_shape_undeclared_demotion_is_reverted(self):
        # Round 0: SAP extracted as required. Round 1: corrector parks bare
        # SAP in nice_to_have with no declaration — the run-12 approved state.
        initial = _draft(["SAP", "MES"], ["5S"])
        settled = _draft(["MES"], ["5S", "SAP"])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "SAP" in result["required_skills"]
        assert "SAP" not in result["nice_to_have_skills"]

    def test_undeclared_promotion_is_reverted_symmetrically(self):
        initial = _draft(["MES"], ["5S"])
        settled = _draft(["MES", "5S"], [])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "5S" not in result["required_skills"]
        assert "5S" in result["nice_to_have_skills"]

    def test_norm_matching_is_case_and_whitespace_folded(self):
        initial = _draft(["SAP  PP"], [])
        settled = _draft([], ["sap pp"])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert result["required_skills"] == ["sap pp"]
        assert result["nice_to_have_skills"] == []


class TestDeclaredMovesStand:
    def test_declared_demotion_stands(self):
        initial = _draft(["Englisch"], [])
        settled = _draft(
            [], ["Englisch"], level_changes=[{"concept": "Englisch", "to": "nice_to_have"}]
        )
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "Englisch" in result["nice_to_have_skills"]
        assert "Englisch" not in result["required_skills"]

    def test_declaration_must_match_the_actual_move(self):
        # Declaring a move to "required" does not authorise a demotion.
        initial = _draft(["SAP"], [])
        settled = _draft(
            [], ["SAP"], level_changes=[{"concept": "SAP", "to": "required"}]
        )
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "SAP" in result["required_skills"]

    def test_multi_round_declared_move_survives_a_later_silent_round(self):
        # Round 1 declares the demotion; round 2 changes something else and
        # declares nothing — the earlier authorised level must persist.
        r0 = _draft(["Englisch", "SAP"], [])
        r1 = _draft(
            ["SAP"], ["Englisch"], level_changes=[{"concept": "Englisch", "to": "nice_to_have"}]
        )
        r2 = _draft(["SAP", "MES"], ["Englisch"], level_changes=[])
        result = apply_jd_level_guard(r2, [r0, r1, r2])
        assert "Englisch" in result["nice_to_have_skills"]
        assert "MES" in result["required_skills"]


class TestBoundaries:
    def test_remove_then_readd_at_a_new_level_is_content_not_a_move(self):
        """2026-08-01 adversarial pass finding #4: a concept removed as
        fabricated in round 1 and correctly re-added at nice_to_have in round
        2 was misread as an undeclared move and forced back to its
        pre-removal level — the guard reproducing, in reverse, the harm it
        exists to prevent. Removal must invalidate the concept's authority."""
        r0 = _draft(["SAP PP"], [])
        r1 = _draft([], [])
        r2 = _draft([], ["SAP PP"], level_changes=[])
        result = apply_jd_level_guard(r2, [r0, r1, r2])
        assert "SAP PP" in result["nice_to_have_skills"]
        assert "SAP PP" not in result["required_skills"]

    def test_removals_are_never_resurrected(self):
        # The reviewer's fabrication removal is legitimate — the guard only
        # polices LEVEL moves, never absence.
        initial = _draft(["Qualitätsmanagement", "SAP"], [])
        settled = _draft(["SAP"], [])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "Qualitätsmanagement" not in result["required_skills"]
        assert "Qualitätsmanagement" not in result["nice_to_have_skills"]

    def test_new_concepts_adopt_their_placement(self):
        initial = _draft(["SAP"], [])
        settled = _draft(["SAP"], ["SAP PP", "SAP MM"])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "SAP PP" in result["nice_to_have_skills"]
        assert "SAP MM" in result["nice_to_have_skills"]

    def test_transport_field_is_stripped(self):
        initial = _draft(["SAP"], [])
        settled = _draft(["SAP"], [], level_changes=[])
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "level_changes" not in result

    def test_single_draft_history_is_a_noop(self):
        only = _draft(["SAP"], ["5S"], level_changes=[])
        result = apply_jd_level_guard(only, [only])
        assert result["required_skills"] == ["SAP"]
        assert "level_changes" not in result

    def test_empty_history_passes_through(self):
        settled = _draft(["SAP"], [])
        assert apply_jd_level_guard(settled, []) == settled

    def test_malformed_level_changes_entries_are_ignored(self):
        initial = _draft(["SAP"], [])
        settled = _draft(
            [],
            ["SAP"],
            level_changes=["SAP", {"concept": "SAP"}, {"to": "nice_to_have"}, 42],
        )
        result = apply_jd_level_guard(settled, [initial, settled])
        assert "SAP" in result["required_skills"]
