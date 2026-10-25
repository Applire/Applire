# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:237 (Hazelnut build 4) — after "keep x+1" the page-length finding is decided.

The x+1 card's "keep" (``page_overflow.choice``, L-3) is the person's decision on
the length; the review surface still listed the failing page-length check. The
audit now says the decision (an advisory ``page-length-kept`` row) for the length
the choice covered — and only that length."""
import inspect

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text
from applire.services.cv import _pages_kept_at

CV = TailoredCVData.model_validate({"contact": {"name": "Max Muster"}, "summary": "", "work_history": [], "skills": []})


def _page_check(page_count, kept_at, target=2):
    r = _audit_cv_text("max muster", CV, [], page_count=page_count, target=target, pages_kept_at=kept_at)
    return next(c for c in r.checks if c.id == "page-length")


def test_without_a_keep_the_over_target_length_fails():
    c = _page_check(3, None)
    assert (c.status, c.details_key) == ("fail", "page-length-target-missed")


def test_a_keep_decides_the_length_it_covered():
    c = _page_check(3, 3)
    assert (c.status, c.details_key, c.details_params) == ("pass", "page-length-kept", {"pages": 3, "target": 2})


def test_a_cv_that_grew_past_the_kept_length_is_not_decided():
    assert _page_check(4, 3).status == "fail"


def test_a_length_inside_the_norm_is_a_plain_pass_whatever_was_kept():
    c = _page_check(2, 3)
    assert (c.status, c.details_key) == ("pass", None)


def test_pages_kept_at_reads_only_a_keep():
    assert _pages_kept_at({"choice": "keep", "full_pages": 3, "actual": 2}) == 3
    assert _pages_kept_at({"choice": "keep", "actual": 3}) == 3
    assert _pages_kept_at({"choice": "cut", "full_pages": 3}) is None
    assert _pages_kept_at({"choice": None, "full_pages": 3}) is None
    assert _pages_kept_at(None) is None


def test_the_cv_audit_door_passes_the_choice():
    from applire.services import cv

    src = inspect.getsource(cv._update_ats_report)
    assert "pages_kept_at=_pages_kept_at(" in src


def test_the_keep_door_without_new_data_decides_the_stored_report():
    """The "keep" with nothing cut commits without a re-audit; the stored report's
    page-length row is updated by the same predicate."""
    from applire.services import cv
    from applire.services.ats_audit import decide_kept_page_length

    stored = {"checks": [
        {"id": "contact-name", "status": "pass", "details": None},
        {"id": "page-length", "status": "fail", "details": "3 pages — couldn't condense",
         "details_key": "page-length-target-missed", "details_params": {"pages": 3, "target": 2}},
    ], "keywords": {}}
    out = decide_kept_page_length(stored, 3)
    assert out["checks"][0] == stored["checks"][0]
    assert (out["checks"][1]["status"], out["checks"][1]["details_key"]) == ("pass", "page-length-kept")
    assert decide_kept_page_length(stored, None) is stored
    assert decide_kept_page_length(stored, 2)["checks"][1]["status"] == "fail"
    src = inspect.getsource(cv)
    i = src.index('po["choice"] = choice')
    assert "decide_kept_page_length(record.ats_report, _pages_kept_at(po))" in src[i : i + 600]
