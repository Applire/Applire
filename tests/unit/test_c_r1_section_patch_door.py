# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Holds re-probe R-1 (MD-G14): every response that carries a held document's rendered
content is a delivery door, the same rule as D-1.

``PATCH /api/cv/{id}/sections/{section}`` answers ``SectionPatchResponse.html``, the
whole CV re-rendered (print-to-PDF of it is the PDF). The person's own session (the
in-app editor's preview) keeps it — the review surface is where the person reads the
document to decide. An API token (bearer) does not: the saved content awaits its
re-audit (D-4) and the document's open points may be undecided (C-1 / MD-G9).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import audit_state, review_signals  # noqa: E402
from test_c_mdg9_holds import _OPEN_ROW  # noqa: E402
from test_cv_docx_endpoint import SUMMARY, db, ready_cv  # noqa: E402,F401


async def _patch(db, cv, via, content=SUMMARY):  # noqa: F811
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth.deps import require_user, try_admit_model_work
    from applire.db.session import get_db
    from applire.routers.cv import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(id=cv.user_id)
    app.dependency_overrides[try_admit_model_work] = lambda: MagicMock(name="door")
    headers = {"Authorization": "Bearer apl_x"} if via == "bearer" else {}
    with patch("applire.services.cv._update_ats_report_by_id", new=AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            return await ac.patch(
                f"/api/cv/{cv.id}/sections/introduction", json={"content": content}, headers=headers,
            )


async def _held_cv(db, cv):  # noqa: F811
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_section_editor import build_content_snapshot

    cv.content_snapshot = build_content_snapshot(TailoredCVData.model_validate(cv.tailored_data))
    cv.ats_report = {"checks": [dict(_OPEN_ROW)]}
    await db.commit()
    assert review_signals.open_points_pending(cv) is not None
    return cv


@pytest.mark.asyncio
async def test_r1_a_bearer_save_on_a_held_cv_gets_no_rendered_document(db, ready_cv):  # noqa: F811
    cv = await _held_cv(db, ready_cv)
    r = await _patch(db, cv, "bearer")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("html"), "the held CV's rendered html reached an API token"
    assert body["html_withheld"] == "audit_pending"
    assert body["overrides_applied"] == ["introduction"]


@pytest.mark.asyncio
async def test_r1_the_owners_in_app_editor_keeps_its_preview(db, ready_cv):  # noqa: F811
    cv = await _held_cv(db, ready_cv)
    r = await _patch(db, cv, "session")
    assert r.status_code == 200
    assert "<html" in r.json()["html"].lower() and r.json().get("html_withheld") is None


@pytest.mark.asyncio
async def test_r1_twice_in_a_row_and_on_a_decided_cv_the_bearer_still_gets_no_html(db, ready_cv):  # noqa: F811
    """The save itself makes the content pending (D-4): a bearer never reads freshly
    saved, unaudited content — also when the points had been decided."""
    cv = await _held_cv(db, ready_cv)
    cv.review_state = {"decisions": [{
        "finding_key": review_signals.TERMINAL_REVIEW_KEY, "action": "kept",
        "basis": review_signals.decision_basis(review_signals.open_points_check(cv)),
    }]}
    await db.commit()
    assert review_signals.open_points_pending(cv) is None
    for content in (SUMMARY, SUMMARY + " Ich habe außerdem 40 Leute geführt."):
        r = await _patch(db, cv, "bearer", content)
        assert r.status_code == 200 and not r.json().get("html")
        assert r.json()["html_withheld"] == "audit_pending"
    await db.refresh(cv)
    assert audit_state.pending(cv)


def test_r1_the_letter_section_patch_carries_no_rendered_content():
    """Letter twin: ``PATCH /api/cover-letter/{id}/section`` answers an id, the section
    and a status — no rendered content, so it is not a delivery door (enumeration)."""
    from applire.schemas.cover_letter import SectionOverridePatchResponse

    assert set(SectionOverridePatchResponse.model_fields) == {"cover_letter_id", "section", "status"}


@pytest.mark.asyncio
async def test_r1_without_a_pending_marker_an_undecided_cv_is_still_withheld(db, ready_cv):  # noqa: F811
    """The second guard: should a save ever not mark the content pending, the undecided
    open points alone withhold the html from an API token."""
    cv = await _held_cv(db, ready_cv)
    with patch("applire.services.audit_state.mark_pending"):
        r = await _patch(db, cv, "bearer")
    assert r.status_code == 200 and not r.json().get("html")
    assert r.json()["html_withheld"] == "open_points_undecided"
