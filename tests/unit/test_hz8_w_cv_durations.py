# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:190 / 786:155 / 786:210 / 786:226 (Hazelnut build 8, WP-W): the CV's one
duration channel.

Under ``CV_PIPELINE=v2`` the writer and every review round read the vault with a
skill's DERIVED span removed (``derived_spans="drop"``), and the EXPERIENCE DURATIONS
block (``cv_v2.cv_durations_block``) is the only place a duration reaches them: the
candidate's own figures as stated, a hedge only for a span nobody stated, and no hedge
above the candidate's own largest figure (W-2, recommendation A). Synthetic shapes;
the replays it rests on are ux-audit-4 rec 115 (Sonnet) and ux-audit-3 rec 180 (luna).
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.services.cv_v2 import cv_durations_block
from applire.services.prompt_view import own_duration_statements

_spec = importlib.util.spec_from_file_location(
    "_seam593", Path(__file__).with_name("test_593_prompt_facing_profile_view.py")
)
_seam593 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seam593)


def _ym(years_ago: float) -> str:
    today = date.today()
    y, m = divmod(today.year * 12 + (today.month - 1) - int(round(years_ago * 12)), 12)
    return f"{y:04d}-{m + 1:02d}"


def _profile() -> dict:
    """Synthetic twin of the Felix shape: own "8 years", a student job 11 years ago,
    PostgreSQL tagged in two roles spanning ~9.4 years, Django only in the current one."""
    return {
        "personal_info": {"name": "Ilka Brandt", "email": "ilka@example.com"},
        "professional_summary": {
            "en": "Backend engineer with 8 years of experience building Python services. "
                  "Enjoys mentoring.",
        },
        "work_experience": [
            {"id": "w1", "company": "Ostwind Freight GmbH", "role": "Senior Backend Engineer",
             "start_date": _ym(5.7), "end_date": None, "is_current": True,
             "responsibilities": ["Operate the tracking backend."],
             "technologies": ["Python", "Django", "PostgreSQL"]},
            {"id": "w2", "company": "Lindqvist Billing AG", "role": "Backend Engineer",
             "start_date": _ym(9.4), "end_date": _ym(5.8),
             "responsibilities": ["Built REST APIs for invoicing."],
             "technologies": ["Python", "PostgreSQL"]},
            {"id": "w3", "company": "Uni-IT Service", "role": "Werkstudent",
             "start_date": _ym(11.1), "end_date": _ym(9.5),
             "responsibilities": ["Developed internal tooling in Python."],
             "technologies": ["Python"]},
        ],
        "skills": [
            {"name": "Python", "category": "technical", "years_experience": 11,
             "source": "computed", "status": "confirmed"},
            {"name": "Django", "category": "technical", "years_experience": 6,
             "source": "computed", "status": "confirmed"},
            {"name": "PostgreSQL", "category": "technical", "years_experience": 9,
             "source": "computed", "status": "confirmed"},
            {"name": "Go", "category": "technical", "years_experience": 4,
             "source": "llm_estimated", "status": "confirmed"},
        ],
    }


def test_own_duration_statements_quote_the_candidates_sentence():
    own = own_duration_statements(_profile())
    assert own[0]["years"] == 8.0 and own[0]["raw"] == "8 years"
    assert own[0]["sentence"].startswith("Backend engineer with 8 years of experience")
    assert own[0]["path"] == "professional_summary.en"


def test_own_duration_statements_ignore_skill_numbers_and_role_labels():
    p = _profile()
    p["work_experience"][0]["role"] = "Engineer (3 years)"
    assert {o["years"] for o in own_duration_statements(p)} == {8.0}


def test_block_states_the_own_figure_and_hedges_only_unstated_spans():
    block = cv_durations_block(_profile(), "en")
    assert '"Backend engineer with 8 years of experience building Python services."' in block
    # R4-1: the own figure wins for the tool it names
    assert 'Python: the candidate\'s own figure — "8 years", or no duration.' in block
    # an unstated span: hedged, never rounded up (5.7 -> over 5 / almost 6)
    assert 'Django: since' in block and '"over 5 years" or "almost 6 years"' in block
    # never the flat computed numbers
    for flat in ("11 years", "6 years", "9 years"):
        assert f'"{flat}"' not in block


def test_W2_a_hedge_longer_than_the_own_figure_is_no_duration():
    block = cv_durations_block(_profile(), "en")
    assert "  - PostgreSQL: no duration." in block
    assert "over 9 years" not in block


def test_without_an_own_figure_the_span_is_hedged_not_bounded():
    p = _profile()
    p["professional_summary"] = {"en": "Backend engineer building Python services."}
    block = cv_durations_block(p, "en")
    assert '"over 9 years"' in block and "own figure" not in block.split("Per tool")[0]


def test_no_instruction_text_inside_a_data_row():
    """14c9f272: the hedge view's ``say`` string was copied into the CV
    ("abgeleiteter Erfahrung"). Data rows carry a name, a date, positions, wordings."""
    rows = [ln for ln in cv_durations_block(_profile(), "de").splitlines() if ln.startswith("  - ")]
    assert rows
    for ln in rows:
        assert not any(w in ln.lower() for w in ("derived", "abgeleitet", "computed", "berechnet", "never"))


def test_german_wordings():
    block = cv_durations_block(_profile(), "de")
    assert '"über 5 Jahre" or "knapp 6 Jahre"' in block
    assert '"8 Jahre"' in block


def test_empty_profile_gives_no_block():
    assert cv_durations_block({}, "en") == ""
    assert cv_durations_block(None, "en") == ""


async def _run(profile_json: dict) -> dict:
    """``test_593``'s background-task harness, capturing the writer's kwargs too."""
    m = _seam593
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_cv = MagicMock(); mock_cv.status = "pending"; mock_cv.target_pages = 2
    mock_cv.document_language = "en"
    mock_profile = MagicMock(); mock_profile.profile_json = profile_json
    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []; mock_gap.critical_gaps = []; mock_gap.keyword_ledger = []
    mock_job = m._job_mock()
    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile}[id_]
    mock_result = MagicMock(); mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.side_effect = m._execute_by_entity(mock_cv, mock_result, job_id)
    captured: dict = {}

    async def fake_writer(*args, **kwargs):
        captured["writer_profile"] = args[1] if len(args) > 1 else kwargs.get("profile")
        captured["v2_fields_block"] = kwargs.get("v2_fields_block")
        return m._tailored_raw()

    async def fake_loop(**kw):
        captured.setdefault("loop_sources", []).append(kw.get("source"))
        return kw["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as sl, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_writer), \
         patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        sl.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background
        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


def _skills(view: dict) -> dict:
    return {s["name"]: s for s in view.get("skills") or []}


@pytest.mark.asyncio
async def test_SEAM_v2_writer_and_every_review_round_get_the_one_duration_channel(monkeypatch):
    monkeypatch.setenv("CV_PIPELINE", "v2")
    p = _seam593._profile_json()
    p.update({k: v for k, v in _profile().items() if k in ("professional_summary", "skills")})
    captured = await _run(p)
    skills = _skills(captured["writer_profile"])
    assert "years_experience" not in skills["Python"]            # computed: dropped
    assert "years_experience" not in skills["Go"]                # llm_estimated: dropped
    assert "EXPERIENCE DURATIONS" in (captured["v2_fields_block"] or "")
    sources = [s for s in captured.get("loop_sources", []) if s]
    assert sources and all("EXPERIENCE DURATIONS" in s for s in sources)
    assert all('"years_experience": 11' not in s for s in sources)


@pytest.mark.asyncio
async def test_SEAM_v1_keeps_the_plain_view_and_no_block(monkeypatch):
    monkeypatch.setenv("CV_PIPELINE", "v1")
    p = _seam593._profile_json()
    p.update({k: v for k, v in _profile().items() if k in ("professional_summary", "skills")})
    captured = await _run(p)
    assert _skills(captured["writer_profile"])["Python"]["years_experience"] == 11
    assert not captured.get("v2_fields_block")
