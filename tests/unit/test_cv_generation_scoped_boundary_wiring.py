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

"""#277 (#270 Fix D inverted) — ``_render_cv_background`` must derive vault SCOPED
BOUNDARIES (a claimable Keyword Ledger concept the vault ALSO holds an explicit
stated limit on, via ``ProfileMetadata.denied_concepts``) and thread the rendered
block into ``_tailor_cv_with_fallback`` AND the review/retry ``source`` — entirely
from data available at CV-generation time, independent of whether a cover letter
exists yet. Mocks the LLM provider/DB session; no Docker, no LLM, no real network.

Fixture data is invented (never real personal data), mirroring the shape already
used by backend/tests/unit/services/test_cross_document.py.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _profile_json(*, metadata: dict | None = None) -> dict:
    return {
        "work_experience": [
            {"id": "w1", "company": "Northwind Labs", "role": "ML Engineer",
             "start_date": "2024-01", "end_date": None, "is_current": True,
             "responsibilities": ["Built and owned the RAG pipeline data layer."],
             "achievements": []},
        ],
        "projects": [],
        "skills": ["RAG pipelines"], "education": [], "languages": [],
        "personal_info": {"name": "Max", "email": None},
        "metadata": metadata or {},
    }


def _tailored_raw() -> dict:
    return {
        "contact": {"name": "Max", "email": None, "phone": None, "location": None, "linkedin": None},
        "summary": "ML engineer.",
        "work_history": [{"company": "Northwind Labs", "role": "ML Engineer",
                           "start_date": "2024-01", "end_date": None, "bullets": []}],
        "skills": ["RAG pipelines"], "education": [], "languages": [],
    }


_LEDGER = [
    {
        "concept": "RAG pipelines",
        "claimable": True,
        "surface_forms": ["RAG pipelines", "RAG"],
        "evidence": "Built and owned the RAG pipeline data layer at Northwind Labs.",
    }
]
_DENIED_CONCEPTS = [
    {
        "concept": "hands-on embedding work",
        "statement": (
            "I designed the database for the RAG pipeline but did not configure the "
            "embedding models myself."
        ),
        "source": "interview",
    }
]


async def _run_render_cv_background(*, profile_json: dict, ledger: list[dict]):
    """Shared harness: patches the DB/provider/review layers and captures the kwargs
    ``_tailor_cv_with_fallback`` and ``review_and_refine`` were called with."""
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 2

    mock_job = MagicMock()
    mock_job.role_title = "ML Engineer"
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

    fallback_kwargs: dict = {}
    review_kwargs: dict = {}

    async def fake_fallback(*args, **kwargs):
        fallback_kwargs.update(kwargs)
        return _tailored_raw()

    async def fake_review(**kwargs):
        review_kwargs.update(kwargs)
        return kwargs["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback), \
         patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=fake_review)), \
         patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda draft, *a, **kw: draft)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        mock_session_local.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    return fallback_kwargs, review_kwargs


# ---------------------------------------------------------------------------
# Guard test 1: a vault-held scoped boundary reaches the CV generation prompt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cv_background_threads_a_real_scoped_boundary_into_the_fallback_call():
    profile_json = _profile_json(metadata={"denied_concepts": _DENIED_CONCEPTS})
    fallback_kwargs, review_kwargs = await _run_render_cv_background(
        profile_json=profile_json, ledger=_LEDGER
    )

    assert "scoped_boundary_block" in fallback_kwargs, (
        "_tailor_cv_with_fallback was not given a scoped_boundary_block"
    )
    block = fallback_kwargs["scoped_boundary_block"]
    assert block, "expected a non-empty scoped boundary block"
    assert "RAG pipelines" in block
    assert "embedding models" in block
    # Never deleted from the CV — the block instructs the SCOPED claim, not a denial.
    assert "never a bare denial that discards" in block

    # Also folded into the review/retry source (US202+US213 precedent) so a retry can
    # ground its correction in the same vault wording.
    assert "source" in review_kwargs
    assert "RAG pipelines" in review_kwargs["source"]
    assert "embedding models" in review_kwargs["source"]


# ---------------------------------------------------------------------------
# Guard test 2: no vault boundary -> unchanged (no regression) common-case behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cv_background_omits_the_block_when_no_denied_concepts_exist():
    profile_json = _profile_json(metadata={})  # no denied_concepts at all
    fallback_kwargs, _review_kwargs = await _run_render_cv_background(
        profile_json=profile_json, ledger=_LEDGER
    )

    assert "scoped_boundary_block" in fallback_kwargs
    assert not fallback_kwargs["scoped_boundary_block"], (
        "no persisted denial exists — the scoped boundary block must stay empty, "
        "identical to pre-#277 behaviour"
    )


# ---------------------------------------------------------------------------
# Guard test 3: a denied, non-claimable concept must never surface as a CV claim
# (ADR-059) — even when denied_concepts is populated, an unrelated/non-claimable
# concept produces no boundary and is not threaded as if it were claimable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cv_background_never_promotes_a_denied_non_claimable_concept():
    non_claimable_ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": False,  # honest gap — the vault does NOT support this claim
            "surface_forms": ["RAG pipelines", "RAG"],
            "evidence": "",
        }
    ]
    profile_json = _profile_json(metadata={"denied_concepts": _DENIED_CONCEPTS})
    fallback_kwargs, _review_kwargs = await _run_render_cv_background(
        profile_json=profile_json, ledger=non_claimable_ledger
    )

    assert "scoped_boundary_block" in fallback_kwargs
    assert not fallback_kwargs["scoped_boundary_block"], (
        "a non-claimable (denied) concept must never be rendered as a scoped CLAIM"
    )
