# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH item 3 (Sorbet invite-gate delivery run, 2026-11-25): My Documents said "Bereit"
for a CV whose download answered 409 ``open_points_undecided``. A list row now carries
``open_points`` — the hold's own predicate and count — so the page can say "Entwurf"."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import review_signals  # noqa: E402
from test_sorbet_l_letter_open_points import _claim, _truth  # noqa: E402
from test_sprint29_documents import _USER_A, _acting_user, _letter_for, _seed_cv, _seed_letter_only, db  # noqa: E402,F401

CLEAN = _truth(_claim("I mentor two junior engineers.", "grounded"))
HELD_ATS = {
    "version": 1, "document": "cv", "passed": 1, "failed": 1, "not_applicable": 0,
    "checks": [{
        "id": "terminal-review", "status": "fail", "details": "x", "details_key": "terminal-review-open",
        "details_params": {"count": 2}, "open_points_hash": "a" * 16,
        "notes": [
            {"kind": "finding", "key": None, "text": "f1", "plain": "P1.", "subjects": []},
            {"kind": "finding", "key": None, "text": "f2", "plain": "P2.", "subjects": []},
            {"kind": "code", "key": "cv.skills_cap", "text": "cap", "subjects": ["S3"]},
        ],
    }],
    "keywords": {"present": [], "missing": [], "present_unsupported": []},
}


@pytest.mark.asyncio
async def test_a_held_cv_row_names_its_open_points(db):
    from applire.services.documents import list_documents

    cv = await _seed_cv(db, _USER_A, "Leiter Operations", "Rheinwerk")
    cv.ats_report = HELD_ATS
    await db.commit()
    (row,) = (await list_documents(user_id=_USER_A, db=db)).items
    assert row.status == "ready"
    assert row.open_points == 2  # the card's count, never the six lines


@pytest.mark.asyncio
async def test_a_kept_cv_row_is_ready_again(db):
    from applire.services.documents import list_documents

    cv = await _seed_cv(db, _USER_A, "Leiter Operations", "Rheinwerk")
    cv.ats_report = HELD_ATS
    basis = review_signals.decision_basis(review_signals.open_points_check(cv))
    cv.review_state = {"walked_at": None, "decisions": [{
        "finding_key": review_signals.TERMINAL_REVIEW_KEY, "label": "terminal-review", "action": "kept",
        "basis": basis, "at": "2026-11-25T10:00:00+00:00",
    }]}
    await db.commit()
    (row,) = (await list_documents(user_id=_USER_A, db=db)).items
    assert row.open_points is None


@pytest.mark.asyncio
async def test_a_clean_cv_row_has_no_open_points(db):
    from applire.services.documents import list_documents

    await _seed_cv(db, _USER_A, "Leiter Operations", "Rheinwerk")
    (row,) = (await list_documents(user_id=_USER_A, db=db)).items
    assert row.open_points is None


@pytest.mark.asyncio
async def test_a_held_letter_names_its_open_points_on_the_cv_row_and_on_its_own_row(db):
    from applire.services.documents import list_documents

    letter_ats = {**HELD_ATS, "document": "cover_letter", "checks": [], "keywords": {
        "present": [], "missing": [], "present_unsupported": ["Maintenance"]}}
    cv = await _seed_cv(db, _USER_A, "Leiter Operations", "Rheinwerk")
    letter = _letter_for(cv, _USER_A, minutes=1)
    letter.letter_data = {"body": {"paragraphs": ["x"]}}
    letter.ats_report = letter_ats
    letter.truthfulness_report = CLEAN
    db.add(letter)
    await db.commit()
    (row,) = (await list_documents(user_id=_USER_A, db=db)).items
    assert row.open_points is None and row.cover_letter.open_points == 1

    _cv2, solo = await _seed_letter_only(db, _USER_A, "Werkleiter", "Nordlicht AG")
    solo.letter_data = {"body": {"paragraphs": ["x"]}}
    solo.ats_report = letter_ats
    solo.truthfulness_report = CLEAN
    await db.commit()
    rows = {r.kind: r for r in (await list_documents(user_id=_USER_A, db=db)).items}
    assert rows["cover_letter"].open_points == 1 and rows["cover_letter"].cover_letter.open_points == 1
