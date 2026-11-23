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
