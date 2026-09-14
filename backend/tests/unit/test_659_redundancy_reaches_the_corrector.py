# Copyright (C) 2026 Tobias Rosenbaum
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

"""#659 (ruling W-1) — a redundant delivered pair reaches the CORRECTOR, not only the report.

The 2026-09-10 and 2026-09-11 delivery runs both shipped a CV whose persisted
`tailored_data` carries a redundant bullet pair AND whose `duplicate-bullets` check
reads `fail`. The check fires at delivery, one stage after the last round that could
have done anything about it. This module pins the transport that closes that gap:
the pairs `redundant_bullet_pairs` computes are handed to the corrector as deterministic
signal issues through the ADR-083 clause-4 channel, bounded, and never as a cut.

The document below is the real 2026-09-11 `work_history[0]`, verbatim.
"""
from __future__ import annotations

import pytest

from applire.services.bullet_redundancy_signal import (
    REDUNDANCY_ISSUE_LIMIT,
    redundancy_signal_issues,
    redundancy_signal_issues_fn,
)
from applire.services.corrector_feedback import fold_issues_into_feedback

_B0 = (
    "Führung von zwei Fertigungsbereichen mit 38 Mitarbeitenden über drei Schichtleiter "
    "im Dreischichtbetrieb; als Eskalationsinstanz für Kundenreklamationen klare "
    "Entscheidungen nach Einbeziehung der Schichtleiter getroffen."
)
_B1 = (
    "Shopfloor-Management und KVP-Routinen eingeführt und dadurch die Ausschussquote "
    "von 4,1 % auf 2,3 % gesenkt; überarbeitete Feinplanung und Rüstworkshops nach SMED "
    "verbesserten die Termintreue von 87 % auf 96 %."
)
_B4 = (
    "Lean-Produktion mit Shopfloor-Management, KVP und SMED in der Fertigung umgesetzt."
)


def _document(bullets: list[str]) -> dict:
    return {
        "contact": {"name": "Stefan Brandt"},
        "summary": "Produktionsleiter.",
        "work_history": [
            {
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2017-04",
                "bullets": bullets,
                "projects": [],
            }
        ],
        "projects": [],
        "skills": [],
    }


def test_the_delivered_redundant_pair_becomes_a_corrector_issue():
    issues = redundancy_signal_issues(_document([_B0, _B1, _B4]))
    assert len(issues) == 1, [i.text for i in issues]
    text = issues[0].text
    # It must name BOTH locations: the corrector patches the prose draft, and one of the
    # two may sit in a composed section it cannot reach (SF-WRITE.29).
    assert text.count("Weberit Kunststofftechnik GmbH / Produktionsleiter") == 2, text
    assert "Shopfloor-Management" in text


def test_the_signal_is_blocking_so_the_transport_renders_it():
    """`corrector_feedback.render_blocking_issues` filters to blocking by design, so a
    `minor` signal issue would be computed every round and silently dropped — the
    'partial consumption reads as consumption' defect ADR-083 exists to close."""
    issues = redundancy_signal_issues(_document([_B0, _B1, _B4]))
    assert all(i.is_blocking for i in issues)
    folded = fold_issues_into_feedback("Reviewer prose.", issues)
    assert folded.startswith("Reviewer prose.")  # prose first (ADR-083 constraint 3)
    assert "- Fix: two delivered bullets state the same achievement" in folded


def test_the_demand_never_says_delete_and_protects_the_unique_detail():
    """Ruling W-1 refused deterministic deletion; the family is labelled
    `triage:document-harm` because its failure mode is a silently missing achievement.
    A corrector told only 'remove the duplicate' has no reason to preserve the figure
    that lives in exactly one of the two."""
    text = redundancy_signal_issues(_document([_B0, _B1, _B4]))[0].text
    low = text.lower()
    assert "merge" in low
    assert "survive in what you keep" in low
    assert "delete" not in low


def test_the_signal_is_bounded():
    """`limit_grounding`'s rule: an unbounded deterministic demand is exhaustion fuel.
    Five mutually-redundant bullets are ten pairs; the corrector sees the bound."""
    many = [
        f"Shopfloor-Management und KVP-Routinen in der Fertigung umgesetzt, Variante {n}, "
        "Ausschussquote gesenkt und Termintreue verbessert."
        for n in range(5)
    ]
    issues = redundancy_signal_issues(_document(many))
    assert len(issues) == REDUNDANCY_ISSUE_LIMIT, len(issues)


def test_a_clean_document_produces_no_demand():
    """The negative control: a signal that fires on every document is a signal the
    corrector learns to ignore."""
    assert redundancy_signal_issues(_document([_B0, _B1])) == []


def test_the_signal_never_mutates_the_document():
    """ADR-082 clauses 1-3 for the deterministic layer: it writes a sentence, never a
    cut."""
    doc = _document([_B0, _B1, _B4])
    import copy

    before = copy.deepcopy(doc)
    redundancy_signal_issues(doc)
    assert doc == before


@pytest.mark.parametrize("bad", [None, {}, {"work_history": None}, {"work_history": [None]}])
def test_a_malformed_document_is_a_silent_no_op(bad):
    """Fail-safe direction: a signal may never break the review loop. A lost signal
    ships what the loop would have shipped anyway; a raised exception loses the round."""
    assert redundancy_signal_issues(bad) == []


def test_the_bound_function_recomputes_per_round_and_needs_no_memory():
    """'A bound replaces verdict memory': the demand is derived from the CURRENT draft
    each round, so a pair the corrector has since merged simply stops being demanded."""
    composed: dict[str, list[str]] = {"bullets": [_B0, _B1, _B4]}
    fn = redundancy_signal_issues_fn(
        structured_document_fn=lambda _draft: _document(composed["bullets"])
    )
    assert len(fn({"work": []})) == 1
    composed["bullets"] = [_B0, _B1]  # the corrector merged the restatement away
    assert fn({"work": []}) == []


def test_the_composed_document_is_the_subject_not_the_prose_draft():
    """#659's six bullets never existed in any writer output — all three generator
    rounds emitted `"projects": []` and `_nest_projects` assembled them afterwards. A
    signal reading the prose draft would see nothing, every round, forever."""
    prose_draft = {"work": [{"company": "Weberit", "bullets": [_B1, _B4]}]}
    # The prose shape uses "work", not "work_history" — the enumeration finds nothing.
    assert redundancy_signal_issues(prose_draft) == []
    # The composed shape is what carries the redundancy.
    assert redundancy_signal_issues(_document([_B1, _B4]))


def test_the_cv_terminal_chain_passes_both_signals_through_one_callable():
    """The seam: `review_and_refine` takes ONE `signal_issues_fn`, and the CV terminal
    chain now has two sources. If the composition is dropped, the under-claim signal
    keeps working and this one silently disappears — which is why the wiring is pinned
    by name rather than by behaviour alone."""
    import inspect

    from applire.services import cv as cv_module

    src = inspect.getsource(cv_module)
    assert "signal_issues_fn=_terminal_signal_issues" in src
    assert "_terminal_signal_issues" in src
    assert "redundancy_signal_issues_fn" in src
    # and the combined demand is bounded by construction
    from applire.services.cv_gap_hints import UNDERCLAIM_ISSUE_LIMIT

    assert REDUNDANCY_ISSUE_LIMIT + UNDERCLAIM_ISSUE_LIMIT <= 6
