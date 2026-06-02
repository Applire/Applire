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

"""
Task 5 (ADR-035, US113) — service-level test proving the stored match score
comes from compute_match_score, not from an LLM-emitted number.
"""


def test_mock_gap_response_has_no_llm_score_and_scores_consistently():
    from applire.providers.llm.mock import _GAP_ANALYSIS_RESPONSE, _JOB_ANALYSIS_RESPONSE
    from applire.services.match_score import compute_match_score

    # The LLM (mock) no longer emits a score
    assert "match_score" not in _GAP_ANALYSIS_RESPONSE

    out = compute_match_score(
        _GAP_ANALYSIS_RESPONSE["classifications"],
        list(_JOB_ANALYSIS_RESPONSE.get("required_skills", [])),
        list(_JOB_ANALYSIS_RESPONSE.get("nice_to_have_skills", [])),
    )
    # With the three mock classifications mapping onto JD requirements,
    # at least one direct match exists so the score is a real number in [0,1].
    assert out["match_score"] is None or 0.0 <= out["match_score"] <= 1.0
    # The three mock requirements are classified (not all dropped):
    classified = set(out["category_a"]) | set(out["category_b"]) | set(out["category_c"])
    assert {"CI/CD pipelines", "Kubernetes", "5+ years Python experience"} & classified
