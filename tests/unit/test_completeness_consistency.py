# Copyright (C) 2024 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

from applire.schemas.profile import MasterProfileData
from applire.services.interview_graph import gap_detector_mode_c


def test_score_drops_for_empty_expected_fields():
    rich = MasterProfileData.model_validate({"work_experience": [
        {"role": "Lead", "company": "Acme", "expected_fields": ["team_size"],
         "start_date": "2020", "end_date": "2023", "achievements": ["X"], "team_size": 5}]})
    sparse = MasterProfileData.model_validate({"work_experience": [
        {"role": "Lead", "company": "Acme", "expected_fields": ["team_size"],
         "start_date": "2020", "end_date": None, "achievements": [], "team_size": None}]})
    assert rich.calculate_completeness() > sparse.calculate_completeness()


def test_gap_detector_delegates_to_unified_field_gaps():
    blob = {"work_experience": [
        {"role": "Dev", "company": "Acme", "expected_fields": [],
         "start_date": "2021", "end_date": None, "achievements": []}]}
    gaps = [g for g in gap_detector_mode_c(blob) if "@ Acme" in g]
    assert any(g.startswith("end_date") for g in gaps)
    assert any(g.startswith("achievements") for g in gaps)
    assert not any(g.startswith("team_size") or g.startswith("budget_managed") for g in gaps)  # not expected for IC


def test_presence_only_no_longer_inflates():
    # an entry that merely exists but is empty must NOT score the full work weight
    bare = MasterProfileData.model_validate({"work_experience": [
        {"role": "Dev", "company": "Acme", "expected_fields": []}]})
    # work_experience weight is 0.30; empty floor (start/end/achievements all missing) → 0 richness
    assert bare.calculate_completeness() < 0.30
