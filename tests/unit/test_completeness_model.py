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

from applire.services.profile import completeness as C

LEAD = {"role": "Team Lead", "company": "Acme", "expected_fields": ["team_size", "budget_managed"],
        "start_date": "2020-01", "end_date": "2023-01", "achievements": ["Shipped X"],
        "team_size": 5, "budget_managed": "EUR 2M"}
IC = {"role": "Junior Developer", "company": "Acme", "expected_fields": [],
      "start_date": "2021-01", "end_date": None, "achievements": []}


def test_expected_fields_floor_plus_conditional():
    assert C.expected_fields_for(LEAD) == ["start_date", "end_date", "achievements", "team_size", "budget_managed"]


def test_no_annotation_falls_back_to_floor():
    assert C.expected_fields_for({"role": "X"}) == list(C.FLOOR_FIELDS)


def test_annotation_filtered_to_conditional():
    assert C.expected_fields_for({"role": "X", "expected_fields": ["team_size", "bogus"]}) == ["start_date", "end_date", "achievements", "team_size"]


def test_ic_not_asked_budget_or_team():
    gaps = C.field_gaps({"work_experience": [IC]})
    assert not any(g.startswith("team_size") or g.startswith("budget_managed") for g in gaps)
    assert "end_date: Junior Developer @ Acme" in gaps   # floor still applies (end_date None)
    assert "achievements: Junior Developer @ Acme" in gaps


def test_lead_gap_only_missing_conditional():
    lead_missing = {**LEAD, "team_size": None}
    gaps = C.field_gaps({"work_experience": [lead_missing]})
    assert "team_size: Team Lead @ Acme" in gaps
    assert not any(g.startswith("budget_managed") for g in gaps)  # present → no gap


def test_presence_always_counts_even_off_role():
    ic_with_budget = {**IC, "budget_managed": "EUR 10k"}
    assert C.field_present(ic_with_budget, "budget_managed") is True


def test_na_fields_suppressed():
    p = {"work_experience": [IC], "_meta": {"na_fields": ["end_date: Junior Developer @ Acme"]}}
    assert "end_date: Junior Developer @ Acme" not in C.field_gaps(p)


def test_fully_complete_lead_has_no_entry_gaps_and_full_richness():
    assert C.work_experience_richness([LEAD]) == 1.0
    assert not [g for g in C.field_gaps({"work_experience": [LEAD]}) if "@ Acme" in g]


def test_richness_empty_list_is_zero():
    assert C.work_experience_richness([]) == 0.0


def test_scope_limits_to_one_entry():
    p = {"work_experience": [IC, {**LEAD, "company": "Beta", "team_size": None}]}
    gaps = C.field_gaps(p, scope="work_experience:Beta:Team Lead")
    assert all("Beta" in g for g in gaps)
    assert any(g.startswith("team_size") for g in gaps)


def test_professional_summary_tail_when_no_scope():
    # match gap_detector_mode_c: professional_summary appended when scope None and work exists
    gaps = C.field_gaps({"work_experience": [IC]})
    assert "professional_summary" in gaps[-1]
