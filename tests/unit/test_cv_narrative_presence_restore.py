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

"""#303 wired into ``_restore_ledger_bullets`` (#234's deterministic restore
guard) -- the path that actually changes the delivered document, no LLM
required.

Mirrors ``tests/unit/test_cv_ledger_bullet_guard.py::
TestRestoreLedgerBulletsProtectsLoadBearingClaims`` (the #315 precedent):
that class proved the guard restores a vault bullet for a LOAD-BEARING
concept even when a bare keyword already satisfies the whole-document check.
This is the sibling case: a HIGH-FIT concept with NO figure at all (so #315's
own guard, gated on ``is_load_bearing``, never fires for it either) must
still get its vault bullet restored once #303's predicate is unioned in.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _kubernetes_fixture():
    concept = "Kubernetes"
    vault_bullet = "Migrated the deployment pipeline onto Kubernetes across three regions."
    other_bullets = [
        "Owned the platform roadmap for the infrastructure team.",
        "Mentored two junior engineers on-call practices.",
    ]
    ledger = [
        {
            "concept": concept,
            "surface_forms": [concept, "K8s"],
            "claimable": True,
            "status": "direct",
            "sources": ["required"],
            "fit_weight": 1.0,
            # Deliberately NO figure -- the #315 load-bearing guard must NOT
            # be the mechanism that catches this; is_load_bearing(entry) is
            # False for this entry.
            "evidence": "Explicitly listed as a skill (Kubernetes, expert, 6 years).",
        }
    ]
    profile_json = {
        "work_experience": [
            {
                "id": "w1",
                "company": "Acme",
                "role": "Platform Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": other_bullets + [vault_bullet],
                "achievements": [],
            }
        ],
        "projects": [],
    }
    return profile_json, ledger, other_bullets, vault_bullet


class TestRestoreLedgerBulletsCoversNarrativePresence:
    def test_precondition_not_load_bearing(self):
        """Sanity check on the fixture itself: this concept must NOT be
        load-bearing, or the test would (wrongly) exercise #315's mechanism
        instead of #303's."""
        from applire.services.load_bearing import is_load_bearing

        _profile_json, ledger, _other, _vault = _kubernetes_fixture()
        assert is_load_bearing(ledger[0]) is False

    def test_restores_the_bullet_despite_bare_skills_tag_elsewhere(self):
        """The #303 bug, reproduced through the actual restoration guard: the
        skills list already carries 'Kubernetes' (satisfying
        verified_missing_claimable's whole-document scan), so pre-fix this
        guard finds nothing missing and never restores the narrative. Post-fix
        it must restore the vault bullet because the concept is high-fit
        claimable and absent from the NARRATIVE specifically."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets
        from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

        profile_json, ledger, other_bullets, vault_bullet = _kubernetes_fixture()
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Max"},
            "summary": "Platform engineer with deep infrastructure expertise.",
            "skills": ["Kubernetes"],
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Platform Engineer",
                "start_date": "2020-01", "end_date": None,
                "bullets": list(other_bullets),
            }],
        })
        budget = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={"top": BulletTier("top", 5, 4)}, target_pages=2, region="DACH",
            claimable_forms=("Kubernetes", "K8s"),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert vault_bullet in final_bullets, (
            "high-fit claimable concept's own narrative bullet was not restored "
            "despite the bare skills-list tag satisfying the whole-document check"
        )

    def test_does_not_restore_when_already_narrated(self):
        """No-op guardrail: once the concept genuinely appears in a bullet,
        nothing further is restored."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets
        from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

        profile_json, ledger, other_bullets, vault_bullet = _kubernetes_fixture()
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Max"},
            "summary": "Platform engineer.",
            "skills": ["Kubernetes"],
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Platform Engineer",
                "start_date": "2020-01", "end_date": None,
                "bullets": other_bullets + [vault_bullet],
            }],
        })
        budget = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={"top": BulletTier("top", 5, 4)}, target_pages=2, region="DACH",
            claimable_forms=("Kubernetes", "K8s"),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        assert result.work_history[0].bullets == other_bullets + [vault_bullet]
