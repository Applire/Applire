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

"""E042 / US237 — deterministic per-role bullet budgets (ADR-051 §3 + amendment §5/§6).

Pure-function unit tests: tier assignment matrix, target-page scaling, recency
boundaries, hit counting via the shared ATS presence predicate, the cross-language
all-zero-hits fallback, the empty-ledger fallback, and deterministic ``today``
injection. No Docker, no LLM.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_budget import (
    attach_projects,
    compute_bullet_budgets,
    render_budget_table,
    role_budget_line,
)

_TODAY = date(2026, 7, 15)


def _work(id_, start, end=None, is_current=None, bullets=None, projects=None):
    return {
        "id": id_,
        "company": f"Co {id_}",
        "role": f"Role {id_}",
        "start_date": start,
        "end_date": end,
        "is_current": is_current,
        "bullets": bullets or [f"did {id_} things"],
        "projects": projects or [],
    }


def _ledger_entry(concept, forms=None, claimable=True):
    return {
        "concept": concept,
        "surface_forms": forms or [concept],
        "claimable": claimable,
        "status": "direct" if claimable else "gap",
    }


# ---------------------------------------------------------------------------
# Target-page scaling (ADR-051 §3 + amendment §6)
# ---------------------------------------------------------------------------


def test_base_tier_ceilings_at_dach_standard_target():
    budget = compute_bullet_budgets([], None, target_pages=2, today=_TODAY)
    assert budget.tiers["top"].max_bullets == 5
    assert budget.tiers["top"].min_bullets == 4
    assert budget.tiers["mid"].max_bullets == 3
    assert budget.tiers["mid"].min_bullets == 2
    assert budget.tiers["bottom"].max_bullets == 1
    assert budget.tiers["bottom"].min_bullets == 0


def test_each_full_page_above_standard_raises_every_ceiling_by_one():
    budget = compute_bullet_budgets([], None, target_pages=3, today=_TODAY)
    assert budget.tiers["top"].max_bullets == 6
    assert budget.tiers["mid"].max_bullets == 4
    assert budget.tiers["bottom"].max_bullets == 2


def test_two_pages_above_standard_raises_every_ceiling_by_two():
    budget = compute_bullet_budgets([], None, target_pages=4, today=_TODAY)
    assert budget.tiers["top"].max_bullets == 7
    assert budget.tiers["mid"].max_bullets == 5
    assert budget.tiers["bottom"].max_bullets == 3


def test_target_below_standard_lowers_ceilings_floored():
    budget = compute_bullet_budgets([], None, target_pages=1, today=_TODAY)
    assert budget.tiers["top"].max_bullets == 4   # floored at 1, 5-1=4 > floor
    assert budget.tiers["mid"].max_bullets == 2
    assert budget.tiers["bottom"].max_bullets == 0


# ---------------------------------------------------------------------------
# Recency boundaries
# ---------------------------------------------------------------------------


def test_current_role_is_current_flag_is_recent_top_tier_with_no_ledger():
    entries = [_work("w1", "2024-01", end=None, is_current=True)]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_no_end_date_with_latest_start_counts_as_current():
    entries = [
        _work("old", "2010-01", end="2015-01"),
        _work("latest", "2024-06", end=None, is_current=None),
    ]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["latest"].tier == "top"


def test_role_ending_within_six_years_is_recent():
    entries = [_work("w1", "2018-01", end="2021-01")]  # ~5.5y ago from 2026-07
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_role_ending_between_six_and_twelve_years_is_mid():
    entries = [_work("w1", "2013-01", end="2017-01")]  # ~9.5y ago
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "mid"


def test_role_ending_over_twelve_years_ago_is_old():
    entries = [_work("w1", "2005-01", end="2008-01")]  # ~18.5y ago
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "bottom"


def test_unparseable_dates_fall_back_to_old_conservatively():
    entries = [_work("w1", "present", end="ongoing")]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "bottom"


# ---------------------------------------------------------------------------
# Tier assignment matrix (recency x relevance) via real hit counting
# ---------------------------------------------------------------------------


def test_recent_and_relevant_is_top():
    entries = [_work("w1", "2024-01", is_current=True, bullets=["Built a Kubernetes platform"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    # need >=2 hits for "relevant": add a second claimable term present in the text
    entries[0]["bullets"].append("Wrote Python services")
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_recent_and_neutral_is_top():
    entries = [_work("w1", "2024-01", is_current=True, bullets=["Built a Kubernetes platform"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Golang")]  # 1 hit only
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_recent_and_irrelevant_is_mid():
    # A second role scores a real hit so the all-zero cross-language fallback does
    # NOT trigger — w1's 0-hit "irrelevant" verdict below is the real tier-matrix path.
    entries = [
        _work("w1", "2024-01", is_current=True, bullets=["Managed office supplies"]),
        _work("w2", "2023-01", end="2023-06", bullets=["Built a Kubernetes platform"]),
    ]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "mid"


def test_mid_age_and_relevant_is_top():
    entries = [_work("w1", "2015-01", end="2016-01",
                      bullets=["Built a Kubernetes platform", "Wrote Python services"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_mid_age_and_neutral_is_mid():
    entries = [_work("w1", "2015-01", end="2016-01", bullets=["Built a Kubernetes platform"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Golang")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "mid"


def test_mid_age_and_irrelevant_is_mid():
    entries = [_work("w1", "2015-01", end="2016-01", bullets=["Managed office supplies"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "mid"


def test_old_and_relevant_is_mid():
    entries = [_work("w1", "2005-01", end="2006-01",
                      bullets=["Built a Kubernetes platform", "Wrote Python services"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "mid"


def test_old_and_neutral_is_bottom():
    entries = [_work("w1", "2005-01", end="2006-01", bullets=["Built a Kubernetes platform"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Golang")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "bottom"


def test_old_and_irrelevant_is_bottom():
    entries = [_work("w1", "2005-01", end="2006-01", bullets=["Managed office supplies"])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "bottom"


# ---------------------------------------------------------------------------
# Hit counting uses the shared ATS presence predicate (surface_forms + folding)
# ---------------------------------------------------------------------------


def test_hit_counting_matches_via_surface_forms_and_morphological_fold():
    # "code review" should match "Code Reviews" (fold) via a surface form, and
    # "CI/CD" should match "ci cd pipelines" (dash/slash -> space fold).
    entries = [_work("w1", "2024-01", is_current=True,
                      bullets=["Led code reviews", "Set up ci cd pipelines"])]
    ledger = [
        _ledger_entry("Code Review", forms=["Code Reviews", "code review"]),
        _ledger_entry("CI/CD", forms=["CI/CD"]),
    ]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"  # recent + relevant (2 hits)


def test_non_claimable_ledger_entries_never_count_as_hits():
    entries = [_work("w1", "2024-01", is_current=True, bullets=["Worked with Azure"])]
    ledger = [_ledger_entry("Azure", claimable=False), _ledger_entry("Python", claimable=False)]
    # both honest gaps -> void relevance (see below), recency-only -> top since current.
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_hit_counting_sees_attached_project_text():
    entries = [_work("w1", "2024-01", is_current=True, bullets=["General duties"],
                      projects=[{"name": "Migration", "responsibilities": ["Built a Kubernetes platform"],
                                 "achievements": ["Wrote Python services"]}])]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"  # 2 hits via project text


# ---------------------------------------------------------------------------
# Cross-language / empty-ledger fallback (amendment §5, binding)
# ---------------------------------------------------------------------------


def test_all_zero_hits_across_every_role_falls_back_to_recency_only():
    entries = [
        _work("recent", "2024-01", is_current=True, bullets=["Führte ein Team von Ingenieuren"]),
        _work("old", "2005-01", end="2006-01", bullets=["Verwaltete Büromaterial"]),
    ]
    # JD-language ledger terms never match the German profile text -> 0 hits everywhere.
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Leadership")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["recent"].tier == "top"     # recency-only: recent -> top
    assert budget.roles["old"].tier == "bottom"      # recency-only: old -> bottom


def test_empty_ledger_falls_back_to_recency_only():
    entries = [_work("w1", "2024-01", is_current=True, bullets=["Anything at all"])]
    budget = compute_bullet_budgets(entries, [], target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "top"


def test_none_ledger_falls_back_to_recency_only():
    entries = [_work("w1", "2005-01", end="2006-01", bullets=["Anything at all"])]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    assert budget.roles["w1"].tier == "bottom"


def test_not_every_role_zero_hits_disables_the_fallback():
    """One role scoring a hit is enough to keep relevance live for ALL roles —
    the fallback is global (void or not), not per-role."""
    entries = [
        _work("hit", "2024-01", is_current=True, bullets=["Built a Kubernetes platform",
                                                            "Wrote Python services"]),
        _work("miss", "2024-02", end="2024-06", bullets=["Unrelated admin work"]),
    ]
    ledger = [_ledger_entry("Kubernetes"), _ledger_entry("Python")]
    budget = compute_bullet_budgets(entries, ledger, target_pages=2, today=_TODAY)
    assert budget.roles["hit"].tier == "top"      # recent + relevant
    assert budget.roles["miss"].tier == "mid"      # recent + irrelevant (real 0-hit verdict, not fallback)


# ---------------------------------------------------------------------------
# Deterministic `today` injection
# ---------------------------------------------------------------------------


def test_today_parameter_is_deterministic_not_wall_clock():
    entries = [_work("w1", "2020-01", end="2020-06")]
    budget_now = compute_bullet_budgets(entries, None, target_pages=2, today=date(2020, 12, 1))
    budget_later = compute_bullet_budgets(entries, None, target_pages=2, today=date(2035, 1, 1))
    assert budget_now.roles["w1"].tier == "top"       # <1y ago from 2020-12
    assert budget_later.roles["w1"].tier == "bottom"  # >12y ago from 2035


# ---------------------------------------------------------------------------
# attach_projects — associates ProjectEntry rows with their parent work entry
# ---------------------------------------------------------------------------


def test_attach_projects_matches_by_id():
    work_entries = [{"id": "w1", "company": "Acme", "role": "Eng"}]
    projects = [{"name": "P1", "associated_experience": "w1", "description": "x"}]
    enriched = attach_projects(work_entries, projects)
    assert enriched[0]["projects"] == [projects[0]]
    # input untouched
    assert "projects" not in work_entries[0]


def test_attach_projects_matches_by_company_name():
    work_entries = [{"id": "w1", "company": "Acme Corp", "role": "Eng"}]
    projects = [{"name": "P1", "associated_experience": "Acme Corp"}]
    enriched = attach_projects(work_entries, projects)
    assert enriched[0]["projects"] == [projects[0]]


def test_attach_projects_leaves_unassociated_projects_out():
    work_entries = [{"id": "w1", "company": "Acme", "role": "Eng"}]
    projects = [{"name": "Standalone", "associated_experience": None}]
    enriched = attach_projects(work_entries, projects)
    assert enriched[0]["projects"] == []


# ---------------------------------------------------------------------------
# Prompt-block rendering
# ---------------------------------------------------------------------------


def test_render_budget_table_lists_every_role_with_max_bullets():
    entries = [_work("w1", "2024-01", is_current=True), _work("w2", "2010-01", end="2011-01")]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    text = render_budget_table(budget)
    assert "ROLE BULLET BUDGETS" in text
    assert "[w1]" in text and "Co w1" in text and "Role w1" in text
    assert "[w2]" in text
    assert f"max {budget.roles['w1'].max_bullets} bullet" in text
    assert f"max {budget.roles['w2'].max_bullets} bullet" in text


def test_render_budget_table_empty_roles_is_empty_string():
    budget = compute_bullet_budgets([], None, target_pages=2, today=_TODAY)
    assert render_budget_table(budget) == ""


def test_role_budget_line_renders_single_role_constraint():
    entries = [_work("w1", "2024-01", is_current=True)]
    budget = compute_bullet_budgets(entries, None, target_pages=2, today=_TODAY)
    line = role_budget_line(budget, "w1")
    assert "MAX BULLETS FOR THIS ENTRY" in line
    assert str(budget.roles["w1"].max_bullets) in line


def test_role_budget_line_unknown_id_is_empty_string():
    budget = compute_bullet_budgets([], None, target_pages=2, today=_TODAY)
    assert role_budget_line(budget, "nope") == ""
