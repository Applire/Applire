# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G36 fix round 3, X-2b (adv re-probe 1): after "Passt so" the headline said 1 while
the kept statements stayed on screen as open "Stimmt das?" cards ("1 VON 8"). The
decided and the undone state are served through the real door (``get_cv_ats_report``
with the ``review_state`` the REST keep/undo writes: ``with_decision(..., "kept",
basis=…)`` and ``without_decision``) and captured as
``frontend/lib/__tests__/fixtures/served-{kept,undone}-cv.json``; the frontend test
``review-served-decided.test.ts`` reads those files.
``APPLIRE_UPDATE_SERVED_FIXTURES=1`` rewrites them."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import review_signals  # noqa: E402
from applire.services import review_state as rs  # noqa: E402
from test_dh_fix2_served_one_count import _FIXTURES, _record  # noqa: E402


def _kept_state(rec):
    basis = review_signals.decision_basis(review_signals.open_points_check(rec))
    return rs.with_decision(rs.load_state(None), review_signals.TERMINAL_REVIEW_KEY, "terminal-review", "kept",
                            basis=basis, with_basis=True)


async def _serve(rec) -> dict:
    from applire.services import cv as svc

    with patch.object(svc, "_load_cv", AsyncMock(return_value=rec)):
        resp = await svc.get_cv_ats_report(rec.id, AsyncMock(), user_id=uuid.uuid4())
    return {"ats": json.loads(resp.model_dump_json()), "truth": rec.truthfulness_report}


def _stable(state):
    """The decision timestamp is the only moving part of a captured state."""
    for d in (state or {}).get("decisions") or []:
        d["at"] = "2026-11-25T12:00:00+00:00"
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["kept", "undone"])
async def test_the_decided_and_undone_fixtures_are_the_served_response(which):
    rec = _record("cv")
    state = _kept_state(rec)
    if which == "undone":
        state = rs.without_decision(state, review_signals.TERMINAL_REVIEW_KEY)
    rec.review_state = _stable(state)
    wire = await _serve(rec)
    assert (review_signals.open_points_pending(rec) is None) is (which == "kept")
    text = json.dumps(wire, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path = _FIXTURES / f"served-{which}-cv.json"
    if os.environ.get("APPLIRE_UPDATE_SERVED_FIXTURES") == "1":
        path.write_text(text, encoding="utf-8")
    if not _FIXTURES.parent.parent.parent.is_dir():
        pytest.skip("frontend tree not checked out")
    assert path.read_text(encoding="utf-8") == text


def test_the_kept_state_releases_the_download_and_undo_holds_it_again():
    rec = _record("cv")
    rec.review_state = _kept_state(rec)
    assert review_signals.open_points_pending(rec) is None
    rec.review_state = rs.without_decision(rec.review_state, review_signals.TERMINAL_REVIEW_KEY)
    assert review_signals.open_points_count(review_signals.open_points_pending(rec)) >= 1
