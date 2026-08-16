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

"""E042 / US237 — ``_render_cv_background`` computes the deterministic bullet
budget from the profile + Keyword Ledger + the row's persisted ``target_pages``,
and threads it into ``_tailor_cv_with_fallback`` (ADR-051 §3). Mocks the LLM
provider/DB session; no Docker, no LLM, no real network.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _profile_json() -> dict:
    return {
        "work_experience": [
            {"id": "w1", "company": "Acme", "role": "Engineer", "start_date": "2024-01",
             "end_date": None, "is_current": True, "responsibilities": ["Built a Kubernetes platform"],
             "achievements": []},
        ],
        "projects": [],
        "skills": ["Python"], "education": [], "languages": [],
        "personal_info": {"name": "Max", "email": None},
    }


def _tailored_raw() -> dict:
    return {
        "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
        "summary": "Dev.",
        "work_history": [{"company": "Acme", "role": "Engineer", "start_date": "2024-01",
                           "end_date": None, "bullets": []}],
        "skills": ["Python"], "education": [], "languages": [],
    }


@pytest.mark.asyncio
async def test_render_cv_background_threads_a_real_budget_into_the_fallback_call():
    from applire.services.cv_budget import BudgetResult

    ledger = [
        {"concept": "Kubernetes", "surface_forms": ["Kubernetes"], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "8y"},
    ]

    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 3  # non-standard target — scaling must reach compute_bullet_budgets

    mock_job = MagicMock()
    mock_job.role_title = "Dev"
    mock_job.required_skills = []
    mock_job.nice_to_have_skills = []
    mock_job.keywords = []
    mock_job.seniority_level = ""
    mock_job.company_culture_signals = []
    mock_job.language_requirement = ""

    mock_profile = MagicMock()
    mock_profile.profile_json = _profile_json()

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

    captured: dict = {}

    async def fake_fallback(*args, **kwargs):
        captured.update(kwargs)
        return _tailored_raw()

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

    assert "budget" in captured, "_tailor_cv_with_fallback was not given a budget"
    budget = captured["budget"]
    assert isinstance(budget, BudgetResult)
    assert budget.target_pages == 3  # record.target_pages, not the region default
    assert "w1" in budget.roles
    # target_pages=3 is +1 page above the DACH standard (2) -> top ceiling scales 5->6.
    assert budget.tiers["top"].max_bullets == 6
    # current + a claimable hit ("Kubernetes" present in the entry's responsibilities) -> top tier.
    assert budget.roles["w1"].tier == "top"


@pytest.mark.asyncio
async def test_render_cv_background_falls_back_to_region_standard_when_target_pages_is_null():
    """Legacy pre-E042 rows have target_pages=NULL — the background task must still
    resolve a usable target rather than crash or silently skip budgeting."""
    from applire.services.cv_budget import BudgetResult

    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = None

    mock_job = MagicMock()
    mock_job.role_title = "Dev"
    mock_job.required_skills = []
    mock_job.nice_to_have_skills = []
    mock_job.keywords = []
    mock_job.seniority_level = ""
    mock_job.company_culture_signals = []
    mock_job.language_requirement = ""

    mock_profile = MagicMock()
    mock_profile.profile_json = _profile_json()

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {
        cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
    }[id_]
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # no GapAnalysis row
    mock_db.execute.return_value = mock_result

    captured: dict = {}

    async def fake_fallback(*args, **kwargs):
        captured.update(kwargs)
        return _tailored_raw()

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

    budget = captured["budget"]
    assert isinstance(budget, BudgetResult)
    assert budget.target_pages == 2  # DACH region standard, resolved via resolve_target_pages(None, None)


@pytest.mark.asyncio
async def test_render_cv_background_threads_the_real_budget_into_the_tailoring_coverage_gate():
    """ADR-076 clause 6 (#543): the SAME ``BudgetResult`` computed above must
    reach ``coverage_reviewer_prompt_fn`` as a real ``CoverageBudget`` in the
    main tailoring loop — not a silently-dropped ``None``. A wiring
    regression here (the ``budget=`` kwarg quietly removed from the
    ``coverage_reviewer_prompt_fn(...)`` call in ``services/cv.py``) reopens
    the exact displacement-churn issue #543 fixes, and neither the pure unit
    tests for ``rank_gate_missing_claimable`` nor
    ``test_render_cv_background_threads_a_real_budget_into_the_fallback_call``
    above would notice — this test exists to close precisely that gap.
    """
    from applire.services.keyword_ledger import CoverageBudget

    ledger = [
        {"concept": "Kubernetes", "surface_forms": ["Kubernetes"], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "8y"},
    ]

    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 3

    mock_job = MagicMock()
    mock_job.role_title = "Dev"
    mock_job.required_skills = []
    mock_job.nice_to_have_skills = []
    mock_job.keywords = []
    mock_job.seniority_level = ""
    mock_job.company_culture_signals = []
    mock_job.language_requirement = ""

    mock_profile = MagicMock()
    mock_profile.profile_json = _profile_json()

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

    coverage_budgets_seen: list = []

    def spy_coverage_reviewer_prompt_fn(base_fn, keyword_ledger, budget=None):
        coverage_budgets_seen.append(budget)
        return lambda source, draft: base_fn(source, draft)

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch(
             "applire.services.cv._tailor_cv_with_fallback",
             new=AsyncMock(side_effect=lambda *a, **kw: _tailored_raw()),
         ), \
         patch(
             "applire.services.keyword_ledger.coverage_reviewer_prompt_fn",
             side_effect=spy_coverage_reviewer_prompt_fn,
         ), \
         patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
         patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        mock_session_local.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    # Two consumers since #538: the tailoring loop AND the terminal review —
    # BOTH must thread the real budget (a None in either silently disables the
    # ADR-076 clause 6 rank gate for that round).
    assert len(coverage_budgets_seen) == 2, (
        f"expected 2 coverage_reviewer_prompt_fn calls (tailoring + terminal), "
        f"saw {len(coverage_budgets_seen)}"
    )
    for cb in coverage_budgets_seen:
        assert isinstance(cb, CoverageBudget), (
            "budget=None reached coverage_reviewer_prompt_fn — the ADR-076 clause 6 "
            "rank gate is silently disabled for one of the review rounds"
        )
        # target_pages=3 -> top ceiling 6 (see the sibling test above); one role only.
        assert cb.capacity == 6


@pytest.mark.asyncio
async def test_review_cv_language_threads_the_budget_into_its_own_coverage_gate():
    """ADR-076 clause 6 (#543): ``_review_cv_language`` is the LAST writer
    (#122 follow-up) and shares the coverage instrument with the tailoring
    loop — it must rank-gate under the SAME budget, or a low-rank concept the
    tailoring loop legally omitted could still be (uselessly) re-demanded by
    the language pass. Exercises ``_review_cv_language`` directly rather than
    through the full background pipeline (see the sibling test above for why:
    the downstream ATS-report/self-audit stages need far more DB mocking than
    this wiring question is worth)."""
    from applire.services.cv import _review_cv_language
    from applire.services.cv_budget import BudgetResult, RoleBudget
    from applire.services.keyword_ledger import CoverageBudget

    budget_result = BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=6)},
        tiers={}, target_pages=3, region="DACH",
    )

    seen: list = []

    def spy_coverage_reviewer_prompt_fn(base_fn, keyword_ledger, budget=None):
        seen.append(budget)
        return lambda source, draft: base_fn(source, draft)

    provider = AsyncMock()
    provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
    with patch(
        "applire.services.keyword_ledger.coverage_reviewer_prompt_fn",
        side_effect=spy_coverage_reviewer_prompt_fn,
    ):
        await _review_cv_language(
            _tailored_raw(), "de", provider, keyword_ledger=[], budget=budget_result
        )

    assert len(seen) == 1
    assert isinstance(seen[0], CoverageBudget)
    assert seen[0].capacity == 6
