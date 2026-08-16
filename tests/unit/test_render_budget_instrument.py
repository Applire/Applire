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

"""ADR-076 Amendment 3 §3 — the RENDER_BUDGET_ITERATION instrument on the
measure-and-condense loop in ``_update_ats_report`` (cv.py).

Reuses the loop-level fixtures/patches from ``test_cv_condense_loop.py`` (same
render + page-count seams mocked, same ``_seed_cv``/``_budget`` helpers) so the
instrument is exercised against the SAME scenarios that pin the loop's own
behaviour — this file only adds ``caplog`` assertions on top, it never asserts
loop-shape (bullet counts, ``extract.call_count``) which the sibling file
already owns.
"""
import logging
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_cv_condense_loop import _budget, _patches, _seed_cv, db  # noqa: E402,F401

_LOGGER_NAME = "applire.services.cv"

_LINE_RE = re.compile(
    r"RENDER_BUDGET_ITERATION cv_id=(?P<cv_id>\S+) iteration=(?P<iteration>\d+) "
    r"pages_before=(?P<pages_before>\d+) pages_after=(?P<pages_after>\d+) "
    r"target=(?P<target>\d+) condense_fired=(?P<condense_fired>\S+) "
    r"condensation_exhausted=(?P<condensation_exhausted>\S+)"
)


def _render_budget_lines(caplog):
    lines = []
    for r in caplog.records:
        m = _LINE_RE.search(r.getMessage())
        if m:
            d = m.groupdict()
            lines.append({
                "cv_id": d["cv_id"],
                "iteration": int(d["iteration"]),
                "pages_before": int(d["pages_before"]),
                "pages_after": int(d["pages_after"]),
                "target": int(d["target"]),
                "condense_fired": d["condense_fired"] == "True",
                "condensation_exhausted": d["condensation_exhausted"] == "True",
                "levelno": r.levelno,
            })
    return lines


# --- the line appears, with correct fields, when condense fires -------------

@pytest.mark.asyncio
async def test_line_emitted_when_condense_fires_and_meets_target(db, caplog):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([3, 2])  # 3 pages -> condense -> 2 pages
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    lines = _render_budget_lines(caplog)
    assert len(lines) == 2, "iteration 1 (fired) + iteration 2 (re-check, no fire)"

    iter1 = next(l for l in lines if l["iteration"] == 1)
    assert iter1["cv_id"] == str(cv.id)
    assert iter1["pages_before"] == 3
    assert iter1["pages_after"] == 2
    assert iter1["target"] == 2
    assert iter1["condense_fired"] is True
    assert iter1["condensation_exhausted"] is False
    assert iter1["levelno"] == logging.INFO

    iter2 = next(l for l in lines if l["iteration"] == 2)
    assert iter2["pages_before"] == 2
    assert iter2["pages_after"] == 2
    assert iter2["condense_fired"] is False
    assert iter2["condensation_exhausted"] is False


# --- condense never fires: still under target on the first render -----------

@pytest.mark.asyncio
async def test_line_emitted_when_condense_does_not_fire(db, caplog):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([2])  # already at target
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    lines = _render_budget_lines(caplog)
    assert len(lines) == 1
    assert lines[0]["iteration"] == 1
    assert lines[0]["pages_before"] == 2
    assert lines[0]["pages_after"] == 2
    assert lines[0]["condense_fired"] is False
    assert lines[0]["condensation_exhausted"] is False


# --- exhausted: nothing left to cut (changed=False) --------------------------

@pytest.mark.asyncio
async def test_line_emitted_and_warns_when_nothing_left_to_cut(db, caplog):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([4])  # over target, but ceiling already fits every bullet
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(5), 2))

    lines = _render_budget_lines(caplog)
    assert len(lines) == 1
    assert lines[0]["iteration"] == 1
    assert lines[0]["pages_before"] == 4
    assert lines[0]["pages_after"] == 4
    assert lines[0]["condense_fired"] is False
    assert lines[0]["condensation_exhausted"] is True
    assert lines[0]["levelno"] == logging.WARNING, "exhaustion warns, like REVIEW_EXHAUSTED"


# --- exhausted after both iterations fired but target never met -------------

@pytest.mark.asyncio
async def test_line_emitted_for_both_iterations_when_max_reached_and_exhausted(db, caplog):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([4, 4, 4])
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    lines = _render_budget_lines(caplog)
    assert len(lines) == 2

    iter1 = next(l for l in lines if l["iteration"] == 1)
    assert iter1["pages_before"] == 4 and iter1["pages_after"] == 4
    assert iter1["condense_fired"] is True
    assert iter1["condensation_exhausted"] is False, "not yet known at iteration 1"
    assert iter1["levelno"] == logging.INFO

    iter2 = next(l for l in lines if l["iteration"] == 2)
    assert iter2["pages_before"] == 4 and iter2["pages_after"] == 4
    assert iter2["condense_fired"] is True
    assert iter2["condensation_exhausted"] is True
    assert iter2["levelno"] == logging.WARNING


# --- audit-only tails never emit the loop line -------------------------------

@pytest.mark.asyncio
async def test_section_editor_reaudit_path_emits_no_line(db, caplog):
    """No CondenseContext -> do_condense=False -> the loop body (and therefore
    the instrument) never runs; this is the section-editor re-audit tail."""
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report

    _, ps = _patches([4])
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, None)

    assert _render_budget_lines(caplog) == []


@pytest.mark.asyncio
async def test_bail_on_existing_overrides_emits_no_line(db, caplog):
    """A CondenseContext IS supplied but section_overrides bail (amendment §1)
    takes do_condense back to False -> audit-only, no loop, no instrument line."""
    cv = await _seed_cv(
        db, n_bullets=5, target_pages=2, section_overrides={"introduction": "Hi"}
    )
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([4])
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert _render_budget_lines(caplog) == []


# --- correlation with bullet_cuts.TAIL_DELETE via the shared iteration field -

@pytest.mark.asyncio
async def test_iteration_field_correlates_with_tail_delete_lines(db, caplog):
    """#3 of the hard limits: RENDER_BUDGET_ITERATION must dock onto the
    existing bullet_cuts TAIL_DELETE lines via a shared field, not duplicate
    cut detail. The shared field is ``iteration`` — same name, same value,
    passed through unchanged to ``condense_to_budget`` -> ``log_cuts``."""
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    _, ps = _patches([3, 2])
    with caplog.at_level(logging.INFO):
        with ps[0], ps[1], ps[2]:
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    render_lines = _render_budget_lines(caplog)
    tail_deletes = [
        r.getMessage() for r in caplog.records if "TAIL_DELETE" in r.getMessage()
    ]
    assert tail_deletes, "condense_to_budget must have cut at least one bullet"
    fired = [l for l in render_lines if l["condense_fired"]]
    assert fired, "at least one RENDER_BUDGET_ITERATION line reports condense firing"

    # Every TAIL_DELETE line from this pass carries iteration=1 (the only
    # condense call this scenario makes) and so does the fired RENDER_BUDGET_
    # ITERATION line -- the shared correlation field, without this line
    # repeating role_id/ceiling/sole_carrier/text, which TAIL_DELETE owns.
    assert all("iteration=1" in t for t in tail_deletes)
    assert fired[0]["iteration"] == 1
    # The new line does not duplicate cut-level detail.
    for l_msg in [m for r in caplog.records if (m := r.getMessage()).startswith(
        "RENDER_BUDGET_ITERATION"
    )]:
        assert "sole_carrier" not in l_msg
        assert "role_id" not in l_msg


# --- a mid-loop render failure never fabricates an "after" value ------------

@pytest.mark.asyncio
async def test_no_line_fabricated_when_reraise_loses_the_after_measurement(db, caplog):
    """When the re-render that would supply iteration 1's ``pages_after`` raises
    (caught by _update_ats_report's outer except, ats_report left NULL), the
    instrument must never invent a pages_after value for the fired iteration —
    it simply has nothing to report for that iteration."""
    from unittest.mock import AsyncMock, MagicMock, patch

    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import CondenseContext, _update_ats_report

    html_mock = AsyncMock(side_effect=["<html></html>", RuntimeError("render boom")])
    extract = MagicMock(side_effect=[("text", 4)])
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with patch("applire.services.cv.get_cv_html", new=html_mock), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
             patch("applire.services.ats_audit.extract_text_and_pages", new=extract):
            await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert cv.ats_report is None
    assert _render_budget_lines(caplog) == []
