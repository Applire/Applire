# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-UAT-1 (founder ruling UAT-2 point 4, 2026-11-08): a Category B
confirmation chip may name its OWN cluster's constituent concept although the
profile states it only in other words; a Category C chip claiming a concept
``direct`` without literal evidence is still dropped. Synthetic data only."""

from applire.services.choice_grounding import filter_ungrounded_choices

PROFILE = {
    "skills": [{"name": "Shift coordination"}],
    "work_experience": [
        {
            "id": "w1",
            "company": "Harbourview Clinic",
            "role": "Ward Nurse",
            "technologies": [],
            "responsibilities": ["Coordinated night shifts for a 20-bed ward."],
            "achievements": [],
        }
    ],
}
CLUSTER = {"id": "c1", "label": "Emergency Response", "gaps": ["Triage"], "jd_skills": [], "jd_context": ""}
CHIP = {"text": "As a ward nurse I did triage of incoming patients on night shifts.", "level": "direct", "denied_terms": []}


def test_b_confirmation_naming_its_own_concept_survives():
    assert filter_ungrounded_choices([CHIP], CLUSTER, PROFILE, "B") == [CHIP["text"]]


def test_b_partial_naming_its_own_concept_survives():
    chip = {**CHIP, "level": "partial"}
    assert filter_ungrounded_choices([chip], CLUSTER, PROFILE, "B") == [chip["text"]]


def test_c_direct_claim_without_literal_evidence_is_still_dropped():
    assert filter_ungrounded_choices([CHIP], CLUSTER, PROFILE, "C") is None


def test_b_exemption_does_not_cover_a_foreign_jd_skill():
    cluster = {**CLUSTER, "jd_skills": ["ECMO"]}
    chip = {**CHIP, "text": "As a ward nurse I did triage and ran ECMO circuits on night shifts."}
    assert filter_ungrounded_choices([chip], cluster, PROFILE, "B") is None
