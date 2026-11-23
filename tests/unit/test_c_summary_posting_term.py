# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-C (Sorbet invite gate, step 4) — the CV summary obeys the tick list's held-terms
rule: a stamped unheld posting form in the summary is a blocking loop finding
(``cv.summary_posting_term``), the same predicate the chips' ``cv.skill_posting_term``
reads. No Docker, no DB, no LLM."""
import sys
from pathlib import Path
from types import SimpleNamespace

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_v2 import _summary_posting_term_findings  # noqa: E402
from applire.services.finding_registry import REGISTRY  # noqa: E402
from applire.services.posting_terms import POSTING_TERM_OPEN_KEY, UNHELD_FORMS_KEY  # noqa: E402

_LEDGER = [
    {"concept": "Arbeitssicherheitsmanagement", "surface_forms": ["Arbeitssicherheitsmanagement"],
     POSTING_TERM_OPEN_KEY: True, UNHELD_FORMS_KEY: ["Arbeitssicherheitsmanagement"]},
    {"concept": "ISO 45001", "surface_forms": ["ISO 45001"]},  # held, not stamped
]


def _run(summary, ledger=_LEDGER):
    return _summary_posting_term_findings(SimpleNamespace(summary=summary), SimpleNamespace(keyword_ledger=ledger))


def test_an_unticked_posting_word_in_the_summary_is_a_blocking_finding():
    (f,) = _run("Produktionsleiter mit Arbeitssicherheitsmanagement nach ISO 45001.")
    assert f.check == "cv.summary_posting_term" and f.blocking is True
    assert f.subjects == ("Arbeitssicherheitsmanagement",)
    assert "profile's own words" in f.text


def test_held_terms_unstamped_rows_and_an_absent_form_raise_nothing():
    assert _run("Produktionsleiter, Arbeitssicherheit nach ISO 45001.") == []
    assert _run("Arbeitssicherheitsmanagement nach ISO 45001.", ledger=[_LEDGER[1]]) == []
    assert _run("") == []


def test_the_code_is_registered():
    assert "terminal:cv.summary_posting_term" in REGISTRY


def test_v2_checks_raises_the_summary_finding_on_the_composed_draft():
    """M14 kill — the seam: the loop's check function (``v2_checks``) carries the summary
    finding, not only the helper."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_v2 import ComposeNotes, V2CheckContext, v2_checks

    composed = TailoredCVData.model_validate({
        "contact": {"name": "x"},
        "summary": "Produktionsleiter mit Arbeitssicherheitsmanagement nach ISO 45001.",
        "work_history": [], "skills": [],
    })
    ctx = V2CheckContext(profile_json={}, keyword_ledger=_LEDGER, job_dict={}, language="de")
    found = [f for f in v2_checks({}, composed, ComposeNotes(), ctx) if f.check == "cv.summary_posting_term"]
    assert [f.subjects for f in found] == [("Arbeitssicherheitsmanagement",)]
