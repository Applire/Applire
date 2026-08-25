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

"""ADR-077 clause 4 — the partition wired through EVERY rank_cuts caller.

Rule-against-one-of-N: `condense_to_budget`, `_cap_bullets` (via
`_restore_ledger_bullets`' cap branch) and the restore-overflow branch each
get their own firing test — a pin that the ranking would cut first survives
in all three.
"""

import uuid

from applire.schemas.application import FactPin
from applire.schemas.cv import TailoredCVData
from applire.services.cv_budget import (
    BudgetResult,
    BulletTier,
    RoleBudget,
    condense_to_budget,
)

PINNED_BULLET = "Verantwortung für den Sauberraumbereich seit 2021"


def _pin(entry_id: str, quote: str = PINNED_BULLET) -> FactPin:
    return FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="work",
        entry_id=entry_id,
        quote=quote,
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


# ── condense_to_budget ───────────────────────────────────────────────────────


def test_condense_cuts_the_pinned_bullet_without_the_pin():
    # Negative control: figure-less and last-listed — first to go.
    data = _cv([{"id": "r1", "bullets": [
        "Raised revenue by 40%", "Cut costs by 20%", PINNED_BULLET,
    ]}])
    out, changed = condense_to_budget(data, _budget({"r1": 2}), 1)
    assert changed and PINNED_BULLET not in out["work_history"][0]["bullets"]


def test_condense_never_cuts_a_pinned_bullet():
    data = _cv([{"id": "r1", "bullets": [
        "Raised revenue by 40%", "Cut costs by 20%", PINNED_BULLET,
    ]}])
    out, changed = condense_to_budget(
        data, _budget({"r1": 2}), 1, pins=[_pin("r1")]
    )
    assert changed
    bullets = out["work_history"][0]["bullets"]
    assert PINNED_BULLET in bullets and len(bullets) == 2


def test_condense_ceiling_yields_when_pins_alone_exceed_it():
    data = _cv([{"id": "r1", "bullets": [
        PINNED_BULLET, "Second pinned fact about quality audits", "Filler",
    ]}])
    pins = [
        _pin("r1"),
        _pin("r1", "Second pinned fact about quality audits"),
    ]
    out, changed = condense_to_budget(data, _budget({"r1": 1}), 1, pins=pins)
    assert changed
    bullets = out["work_history"][0]["bullets"]
    assert len(bullets) == 2  # ceiling 1 violated by design: both pins survive


def test_condense_pin_on_another_entry_protects_nothing():
    data = _cv([{"id": "r1", "bullets": [
        "Raised revenue by 40%", "Cut costs by 20%", PINNED_BULLET,
    ]}])
    out, _ = condense_to_budget(
        data, _budget({"r1": 2}), 1, pins=[_pin("r2")]
    )
    assert PINNED_BULLET not in out["work_history"][0]["bullets"]


# ── _restore_ledger_bullets (both branches) ──────────────────────────────────


def _tailored(bullets, entry_id="w1") -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [{
            "id": entry_id, "company": "Acme", "role": "Engineer",
            "start_date": "2020-01", "bullets": list(bullets),
        }],
        "skills": [],
    })


def _profile_json(responsibilities):
    return {
        "work_experience": [{
            "id": "w1", "company": "Acme", "role": "Engineer",
            "start_date": "2020-01", "responsibilities": list(responsibilities),
            "achievements": [],
        }],
        "projects": [],
    }


def test_cap_branch_spares_the_pinned_bullet():
    from applire.services.cv import _restore_ledger_bullets

    bullets = ["Raised revenue by 40%", "Cut costs by 20%", PINNED_BULLET]
    tailored = _tailored(bullets)
    # A ledger whose concept nothing carries: nothing restores, but the
    # ceiling is still over → the _cap_bullets branch fires.
    ledger = [{
        "concept": "Quantenphysik", "surface_forms": ["Quantenphysik"],
        "claimable": True, "status": "direct", "sources": ["required"],
        "fit_weight": 1.0, "evidence": "vault",
    }]
    result = _restore_ledger_bullets(
        tailored,
        _profile_json(bullets),
        ledger,
        _budget({"w1": 2}),
        pins=[_pin("w1")],
    )
    kept = result.work_history[0].bullets
    assert PINNED_BULLET in kept and len(kept) == 2


def test_restore_overflow_branch_spares_the_pinned_bullet():
    from applire.services.cv import _restore_ledger_bullets

    # The draft kept only the pinned (no-hit) bullet + filler; the vault holds
    # a claimable-concept bullet that gets restored. Ceiling 2 forces the
    # overflow ranking — without the pin, the no-hit pinned bullet is cut.
    ledger = [{
        "concept": "Kubernetes", "surface_forms": ["Kubernetes"],
        "claimable": True, "status": "direct", "sources": ["required"],
        "fit_weight": 1.0, "evidence": "vault",
    }]
    vault = ["Migrated the stack to Kubernetes", PINNED_BULLET]
    tailored = _tailored([PINNED_BULLET, "Filler bullet"])
    result = _restore_ledger_bullets(
        tailored,
        _profile_json(vault),
        ledger,
        _budget({"w1": 2}),
        pins=[_pin("w1")],
    )
    kept = result.work_history[0].bullets
    assert PINNED_BULLET in kept
    assert "Migrated the stack to Kubernetes" in kept


# ── _tailor_skills_to_jd (the skills-section cap — clause-4 pass inventory) ──


def test_pinned_skill_survives_the_skills_cap():
    from applire.services.cv import _tailor_skills_to_jd

    pin = FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="skill",
        entry_id="s1",
        quote="Sauberraumtechnik",
    )
    tailored = TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [],
        # JD-irrelevant pinned skill listed LAST — tier 3, cut first without the guard
        "skills": ["React", "Node.js", "Sauberraumtechnik"],
    })
    job_dict = {"required_skills": ["React", "Node.js"], "nice_to_have_skills": [], "keywords": []}
    profile_json = {"skills": [
        {"name": "React"}, {"name": "Node.js"}, {"name": "Sauberraumtechnik"},
    ]}
    out = _tailor_skills_to_jd(
        tailored, profile_json, job_dict, None, cap=2, pins=[pin]
    )
    assert "Sauberraumtechnik" in out.skills

    # negative control: without the pin the cap drops it
    out_unpinned = _tailor_skills_to_jd(
        tailored, profile_json, job_dict, None, cap=2
    )
    assert "Sauberraumtechnik" not in out_unpinned.skills
