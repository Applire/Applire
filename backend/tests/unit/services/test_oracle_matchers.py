# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — table-driven tests for the deterministic Oracle matchers."""
import pytest

from applire.services.oracle.matchers import (
    build_vault_index,
    extract_figures,
    ground_skill_claim,
    ground_text_claim,
    match_figures,
)


# ── figures ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Reduced effort by 70%.", [("percent", "70")]),
        ("targets a ~70% reduction", [("percent", "70")]),
        ("cut costs by 12,5 %", [("percent", "12.5")]),
        ("saved €1.2M annually", [("currency", "1.2m")]),
        ("budget of 500k €", [("currency", "500k")]),
        ("managed a $2 Mio budget", [("currency", "2m")]),
        ("from 2019 to 2023", [("year", "2019"), ("year", "2023")]),
        ("migrated 200 users", [("number", "200")]),
        ("supported 1.000 clients", [("number", "1000")]),
        ("supported 1,000 clients", [("number", "1000")]),
        ("led a team of 12", [("number", "12")]),
        # single digits sit below the signal floor (no false red flags)
        ("led a team of 5", []),
        ("no figures here", []),
        # a percent span is consumed once, not double-counted as a number
        ("improved by 40% overall", [("percent", "40")]),
    ],
)
def test_extract_figures(text, expected):
    got = [(f.kind, f.value) for f in extract_figures(text)]
    assert got == expected


# ── vault index ───────────────────────────────────────────────────────────────

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"en": "IT leader focused on compliance automation."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Acme GmbH",
            "role": "Head of IT",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "responsibilities": ["Led a team of 12 engineers."],
            "achievements": [
                "Rollout of a compliance workflow that targets a ~70% reduction in manual effort.",
                "Reduced deployment time by 40% through CI automation.",
            ],
            "technologies": ["Python", "Kubernetes"],
        }
    ],
    "skills": [{"name": "Python"}, {"name": "Kubernetes"}],
    "metadata": {
        "enrichment_history": [
            {
                "id": "rec-1",
                "timestamp": "2026-07-01T10:00:00Z",
                "source": "interview",
                "changes": [
                    {
                        "section": "work_experience",
                        "field": "achievements",
                        "action": "added",
                        "new_value": (
                            "Rollout of a compliance workflow that targets a ~70% "
                            "reduction in manual effort."
                        ),
                    }
                ],
            }
        ]
    },
}


def test_vault_index_units_and_figure_map():
    index = build_vault_index(PROFILE)
    paths = {u.path for u in index.units}
    assert "work_experience[0].achievements[0]" in paths
    assert "work_experience[0].dates" in paths
    assert "skills[0]" in paths
    assert ("percent", "70") in index.figure_map
    assert ("percent", "40") in index.figure_map
    assert ("year", "2019") in index.figure_map
    assert ("number", "12") in index.figure_map


def test_vault_index_attaches_adr046_receipts():
    index = build_vault_index(PROFILE)
    target_unit = next(
        u for u in index.units if u.path == "work_experience[0].achievements[0]"
    )
    assert target_unit.receipt_ids == ["rec-1"]
    other = next(u for u in index.units if u.path == "work_experience[0].achievements[1]")
    assert other.receipt_ids == []


# ── figure matching ───────────────────────────────────────────────────────────

def test_match_figures_unmatched_is_red_flag_material():
    index = build_vault_index(PROFILE)
    result = match_figures(extract_figures("Improved satisfaction by 55%."), index)
    assert [f.value for f in result.unmatched] == ["55"]
    assert result.matched == []

    result = match_figures(extract_figures("Reduced effort by 70%."), index)
    assert result.unmatched == []
    assert result.matched[0][1][0].path == "work_experience[0].achievements[0]"


# ── grounding (shared predicate) ─────────────────────────────────────────────

def test_ground_skill_claim_surface_and_near_dupe():
    index = build_vault_index(PROFILE)
    assert ground_skill_claim("Python", index) is not None
    assert ground_skill_claim("kubernetes", index) is not None  # case-folded
    assert ground_skill_claim("React Native", index) is None


def test_ground_text_claim_coverage():
    index = build_vault_index(PROFILE)
    close = ground_text_claim("Cut deployment time through CI automation.", index)
    assert close.best_unit is not None
    assert close.best_unit.path == "work_experience[0].achievements[1]"
    assert close.best_coverage >= 0.6

    far = ground_text_claim("Owned vendor negotiations across three continents.", index)
    assert far.best_coverage < 0.6
