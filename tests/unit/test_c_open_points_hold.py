# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-C (Sorbet invite gate, C-1 recommendation B): a CV whose reviewed final draft
still has open points is NOT handed out until the person has decided on them.

The Word export is held over the real ASGI stack (409 ``open_points_undecided``); a
``kept`` decision bound to THESE points (basis = ``open_points_hash``) releases it, and
a new set of points voids that decision. The predicate is ONE function
(``review_signals.open_points_pending``) read by the REST door, the agent door and the
review surface's card."""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import review_signals  # noqa: E402
from applire.services.review_signals import TERMINAL_REVIEW_KEY  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_cv_docx_endpoint import client, db, ready_cv  # noqa: E402,F401


def _report(key="terminal-review-open", h="a" * 16, count=2):
    return {"checks": [{
        "id": "terminal-review", "status": "fail", "details_key": key,
        "details_params": {"count": count}, "open_points_hash": h,
    }]}


def _kept(basis):
    return {"walked_at": None, "decisions": [{
        "finding_key": TERMINAL_REVIEW_KEY, "label": "terminal-review", "action": "kept",
        "basis": basis, "at": "2026-11-23T10:00:00+00:00",
    }]}


def _rec(report, state=None):
    return SimpleNamespace(ats_report=report, review_state=state)


def test_open_points_are_pending_until_kept_with_their_own_hash():
    assert review_signals.open_points_pending(_rec(_report())) is not None
    assert review_signals.open_points_pending(_rec(_report(), _kept("a" * 16))) is None
    # a different set of open points (new version) voids the earlier answer
    assert review_signals.open_points_pending(_rec(_report(h="b" * 16), _kept("a" * 16))) is not None


def test_only_a_state_a_verdict_cleared_releases():
    """MD-G9: a state in which no verdict read the delivered draft holds too."""
    for key in ("terminal-review-clean", "terminal-review-observations", "terminal-review-unnamed",
                "terminal-review-not-run", "terminal-review-clean-code-open"):
        assert review_signals.open_points_pending(_rec(_report(key=key))) is None, key
    for key in ("terminal-review-open", "terminal-review-incomplete", "terminal-review-unverified",
                "terminal-review-unverified-clean"):
        assert review_signals.open_points_pending(_rec(_report(key=key))) is not None, key
    assert review_signals.open_points_pending(_rec(None)) is None


def test_hold_raises_with_the_count():
    with pytest.raises(review_signals.DeliveryHeld) as exc:
        review_signals.hold_if_open_points_pending(_rec(_report(count=3)))
    assert exc.value.count == 3


@pytest.mark.asyncio
async def test_word_export_is_409_until_the_person_keeps_the_points(client, db, ready_cv):
    ready_cv.ats_report = _report(count=2)
    await db.commit()
    r = await client.get(f"/api/cv/{ready_cv.id}/docx")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "open_points_undecided", "count": 2}

    ready_cv.review_state = _kept("a" * 16)
    await db.commit()
    r = await client.get(f"/api/cv/{ready_cv.id}/docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_pdf_is_held_too(client, db, ready_cv):
    ready_cv.ats_report = _report(count=1)
    await db.commit()
    r = await client.get(f"/api/cv/{ready_cv.id}/pdf")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "open_points_undecided"


# ---------------------------------------------------------------------------
# The agent door: get_cv_status.open_points + decide_open_points (relay only)
# ---------------------------------------------------------------------------


def test_the_status_envelope_names_the_open_points_and_whether_they_are_decided():
    from applire.mcp.server import _open_points_envelope

    a = "a" * 16
    assert _open_points_envelope(_rec(_report(count=2))) == {"count": 2, "decided": False, "basis": a}
    assert _open_points_envelope(_rec(_report(count=2), _kept(a))) == {"count": 2, "decided": True, "basis": a}
    assert _open_points_envelope(_rec(_report(key="terminal-review-clean"))) is None
    assert _open_points_envelope(_rec(None)) is None


def _db_cm(session):
    from unittest.mock import AsyncMock, MagicMock

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_decide_open_points_records_the_humans_keep_and_releases_the_download(client, db, ready_cv):
    from unittest.mock import patch

    from applire.mcp.server import decide_open_points

    ready_cv.ats_report = _report(count=2)
    await db.commit()
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        out = await decide_open_points(str(ready_cv.id), keep=True, basis="a" * 16)
    assert out["open_points"] == {"count": 2, "decided": True, "basis": "a" * 16}
    assert (await client.get(f"/api/cv/{ready_cv.id}/docx")).status_code == 200

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        out = await decide_open_points(str(ready_cv.id), keep=False)
    assert out["open_points"] == {"count": 2, "decided": False, "basis": "a" * 16}
    assert (await client.get(f"/api/cv/{ready_cv.id}/docx")).status_code == 409


@pytest.mark.asyncio
async def test_decide_open_points_refuses_a_cv_without_open_points(db, ready_cv):
    from unittest.mock import patch

    from mcp.shared.exceptions import McpError

    from applire.mcp.server import decide_open_points

    ready_cv.ats_report = _report(key="terminal-review-clean")
    await db.commit()
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), pytest.raises(McpError) as exc:
        await decide_open_points(str(ready_cv.id), keep=True)
    assert "open_points" in str(exc.value)
