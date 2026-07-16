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

"""E042 Task 1.3 (US238, ADR-051 §4 + amendment §6): the deterministic, omission-only
``condense_to_budget`` pass. Pure — no DB/LLM/I/O, never mutates its input."""
import copy
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_budget import (  # noqa: E402
    BudgetResult,
    BulletTier,
    RoleBudget,
    condense_to_budget,
)


def _budget(role_ceilings: dict[str, int], claimable_forms=()) -> BudgetResult:
    roles = {
        eid: RoleBudget(work_entry_id=eid, tier="mid", max_bullets=c)
        for eid, c in role_ceilings.items()
    }
    tiers = {
        "top": BulletTier("top", 5, 4),
        "mid": BulletTier("mid", 3, 2),
        "bottom": BulletTier("bottom", 1, 0),
    }
    return BudgetResult(
        roles=roles, tiers=tiers, target_pages=2, region="DACH",
        claimable_forms=tuple(claimable_forms),
    )


def _cv(work_history) -> dict:
    return {"contact": {"name": "X"}, "work_history": work_history, "skills": []}


def _all_bullets(data: dict) -> set[str]:
    out: set[str] = set()
    for w in data.get("work_history", []):
        out.update(w.get("bullets") or [])
        for p in w.get("projects") or []:
            out.update(p.get("bullets") or [])
    return out


# --- determinism -----------------------------------------------------------

def test_condense_is_deterministic():
    data = _cv([{"id": "r1", "bullets": ["a", "b", "c", "d"]}])
    budget = _budget({"r1": 2})
    out1, ch1 = condense_to_budget(data, budget, 1)
    out2, ch2 = condense_to_budget(data, budget, 1)
    assert out1 == out2 and ch1 == ch2 is True


def test_condense_never_mutates_input():
    data = _cv([{"id": "r1", "bullets": ["a", "b", "c"],
                 "projects": [{"name": "P", "bullets": ["x", "y"]}]}])
    snapshot = copy.deepcopy(data)
    condense_to_budget(data, _budget({"r1": 1}), 1)
    assert data == snapshot


# --- cut order -------------------------------------------------------------

def test_no_hit_bullets_cut_before_hit_bullets():
    data = _cv([{"id": "r1", "bullets": [
        "Led the Python migration", "Filed weekly reports", "Watered the plants",
    ]}])
    out, changed = condense_to_budget(data, _budget({"r1": 1}, claimable_forms=["Python"]), 1)
    assert changed
    assert out["work_history"][0]["bullets"] == ["Led the Python migration"]


def test_project_bullets_cut_before_role_bullets():
    data = _cv([{"id": "r1", "bullets": ["Managed the team"],
                 "projects": [{"name": "Atlas", "bullets": ["Built the widget"]}]}])
    out, _ = condense_to_budget(data, _budget({"r1": 1}), 1)
    assert out["work_history"][0]["bullets"] == ["Managed the team"]
    # project lost its only bullet → dropped (omission)
    assert out["work_history"][0]["projects"] == []


def test_full_cut_order_no_hit_project_role_then_hit():
    # ceiling 1 over 4 bullets: no-hit-project, no-hit-role, hit-project cut; hit-role kept.
    data = _cv([{"id": "r1",
                 "bullets": ["Administrative work", "Owned Python services"],
                 "projects": [{"name": "P", "bullets": ["Misc chores", "Shipped Python SDK"]}]}])
    out, _ = condense_to_budget(data, _budget({"r1": 1}, claimable_forms=["Python"]), 1)
    assert out["work_history"][0]["bullets"] == ["Owned Python services"]
    assert out["work_history"][0]["projects"] == []


def test_within_group_later_bullets_cut_first():
    data = _cv([{"id": "r1", "bullets": ["a", "b", "c"]}])
    out, _ = condense_to_budget(data, _budget({"r1": 2}), 1)
    assert out["work_history"][0]["bullets"] == ["a", "b"]


# --- iteration semantics ---------------------------------------------------

def test_iteration_two_lowers_every_ceiling_by_one():
    data = _cv([{"id": "r1", "bullets": ["a", "b", "c"]}])
    budget = _budget({"r1": 2})
    out1, _ = condense_to_budget(data, budget, 1)
    out2, _ = condense_to_budget(data, budget, 2)
    assert out1["work_history"][0]["bullets"] == ["a", "b"]
    assert out2["work_history"][0]["bullets"] == ["a"]


def test_iteration_two_floors_ceiling_at_zero():
    data = _cv([{"id": "r1", "bullets": ["a"]}])
    # bottom-tier ceiling 1 → iteration 2 lowers to 0, collapse to one-liner (empty)
    out, changed = condense_to_budget(data, _budget({"r1": 1}), 2)
    assert changed
    assert out["work_history"][0]["bullets"] == []


# --- invariants ------------------------------------------------------------

def test_roles_are_never_removed():
    data = _cv([
        {"id": "r1", "bullets": ["a", "b"]},
        {"id": "r2", "bullets": ["x"]},
    ])
    out, _ = condense_to_budget(data, _budget({"r1": 0, "r2": 0}), 1)
    assert len(out["work_history"]) == 2
    assert out["work_history"][0]["bullets"] == []
    assert out["work_history"][1]["bullets"] == []


def test_omission_only_every_survivor_is_verbatim_input_member():
    data = _cv([{"id": "r1", "bullets": ["Alpha", "Beta", "Gamma"],
                 "projects": [{"name": "P", "bullets": ["Delta", "Epsilon"]}]}])
    inputs = _all_bullets(data)
    out, _ = condense_to_budget(data, _budget({"r1": 2}, claimable_forms=["Beta"]), 1)
    assert _all_bullets(out) <= inputs


def test_no_change_early_stop():
    data = _cv([{"id": "r1", "bullets": ["a", "b"]}])
    out, changed = condense_to_budget(data, _budget({"r1": 5}), 1)
    assert changed is False
    assert out == data


def test_unknown_role_id_is_left_untouched():
    data = _cv([{"id": "not-in-budget", "bullets": ["a", "b", "c"]}])
    out, changed = condense_to_budget(data, _budget({"r1": 1}), 1)
    assert changed is False
    assert out["work_history"][0]["bullets"] == ["a", "b", "c"]


def test_void_relevance_treats_all_bullets_as_no_hit():
    # No claimable_forms → every bullet is a no-hit bullet; project cut before role.
    data = _cv([{"id": "r1", "bullets": ["Role work"],
                 "projects": [{"name": "P", "bullets": ["Project work"]}]}])
    out, _ = condense_to_budget(data, _budget({"r1": 1}, claimable_forms=[]), 1)
    assert out["work_history"][0]["bullets"] == ["Role work"]


def test_project_with_surviving_bullets_is_kept():
    data = _cv([{"id": "r1", "bullets": ["Role A"],
                 "projects": [{"name": "P", "bullets": ["P1", "P2", "P3"]}]}])
    # total 4, ceiling 3 → cut one project bullet, project stays.
    out, _ = condense_to_budget(data, _budget({"r1": 3}), 1)
    assert out["work_history"][0]["bullets"] == ["Role A"]
    assert out["work_history"][0]["projects"][0]["bullets"] == ["P1", "P2"]
