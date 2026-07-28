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

"""``_render_cv_background`` must thread the vault's STATED LIMITS (the candidate's
own persisted denial statements, ``ProfileMetadata.denied_concepts``) into
``_tailor_cv_with_fallback`` AND the review/retry ``source``, so no CV section
contradicts one. Mocks the LLM provider/DB session; no Docker, no LLM, no network.

Replaces the #277 SCOPED BOUNDARIES wiring tests (charter run #8, 2026-07-28): that
block also told the writer WHICH claimable concept each limit bounded, decided by
text overlap, and on real data it named four of the candidate's strongest concepts
as limited. The block now carries statements only.

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
# Shaped like a real honest denial: it names an adjacent STRENGTH ("designed the
# database for the RAG pipeline") in the same breath as the limit. That is exactly
# the shape the deleted boundary matcher read backwards.
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
# Guard test 1: the vault's stated limits reach the CV generation prompt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cv_background_threads_the_stated_limits_into_the_fallback_call():
    profile_json = _profile_json(metadata={"denied_concepts": _DENIED_CONCEPTS})
    fallback_kwargs, review_kwargs = await _run_render_cv_background(
        profile_json=profile_json, ledger=_LEDGER
    )

    assert "stated_limits_block" in fallback_kwargs, (
        "_tailor_cv_with_fallback was not given a stated_limits_block"
    )
    block = fallback_kwargs["stated_limits_block"]
    assert block, "expected a non-empty stated limits block"
    # The candidate's own sentence, verbatim and whole.
    assert "did not configure the embedding models myself" in block
    # ...but never turned into a verdict about a specific claimable concept.
    assert "POSITIVE (candidate's own vault evidence)" not in block
    assert "both halves" not in block
    # And the rule that stops the writer generalising from it.
    assert "strength, not a limit" in block.lower()

    # Also folded into the review/retry source (US202+US213 precedent) so a retry can
    # ground its correction in the same vault wording.
    assert "source" in review_kwargs
    assert "embedding models" in review_kwargs["source"]


# ---------------------------------------------------------------------------
# Guard test 2: no persisted denial -> the prompt is unchanged (no regression).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cv_background_omits_the_block_when_no_denied_concepts_exist():
    profile_json = _profile_json(metadata={})  # no denied_concepts at all
    fallback_kwargs, _review_kwargs = await _run_render_cv_background(
        profile_json=profile_json, ledger=_LEDGER
    )

    assert "stated_limits_block" in fallback_kwargs
    assert not fallback_kwargs["stated_limits_block"], (
        "no persisted denial exists — the stated limits block must stay empty"
    )


# ---------------------------------------------------------------------------
# Guard test 3 (the run-8 regression): the block must not change with the ledger.
#
# The deleted `find_scoped_boundaries` cross-referenced the ledger against the
# denials, so the same denial produced a different instruction depending on which
# concepts happened to be claimable. That coupling IS the defect. The limits are a
# property of the candidate's own statements and nothing else.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stated_limits_block_is_independent_of_the_keyword_ledger():
    profile_json = _profile_json(metadata={"denied_concepts": _DENIED_CONCEPTS})

    claimable, _ = await _run_render_cv_background(profile_json=profile_json, ledger=_LEDGER)
    non_claimable, _ = await _run_render_cv_background(
        profile_json=profile_json,
        ledger=[{**_LEDGER[0], "claimable": False, "evidence": ""}],
    )
    empty_ledger, _ = await _run_render_cv_background(profile_json=profile_json, ledger=[])

    assert claimable["stated_limits_block"]
    assert claimable["stated_limits_block"] == non_claimable["stated_limits_block"]
    assert claimable["stated_limits_block"] == empty_ledger["stated_limits_block"]
