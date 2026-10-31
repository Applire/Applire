# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-6 (Hazelnut build 5, founder ruling 2026-10-28): "in case of us needing to
reach the limit, shorten the old jobs". Page-ladder rung 6: a role that ended
more than ``OLD_ROLE_YEARS`` years ago keeps title, company and dates in THIS CV
(no bullets, no nested projects); the profile is untouched. It runs only in the
person's page-length proposal ("Kürzen"), never in the automatic target+1 ladder.
All data synthetic."""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from applire.services.cv_page_ladder import (
    OLD_ROLE_BULLETS,
    OLD_ROLE_YEARS,
    ladder_steps,
    reinsert_cuts,
)


def _cv() -> dict:
    return {
        "contact": {"name": "T", "email": "t@example.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
        "summary": "Lead.", "education": [], "skills": [], "languages": [], "certifications": [],
        "show_photo": False,
        "work_history": [
            {"id": "w1", "company": "Now AG", "role": "Lead", "start_date": "2019", "end_date": None,
             "is_current": True, "bullets": ["Leads 12 people."]},
            {"id": "w2", "company": "Mid GmbH", "role": "Architect", "start_date": "2011", "end_date": "2019",
             "bullets": ["Designed the LIMS."]},
            {"id": "w3", "company": "Lab Institute", "role": "Research Assistant", "start_date": "2006",
             "end_date": "2008", "bullets": ["Ran assays.", "Wrote SOPs."],
             "projects": [{"name": "Assay study", "bullets": ["Ran 40 assays."]}]},
            {"id": "w4", "company": "Diagnostics Inc", "role": "Technician", "start_date": "2003",
             "end_date": "2005", "bullets": ["Calibrated analysers.", "Trained 5 colleagues."]},
        ],
        "projects": [],
    }


BEFORE = 2026 - OLD_ROLE_YEARS  # 2011: a role that ended 2008 or 2005 is old, 2019 is not


def test_rung_six_shortens_old_roles_oldest_first_and_keeps_their_headers():
    data = _cv()
    steps = [s for s in ladder_steps(data, None, old_roles_before=BEFORE)]
    assert [s.cut.kind for s in steps] == [OLD_ROLE_BULLETS, OLD_ROLE_BULLETS]
    assert steps[0].cut.items == ("Technician — Diagnostics Inc — 2005",)
    assert steps[1].cut.items == ("Research Assistant — Lab Institute — 2008",)
    final = steps[-1].data["work_history"]
    assert final[3]["bullets"] == [] and final[2]["bullets"] == [] and final[2]["projects"] == []
    assert {k: final[2][k] for k in ("role", "company", "start_date", "end_date")} == {
        "role": "Research Assistant", "company": "Lab Institute", "start_date": "2006", "end_date": "2008",
    }
    assert final[0]["bullets"] == ["Leads 12 people."] and final[1]["bullets"] == ["Designed the LIMS."]
    assert data == _cv(), "the input (and so the profile) is never mutated"


def test_the_automatic_ladder_never_reaches_rung_six():
    assert not [s for s in ladder_steps(_cv(), None) if s.cut.kind == OLD_ROLE_BULLETS]


def test_a_pinned_bullet_of_an_old_role_stays():
    def pins(entry, texts):
        return {i for i, t in enumerate(texts) if t == "Trained 5 colleagues."}

    steps = list(ladder_steps(_cv(), None, old_roles_before=BEFORE, pinned_bullets=pins))
    w4 = steps[-1].data["work_history"][3]
    assert w4["bullets"] == ["Trained 5 colleagues."]


def test_undo_after_an_edit_puts_the_old_roles_bullets_back():
    full = _cv()
    cut = list(ladder_steps(full, None, old_roles_before=BEFORE))[-1].data
    edited = copy.deepcopy(cut)
    edited["summary"] = "Edited after the cut."
    data, _override, unrestorable = reinsert_cuts(edited, full, cut)
    assert data["summary"] == "Edited after the cut."
    assert data["work_history"][2]["bullets"] == ["Ran assays.", "Wrote SOPs."]
    assert data["work_history"][2]["projects"] == full["work_history"][2]["projects"]
    assert data["work_history"][3]["bullets"] == full["work_history"][3]["bullets"]
    assert unrestorable == []


def _pages(data: dict) -> int:
    """Fake render: one page per three bullets (role + nested project), at least one."""
    n = sum(len(w.get("bullets") or []) + sum(len(p.get("bullets") or []) for p in w.get("projects") or [])
            for w in data.get("work_history") or [])
    return max(1, -(-n // 3))


@pytest.mark.asyncio
async def test_the_page_card_proposes_shortening_old_roles(monkeypatch):
    import applire.services.ats_audit as ats_audit
    import applire.services.cv as cv
    from applire.services.cv import CondenseContext, MeasuredRender

    record = SimpleNamespace(id=uuid.uuid4(), user_id=None, tailored_data=_cv(),
                             section_overrides=None, page_overflow=None)

    async def _html(cv_id, db, *, user_id=None):
        return copy.deepcopy(record.tailored_data)

    async def _pdf(html, **_kw):
        return html

    monkeypatch.setattr(cv, "get_cv_html", _html)
    monkeypatch.setattr(cv, "_html_to_pdf", _pdf)
    monkeypatch.setattr(ats_audit, "extract_text_and_pages", lambda pdf: ("", _pages(pdf)))
    monkeypatch.setattr(cv, "_old_roles_before", lambda: BEFORE)

    full_pages = _pages(_cv())  # 8 bullets → 3 pages
    assert full_pages == 3
    ctx = CondenseContext(budgets=MagicMock(region="DACH"), target=2)
    await cv._propose_page_cut(record, MagicMock(), ctx, MeasuredRender("", full_pages, True, 2, "DACH"))
    po = record.page_overflow
    assert record.tailored_data == _cv(), "the dry run leaves the delivered CV untouched"
    assert po["reaches_target"] is True and po["cut_pages"] == 2
    assert po["proposed"] == [{"kind": OLD_ROLE_BULLETS, "items": ["Technician — Diagnostics Inc — 2005"]}]
    assert po["cut_data"]["work_history"][3]["bullets"] == []
    from applire.schemas.cv import CVPageOverflowCut

    CVPageOverflowCut.model_validate(po["proposed"][0])  # the kind is in the response contract


def test_old_roles_before_is_this_year_minus_the_ruling():
    from datetime import date

    import applire.services.cv as cv

    assert cv._old_roles_before() == date.today().year - 15
