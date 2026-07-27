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

"""#234 (Tiramisu founder-acceptance F1/F2) — deterministic ledger-aware bullet
retention guard.

Ground truth: a vault work entry had 9 responsibilities (5 interview-elicited,
mapping 1:1 to JD requirements); the tailored CV kept only the 4 generic
LinkedIn-baseline bullets the writer happened to prefer. The generic Keyword
Ledger prompt block (ADR-048) is not enough to guarantee any SPECIFIC bullet
survives selection — this guard is the deterministic, post-draft fix: restore
verbatim vault bullets that carry a claimable ledger concept the draft dropped
entirely, using THE shared presence predicate (ats_audit.surface_present, #122).

Pure function tests — no DB, no LLM.
"""

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _ledger_entry(concept: str) -> dict:
    return {
        "concept": concept,
        "surface_forms": [concept],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": f"vault evidence for {concept}",
    }


def _founder_fixture():
    """9 vault responsibilities on one role: 4 generic (no ledger hit) + 5
    JD-matching (each carries a distinct claimable concept). The tailored draft
    kept only the 4 generic bullets — the founder-acceptance shape (F1)."""
    generic = [f"Generic baseline bullet {i}" for i in range(1, 5)]
    hits = [f"Delivered Concept{i} work on the platform" for i in range(1, 6)]
    vault_responsibilities = generic + hits
    ledger = [_ledger_entry(f"Concept{i}") for i in range(1, 6)]

    profile_json = {
        "work_experience": [
            {
                "id": "w1",
                "company": "Acme",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": vault_responsibilities,
                "achievements": [],
            }
        ],
        "projects": [],
    }
    return profile_json, ledger, generic, hits


def _tailored_cv(bullets, *, entry_id="w1", extra_entries=None):
    from applire.schemas.cv import TailoredCVData

    work_history = [
        {
            "id": entry_id,
            "company": "Acme",
            "role": "Engineer",
            "start_date": "2020-01",
            "end_date": None,
            "bullets": list(bullets),
        }
    ]
    if extra_entries:
        work_history.extend(extra_entries)
    return TailoredCVData.model_validate({
        "contact": {"name": "Max"},
        "summary": "Engineer.",
        "work_history": work_history,
        "skills": [],
    })


def _budget(max_bullets: int, *, entry_id="w1", claimable_forms=()):
    from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

    tiers = {
        "top": BulletTier("top", max_bullets, max(0, max_bullets - 1)),
        "mid": BulletTier("mid", 3, 2),
        "bottom": BulletTier("bottom", 1, 0),
    }
    return BudgetResult(
        roles={entry_id: RoleBudget(work_entry_id=entry_id, tier="top", max_bullets=max_bullets)},
        tiers=tiers, target_pages=2, region="DACH", claimable_forms=tuple(claimable_forms),
    )


class TestRestoreLedgerBullets:
    def test_restores_dropped_hit_bullets_within_budget(self):
        """The founder-acceptance shape: budget ceiling = 5, 5 hit bullets available ->
        the guard fills the entry with exactly the 5 hit bullets, evicting all 4
        generic ones (they yield first per spec)."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic)
        budget = _budget(5, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 5
        for h in hits:
            assert h in final_bullets
        for g in generic:
            assert g not in final_bullets

    def test_hit_bullets_ordered_before_no_hit_bullets_when_uncapped(self):
        """Without a tight ceiling, all 9 bullets survive but are reordered
        hits-first (aligns with condense's later-listed-cut-first rule)."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic)
        budget = _budget(20, claimable_forms=forms)  # no eviction needed

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 9
        hit_positions = [final_bullets.index(h) for h in hits]
        no_hit_positions = [final_bullets.index(g) for g in generic]
        assert max(hit_positions) < min(no_hit_positions)

    def test_idempotent_second_pass_is_a_noop(self):
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic)
        budget = _budget(5, claimable_forms=forms)

        once = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        twice = _restore_ledger_bullets(once, profile_json, ledger, budget)

        assert twice.work_history[0].bullets == once.work_history[0].bullets

    def test_noop_without_a_keyword_ledger(self):
        from applire.services.cv import _restore_ledger_bullets

        profile_json, _ledger, generic, _hits = _founder_fixture()
        tailored = _tailored_cv(generic)
        budget = _budget(9)

        result = _restore_ledger_bullets(tailored, profile_json, None, budget)
        assert result.work_history[0].bullets == generic

    def test_noop_when_every_claimable_concept_already_present(self):
        """If the draft already surfaces every claimable concept (elsewhere in the
        document, e.g. in the summary), nothing is missing -> nothing restored."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # All 5 concepts are already present -- in the SUMMARY, not the bullets.
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Max"},
            "summary": "Delivered Concept1 Concept2 Concept3 Concept4 Concept5 work.",
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Engineer",
                "start_date": "2020-01", "end_date": None, "bullets": list(generic),
            }],
            "skills": [],
        })
        budget = _budget(9, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        assert result.work_history[0].bullets == generic

    def test_does_not_duplicate_a_bullet_already_present_verbatim(self):
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # The draft already kept ONE of the hit bullets alongside the generic ones.
        tailored = _tailored_cv(generic + [hits[0]])
        budget = _budget(20, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        final_bullets = result.work_history[0].bullets
        assert final_bullets.count(hits[0]) == 1
        # The remaining 4 hits are still restored.
        for h in hits[1:]:
            assert h in final_bullets

    def test_no_budget_for_entry_restores_uncapped(self):
        """An entry id absent from budget.roles (id mismatch / legacy) must not crash
        — restoration proceeds uncapped for that entry."""
        from applire.services.cv_budget import BudgetResult, BulletTier
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic)
        empty_budget = BudgetResult(
            roles={}, tiers={"top": BulletTier("top", 5, 4)}, target_pages=2,
            region="DACH", claimable_forms=tuple(forms),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, empty_budget)
        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 9
        for h in hits:
            assert h in final_bullets

    def test_no_budget_object_at_all_restores_uncapped(self):
        """budget=None (legacy caller) must still restore, just without a ceiling."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        tailored = _tailored_cv(generic)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, None)
        final_bullets = result.work_history[0].bullets
        for h in hits:
            assert h in final_bullets

    def test_pure_input_unmutated(self):
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic)
        budget = _budget(5, claimable_forms=forms)

        _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        assert tailored.work_history[0].bullets == generic

    def test_caps_entry_over_budget_even_without_restoration(self):
        """#234-adjacent friction finding: the #122 coverage-review loop can push
        the writer to ADD a bullet with no ceiling awareness of its own. This
        entry already carries every claimable concept (nothing to restore) but
        landed at 6 bullets against a max_bullets=5 ceiling -- the guard must
        still cap it, cutting the no-hit bullet first."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # 5 hit bullets (all claimable concepts already present) + 1 no-hit
        # generic bullet the review loop tacked on -- 6 total, nothing missing.
        six_bullets = list(hits) + [generic[0]]
        tailored = _tailored_cv(six_bullets)
        budget = _budget(5, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 5
        assert generic[0] not in final_bullets
        for h in hits:
            assert h in final_bullets

    def test_caps_entry_even_when_every_surviving_bullet_is_a_hit(self):
        """Live-reproduced shape: RoleBudget max_bullets=5, the writer/review loop
        landed on 6 bullets that ALL carry a claimable hit. The ceiling still
        applies -- the later-listed (last-in) hit bullet is cut."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # 6 bullets, all hits (5 known concepts + a 6th that also carries Concept1
        # again so nothing is "missing" -- pure over-ceiling, no restoration).
        six_hit_bullets = list(hits) + ["Also delivered further Concept1 rollout"]
        tailored = _tailored_cv(six_hit_bullets)
        budget = _budget(5, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 5
        # Later-listed (last-in) bullet is cut first -- LIFO among equal hit-status.
        assert "Also delivered further Concept1 rollout" not in final_bullets
        assert final_bullets == hits

    def test_entries_under_ceiling_are_left_untouched_and_unreordered(self):
        """No restoration needed (every claimable concept already surfaces
        elsewhere in the document) and already within budget -- the entry,
        incl. its original bullet ORDER, must not be touched at all."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # All 5 concepts already present -- in the SUMMARY, so nothing is
        # "missing" and restoration never triggers for this entry.
        two_bullets = [generic[0], hits[0]]
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Max"},
            "summary": "Delivered Concept1 Concept2 Concept3 Concept4 Concept5 work.",
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Engineer",
                "start_date": "2020-01", "end_date": None, "bullets": list(two_bullets),
            }],
            "skills": [],
        })
        budget = _budget(5, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        assert result.work_history[0].bullets == two_bullets

    def test_capping_without_restoration_is_idempotent(self):
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        six_bullets = list(hits) + [generic[0]]
        tailored = _tailored_cv(six_bullets)
        budget = _budget(5, claimable_forms=forms)

        once = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        twice = _restore_ledger_bullets(once, profile_json, ledger, budget)

        assert twice.work_history[0].bullets == once.work_history[0].bullets

    def test_restore_and_cap_interact_in_one_pass(self):
        """A vault restoration pushes an entry over budget in the SAME pass that
        performs the restoration -- restore-then-cap must both happen, evicting
        no-hit bullets first even though they were never touched by restoration."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        forms = [f"Concept{i}" for i in range(1, 6)]
        # Draft already has 4 hit bullets (Concept1-4) plus 2 generic no-hit
        # bullets -- 6 total. Concept5 is still missing and must be restored,
        # which would make 7 without capping; ceiling is 5.
        draft = [hits[0], hits[1], hits[2], hits[3], generic[0], generic[1]]
        tailored = _tailored_cv(draft)
        budget = _budget(5, claimable_forms=forms)

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert len(final_bullets) == 5
        assert hits[4] in final_bullets  # Concept5 was restored
        for h in hits[:4]:
            assert h in final_bullets  # pre-existing hits all survive
        assert generic[0] not in final_bullets
        assert generic[1] not in final_bullets

    def test_only_restores_from_the_matching_vault_entry(self):
        """A vault bullet on a DIFFERENT work entry must never be restored onto
        this one, even if it carries a missing claimable concept."""
        from applire.services.cv import _restore_ledger_bullets

        profile_json, ledger, generic, hits = _founder_fixture()
        # Add a second vault entry carrying Concept1 too -- must not leak.
        profile_json["work_experience"].append({
            "id": "w2", "company": "Other", "role": "Dev", "start_date": "2015-01",
            "end_date": "2019-01", "is_current": False,
            "responsibilities": ["Delivered Concept1 work elsewhere"], "achievements": [],
        })
        forms = [f"Concept{i}" for i in range(1, 6)]
        tailored = _tailored_cv(generic, extra_entries=[{
            "id": "w2", "company": "Other", "role": "Dev", "start_date": "2015-01",
            "end_date": "2019-01", "bullets": [],
        }])
        budget = _budget(5, claimable_forms=forms)
        budget.roles["w2"] = budget.roles["w1"].__class__(
            work_entry_id="w2", tier="bottom", max_bullets=5,
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        w2 = next(w for w in result.work_history if w.id == "w2")
        assert "Delivered Concept1 work elsewhere" not in w2.bullets


def _load_bearing_ledger_entry(concept: str, *, evidence: str) -> dict:
    return {
        "concept": concept,
        "surface_forms": [concept, "Budgetverantwortung"],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": evidence,
    }


class TestRestoreLedgerBulletsProtectsLoadBearingClaims:
    """#315 — charter run #7 case 2 (operations_marcus_de, DE): a `direct` +
    `claimable` + `fit_weight: 1.0` concept ("Budget- und
    Investitionsverantwortung") reached the delivered CV as a bare keyword
    ("Budgetverantwortung" in the summary + skills) while its quantified
    vault bullet ("Budgetverantwortung ca. 6 Mio. € ...") never landed in a
    narrative bullet. ``verified_missing_claimable``'s whole-document scan
    found the bare keyword and called the concept "present", so this guard
    never restored the vault's own quantified bullet. This is the exact
    reproduction, using the real evidence text from the run's LLM log."""

    def _fixture(self):
        concept = "Budget- und Investitionsverantwortung"
        vault_bullet = (
            "Budgetverantwortung ca. 6 Mio. € (Personal, Instandhaltung, "
            "Material-Gemeinkosten)."
        )
        other_bullets = [
            "Führung von 38 Mitarbeitenden in zwei Fertigungsbereichen.",
            "Schnittstelle zu Einkauf, Qualitätssicherung und Supply Chain.",
        ]
        ledger = [
            _load_bearing_ledger_entry(
                concept,
                evidence=(
                    "Explicitly listed as a skill ('Budgetverantwortung', "
                    "intermediate) and work experience (Budget- und "
                    "Investitionsverantwortung für 6 Mio. €)."
                ),
            )
        ]
        profile_json = {
            "work_experience": [
                {
                    "id": "w1",
                    "company": "Weberit Kunststofftechnik GmbH",
                    "role": "Produktionsleiter",
                    "start_date": "2017-04",
                    "end_date": None,
                    "is_current": True,
                    "responsibilities": other_bullets + [vault_bullet],
                    "achievements": [],
                }
            ],
            "projects": [],
        }
        return profile_json, ledger, other_bullets, vault_bullet

    def test_restores_the_quantified_bullet_despite_bare_keyword_elsewhere(self):
        """The bug: the concept is already 'present' (bare keyword in summary
        + skills), so pre-#315 this guard found nothing missing and never
        restored the number. Post-#315 it must restore the vault bullet
        anyway, because the concept is load-bearing and its narrative is
        absent."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets
        from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

        profile_json, ledger, other_bullets, vault_bullet = self._fixture()
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Stefan Brandt"},
            "summary": "... und Budgetverantwortung.",
            "skills": ["Budget- und Investitionsverantwortung"],
            "work_history": [{
                "id": "w1", "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter", "start_date": "2017-04", "end_date": None,
                "bullets": list(other_bullets),
            }],
        })
        budget = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={"top": BulletTier("top", 5, 4)}, target_pages=2, region="DACH",
            claimable_forms=("Budget- und Investitionsverantwortung", "Budgetverantwortung"),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        final_bullets = result.work_history[0].bullets
        assert vault_bullet in final_bullets, (
            "load-bearing quantified bullet was not restored despite the bare "
            "keyword satisfying the whole-document coverage check"
        )

    def test_no_new_figure_is_ever_minted(self):
        """Guardrail (#315 acceptance): restoration only ever copies a vault
        bullet VERBATIM, so every figure in the result must already have been
        present, verbatim, in the vault's own narrative text -- never a
        rephrased or invented number."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets
        from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget
        from applire.services.oracle.matchers.figures import extract_figures

        profile_json, ledger, other_bullets, vault_bullet = self._fixture()
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Stefan Brandt"},
            "summary": "... und Budgetverantwortung.",
            "skills": ["Budget- und Investitionsverantwortung"],
            "work_history": [{
                "id": "w1", "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter", "start_date": "2017-04", "end_date": None,
                "bullets": list(other_bullets),
            }],
        })
        budget = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={"top": BulletTier("top", 5, 4)}, target_pages=2, region="DACH",
            claimable_forms=("Budget- und Investitionsverantwortung", "Budgetverantwortung"),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)

        vault_text = " ".join(
            profile_json["work_experience"][0]["responsibilities"]
            + profile_json["work_experience"][0]["achievements"]
        )
        vault_values = {(f.kind, f.value) for f in extract_figures(vault_text)}
        for bullet in result.work_history[0].bullets:
            for fig in extract_figures(bullet):
                assert (fig.kind, fig.value) in vault_values, (
                    f"minted figure not present in vault: {fig.raw!r} in {bullet!r}"
                )

    def test_does_not_regress_non_load_bearing_bare_keyword_noop(self):
        """A plain (non-quantified) claimable concept must keep the EXISTING
        behaviour: a bare keyword mention elsewhere is still sufficient, and
        this guard must not restore anything for it. Only load-bearing
        concepts get the stronger narrative check."""
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _restore_ledger_bullets
        from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

        concept = "SAP (PP/MM)"
        ledger = [{
            "concept": concept, "surface_forms": [concept, "SAP"], "claimable": True,
            "status": "direct", "sources": ["required"], "fit_weight": 1.0,
            "evidence": "Explicitly listed as a skill (SAP, expert, 15 years).",
        }]
        profile_json = {
            "work_experience": [{
                "id": "w1", "company": "Acme", "role": "Engineer",
                "start_date": "2020-01", "end_date": None, "is_current": True,
                "responsibilities": ["Daily work with SAP PP and MM modules."],
                "achievements": [],
            }],
            "projects": [],
        }
        other_bullets = ["Generic bullet with no ledger hit."]
        tailored = TailoredCVData.model_validate({
            "contact": {"name": "Max"},
            "summary": "Experienced with SAP.",
            "skills": [concept],
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Engineer",
                "start_date": "2020-01", "end_date": None,
                "bullets": list(other_bullets),
            }],
        })
        budget = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={"top": BulletTier("top", 5, 4)}, target_pages=2, region="DACH",
            claimable_forms=(concept, "SAP"),
        )

        result = _restore_ledger_bullets(tailored, profile_json, ledger, budget)
        assert result.work_history[0].bullets == other_bullets


class TestRestoreLedgerBulletsWiredIntoBackgroundRender:
    """End-to-end: ``_render_cv_background`` must thread the guard so a founder-
    acceptance-shaped draft (4 generic bullets survive, 5 JD-matching vault
    responsibilities dropped) ships with the 5 restored, not the 4 generic-only
    document the LLM drafted. Mocks the LLM provider/DB session; no Docker, no
    LLM, no real network (mirrors test_cv_generation_budget_wiring.py)."""

    @pytest.mark.asyncio
    async def test_render_cv_background_restores_dropped_evidence_bullets(self):
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch

        profile_json, ledger, generic, hits = _founder_fixture()
        profile_json["skills"] = []
        profile_json["education"] = []
        profile_json["languages"] = []
        profile_json["personal_info"] = {"name": "Max", "email": None}

        cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cv = MagicMock()
        mock_cv.status = "pending"
        mock_cv.target_pages = 2

        mock_job = MagicMock()
        mock_job.role_title = "Lead AI Engineer"
        mock_job.required_skills = []
        mock_job.nice_to_have_skills = []
        mock_job.keywords = []
        mock_job.seniority_level = ""
        mock_job.company_culture_signals = []
        mock_job.language_requirement = ""

        mock_profile = MagicMock()
        mock_profile.profile_json = profile_json

        mock_gap = MagicMock()
        mock_gap.keyword_gaps = []
        mock_gap.critical_gaps = []
        mock_gap.keyword_ledger = ledger

        mock_db = AsyncMock()
        mock_db.get.side_effect = lambda model, id_: {
            cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
        }[id_]
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_gap
        mock_db.execute.return_value = mock_result

        draft = {
            "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
            "summary": "Engineer.",
            "work_history": [{
                "id": "w1", "company": "Acme", "role": "Engineer",
                "start_date": "2020-01", "end_date": None, "bullets": list(generic),
            }],
            "skills": [], "education": [], "languages": [],
        }

        async def fake_fallback(*args, **kwargs):
            return draft

        with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
             patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
             patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback), \
             patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
             patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
             patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            from applire.services.cv import _render_cv_background
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

        final_bullets = mock_cv.tailored_data["work_history"][0]["bullets"]
        for h in hits:
            assert h in final_bullets, f"dropped JD-matching vault bullet was not restored: {h!r}"
