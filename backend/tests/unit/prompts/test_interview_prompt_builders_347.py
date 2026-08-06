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

"""ADR-064 clause 6 amendment (2026-08-05, #347) — prompt-builder behaviour.

The user prompt's deterministic parts must state evidence FACTS computed
against the CURRENT profile (ADR-062): the Category-C gap-type hint may not
assert "no signal was found" when the reconciled profile carries a
constituent as a skill (charter run 2026-07-29, record 30), and the choices
hint names the unevidenced constituents — the only valid scope for a denial
choice. The transfer probe's profile summary must carry real per-role
evidence, or the coverage rules' conditions are unsatisfiable there.
"""

from applire.prompts.interview import (
    build_denial_probe_question_prompt,
    build_question_prompt,
)

_PROFILE = {
    "skills": [{"name": "Industrie 4.0"}, {"name": "SMED"}],
    "work_experience": [
        {
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "technologies": ["MES"],
            "responsibilities": ["MES-Einfuehrung an 14 Spritzgussmaschinen"],
            "achievements": ["OEE von 61 auf 73 % gesteigert"],
        }
    ],
}


def _cluster(gaps):
    return {
        "id": "digital-transformation",
        "label": "Digital Transformation",
        "gaps": gaps,
        "jd_skills": [],
        "jd_context": "",
    }


class TestGapTypeHintRecompute:
    def test_evidenced_constituent_is_stated_not_denied(self):
        # Record 30's shape: the analysis-time snapshot said Category C, but
        # the current profile evidences one constituent.
        prompt = build_question_prompt(
            _cluster(["Digitalisierung", "Industrie 4.0"]), _PROFILE, [], gap_category="C"
        )
        assert "now evidences: Industrie 4.0" in prompt
        assert "Still unevidenced: Digitalisierung" in prompt
        assert "no signal for" not in prompt

    def test_fully_unevidenced_cluster_keeps_the_original_hint(self):
        prompt = build_question_prompt(
            _cluster(["IFS", "BRC"]), _PROFILE, [], gap_category="C"
        )
        assert "no signal for 'Digital Transformation' was found" in prompt

    def test_category_b_hint_is_unchanged(self):
        prompt = build_question_prompt(
            _cluster(["Industrie 4.0", "MES"]), _PROFILE, [], gap_category="B"
        )
        assert "CONFIRMATION (Category B)" in prompt


class TestChoicesHintDenialScope:
    def test_unevidenced_constituents_are_named_as_the_denial_scope(self):
        prompt = build_question_prompt(
            _cluster(["Digitalisierung", "Industrie 4.0"]), _PROFILE, [], gap_category="C"
        )
        assert "only valid scope for a denial choice: Digitalisierung" in prompt

    def test_all_evidenced_cluster_instructs_no_denial_choice(self):
        prompt = build_question_prompt(
            _cluster(["Industrie 4.0", "SMED"]), _PROFILE, [], gap_category="C"
        )
        assert "offer NO denial choice" in prompt

    def test_single_gap_cluster_still_sets_choices_null(self):
        prompt = build_question_prompt(
            _cluster(["Digitalisierung"]), _PROFILE, [], gap_category="C"
        )
        assert "Set choices to null." in prompt


class TestProbeSummaryCarriesEvidence:
    def test_probe_profile_summary_has_bullets_and_technologies(self):
        # Was bare company/role pairs — the evidence conditions were
        # unsatisfiable on the probe (#347).
        prompt = build_denial_probe_question_prompt(
            "Quality and Safety Standards",
            "The candidate DENIED ISO 45001; probe the broader area.",
            _PROFILE,
            [],
        )
        assert "MES-Einfuehrung an 14 Spritzgussmaschinen" in prompt
        assert "OEE von 61 auf 73 % gesteigert" in prompt
        assert "MES" in prompt
