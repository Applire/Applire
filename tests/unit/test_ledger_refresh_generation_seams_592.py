# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#592 — the two GENERATION seams of ``refresh_ledger_against_vault``.

The refresh is a shared helper at four call sites; ``test_ledger_refresh_seams_592``
covers the two report-side ones (each chain's ``_latest_keyword_ledger``). These
are the two that matter to a delivered document: the ledger the CV writer is
handed and the ledger the letter writer is handed. Each drives the real service
entrypoint (``_render_cv_background`` / ``_render_cover_letter_background``) and
asserts on the artefact the writer actually received — not on the helper.

Revert either generation site independently and exactly its own test goes red.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# The vault as it stands at GENERATION time — verbatim from the captured
# 2026-08-25 Anna-Bauer CV-writer prompt.
_VAULT_SENTENCE = "Built and maintained REST APIs in FastAPI serving 2 million-plus daily requests."

_PROFILE = {
    "personal_info": {"name": "Anna Bauer", "email": None},
    "work_experience": [
        {
            "id": "w1",
            "company": "StartupX AG",
            "role": "Backend Engineer",
            "start_date": "2020-01",
            "end_date": None,
            "is_current": True,
            "responsibilities": [_VAULT_SENTENCE],
            "achievements": [],
        }
    ],
    "projects": [],
    "skills": [],
    "education": [],
    "languages": [],
    "metadata": {"denied_concepts": []},
}

# The ledger as it was PERSISTED — built when those work entries had no bullets.
# `REST APIs` was a correct honest gap THEN and is a contradiction NOW.
_STALE_LEDGER = [
    {
        "concept": "REST APIs",
        "surface_forms": ["REST APIs"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
    {
        "concept": "GraphQL",
        "surface_forms": ["GraphQL"],
        "sources": ["nice_to_have"],
        "fit_weight": 0.5,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
]


# ── seam 3: the CV generation read ─────────────────────────────────────────


def _tailored_raw() -> dict:
    return {
        "contact": {
            "name": "Anna Bauer",
            "email": None,
            "phone": None,
            "location": None,
            "linkedin": None,
        },
        "summary": "Backend engineer.",
        "work_history": [
            {
                "company": "StartupX AG",
                "role": "Backend Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": [],
            }
        ],
        "skills": [],
        "education": [],
        "languages": [],
    }


async def _run_cv(ledger: list[dict]) -> dict:
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock()
    mock_cv.status = "pending"
    mock_cv.target_pages = 2

    mock_job = MagicMock()
    mock_job.role_title = "Senior Backend Engineer"
    mock_job.required_skills = ["REST APIs"]
    mock_job.nice_to_have_skills = ["GraphQL"]
    mock_job.keywords = []
    mock_job.seniority_level = ""
    mock_job.company_culture_signals = []
    mock_job.language_requirement = ""
    mock_job.raw_text = "We are hiring a Senior Backend Engineer."
    mock_job.jd_language = "en"

    mock_profile = MagicMock()
    mock_profile.profile_json = _PROFILE

    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []
    mock_gap.critical_gaps = []
    mock_gap.keyword_ledger = ledger

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {
        cv_id: mock_cv,
        job_id: mock_job,
        profile_id: mock_profile,
    }[id_]
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.return_value = mock_result

    captured: dict = {}

    async def fake_fallback(*args, **kwargs):
        captured.update(kwargs)
        return _tailored_raw()

    with patch("applire.services.cv.AsyncSessionLocal") as msl, patch(
        "applire.services.cv.get_provider", return_value=AsyncMock()
    ), patch(
        "applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback
    ), patch(
        "applire.services.cv.review_and_refine",
        new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
    ), patch(
        "applire.services.cv._review_cv_language",
        new=AsyncMock(side_effect=lambda draft, *a, **kw: draft),
    ), patch(
        "applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")
    ), patch(
        "applire.services.cv_section_editor.build_content_snapshot", return_value={}
    ):
        msl.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background

        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


@pytest.mark.asyncio
async def test_cv_generation_seam_hands_the_writer_a_ledger_the_vault_agrees_with():
    captured = await _run_cv([dict(r) for r in _STALE_LEDGER])
    ledger = {e["concept"]: e for e in captured.get("keyword_ledger") or []}

    assert ledger, "_tailor_cv_with_fallback was not given a keyword_ledger"
    assert ledger["REST APIs"]["claimable"] is True, (
        "the CV writer was forbidden a term its own profile block carries"
    )
    assert _VAULT_SENTENCE in ledger["REST APIs"]["evidence"]
    assert ledger["GraphQL"]["claimable"] is False, "a genuine gap must stay forbidden"


@pytest.mark.asyncio
async def test_cv_generation_seam_leaves_a_vault_consistent_ledger_alone():
    """No behaviour change on the ordinary case: nothing to refresh, nothing moves."""
    fresh = [
        {
            "concept": "REST APIs",
            "surface_forms": ["REST APIs"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "direct",
            "evidence": _VAULT_SENTENCE,
            "claimable": True,
        }
    ]
    captured = await _run_cv([dict(r) for r in fresh])
    got = (captured.get("keyword_ledger") or [])[0]
    assert got["status"] == "direct" and got["evidence"] == _VAULT_SENTENCE


# ── seam 4: the letter generation read ─────────────────────────────────────

_SAMPLE_LETTER = {
    "header": {"name": "Anna Bauer"},
    "recipient": {"name": None, "company": "Vector Analytics", "date": None},
    "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Mit freundlichen Grüßen"]},
    "signature": {"closing": None, "name": None},
}


async def _run_letter(ledger: list[dict]) -> list[dict]:
    cl_id, cv_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    mock_cl = MagicMock()
    mock_cl.status = "pending"
    mock_cl.pre_gen_inputs = {"tone": "formal", "motivation": "", "salary": "", "availability": ""}
    mock_cl.letter_data = {}
    mock_cl.section_overrides = {}
    mock_cl.document_language = "de"

    mock_job = MagicMock()
    mock_job.raw_text = "We are hiring a Senior Backend Engineer."
    mock_job.company_name = "Vector Analytics"
    mock_job.keywords = []

    mock_cv = MagicMock()
    mock_cv.tailored_data = None

    mock_profile = MagicMock()
    mock_profile.profile_json = _PROFILE

    mock_gap = MagicMock()
    mock_gap.keyword_ledger = ledger
    mock_gap.category_c = []
    mock_gap.critical_gaps = []
    mock_gap.keyword_gaps = []

    shared_result = MagicMock()
    shared_result.scalar_one_or_none.side_effect = [
        mock_cl,
        mock_job,
        mock_cv,
        mock_profile,
        mock_gap,
    ]
    mock_db = AsyncMock()
    mock_db.execute.return_value = shared_result

    seen: list[list[dict]] = []
    real_refresh = None

    def spy(ledger_arg, profile_arg, *, seam=""):
        out, changed = real_refresh(ledger_arg, profile_arg, seam=seam)
        if seam == "letter generation":
            seen.append(out)
        return out, changed

    from applire.services import keyword_ledger as kl

    real_refresh = kl.refresh_ledger_against_vault

    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = json.loads(json.dumps(_SAMPLE_LETTER))

    with patch("applire.services.cover_letter.AsyncSessionLocal") as msl, patch(
        "applire.services.cover_letter.get_provider", return_value=mock_provider
    ), patch(
        "applire.services.cover_letter.review_and_refine",
        new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
    ), patch(
        "applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 0
    ), patch(
        "applire.services.cover_letter.resolve_jd_language", return_value="de"
    ), patch(
        "applire.services.cover_letter.extract_recipient_from_jd",
        return_value={"name": None},
    ), patch(
        "applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()
    ), patch(
        "applire.services.cover_letter_pdf.render_pdf",
        new=AsyncMock(return_value=b"%PDF-fake"),
    ), patch.object(
        kl, "refresh_ledger_against_vault", spy
    ):
        msl.return_value.__aenter__.return_value = mock_db
        from applire.services.cover_letter import _render_cover_letter_background

        await _render_cover_letter_background(cl_id=cl_id, cv_id=cv_id, job_id=job_id)
    return seen


@pytest.mark.asyncio
async def test_letter_generation_seam_refreshes_the_ledger_it_hands_the_writer():
    seen = await _run_letter([dict(r) for r in _STALE_LEDGER])

    assert seen, (
        "the letter generation path never refreshed its ledger against the vault "
        "(seam 'letter generation' was not reached)"
    )
    rows = {e["concept"]: e for e in seen[0]}
    assert rows["REST APIs"]["claimable"] is True
    assert _VAULT_SENTENCE in rows["REST APIs"]["evidence"]
    assert rows["GraphQL"]["claimable"] is False
