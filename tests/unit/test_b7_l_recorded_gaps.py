# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — RULING L-1 narrowed by L-2 = B (#820, ADR-075 amended 2026-11-07).

The letter names only the candidate's RECORDED DENIALS. A never-asked key requirement,
a question the candidate walked away from, a member of an answered topic and a
containment-floored row all stay out of the letter (as DEL-2 before G-1).

UX audit round 3 (gpt-6-luna): the skip-path letter said "I have not worked with
Apache Kafka" for a candidate who ran a Kafka pipeline and was never asked; Priya's
letter said "SBAR is new to me" — SBAR was never named by her. Both shapes are rebuilt
here with synthetic data. #821 (G-1 of build 7): a denied row is named in the
candidate's own scope wording, never the bare concept.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cross_document import (  # noqa: E402
    letter_gap_ladder,
    recorded_ladder_rows,
    render_gap_ladder_block,
)

JD = (
    "Senior Backend Engineer (Payments).\n"
    "Your profile:\n"
    "- Apache Kafka in production\n"
    "- Kubernetes\n"
    "- SBAR-style structured handovers\n"
    "- PSD2 licence experience\n"
)


def _row(concept, status="gap", weight=1.0, claimable=False, **over):
    row = {
        "concept": concept, "surface_forms": [], "status": status,
        "claimable": claimable, "fit_weight": weight, "sources": ["required"],
    }
    row.update(over)
    return row


def _ledger():
    return [
        _row("Apache Kafka"),                       # never asked (skip path)
        _row("Kubernetes"),                         # asked, walked away (L-2 = B: out)
        _row("SBAR"),                               # member of an ANSWERED topic
        _row("PSD2", status="denied", denied_by="PSD2 licence"),  # a recorded no, scoped
        _row("Python", status="direct", claimable=True),
    ]


DENIED = [{"concept": "PSD2 licence", "statement": "A PSD2 licence I have never held."}]


def test_only_a_recorded_denial_survives_the_filter():
    rows = recorded_ladder_rows(_ledger())
    assert [r["concept"] for r in rows] == ["PSD2", "Python"]


def test_a_containment_floored_row_is_not_recorded():
    from applire.services.keyword_ledger import DENIAL_FLOOR_EVIDENCE

    floored = _row("ECMO", evidence=DENIAL_FLOOR_EVIDENCE, denied_by="ECMO certification")
    assert recorded_ladder_rows([floored]) == []


def test_skip_path_names_no_gap_at_all():
    # Felix, skip path: no denial on file → the letter names nothing; no block.
    key_gaps, named = letter_gap_ladder(_ledger()[:3], [], JD)
    assert key_gaps == [] and named == []
    assert render_gap_ladder_block(key_gaps, named, "en") == ""


def test_the_ladder_names_the_denial_in_its_own_scope_and_never_an_unasked_gap():
    key_gaps, named = letter_gap_ladder(_ledger(), DENIED, JD)
    assert [g.label for g in key_gaps] == ["PSD2 licence"]
    assert [g.label for g in named] == ["PSD2 licence"]
    block = render_gap_ladder_block(key_gaps, named, "en")
    assert "PSD2 licence — the candidate's own words (recorded)" in block
    for unasked in ("Apache Kafka", "Kubernetes", "SBAR"):
        assert unasked not in block
    assert "was not asked about it" not in block
    assert "This posting has 1 key requirement(s) the candidate has said they do not have" in block


def test_a_denied_row_without_scope_wording_keeps_its_concept_label():
    ledger = [_row("PSD2 licence", status="denied", denied_by="PSD2 licence")]
    _, named = letter_gap_ladder(ledger, DENIED, JD)
    assert [g.label for g in named] == ["PSD2 licence"]


def test_unasked_rows_never_lift_the_count_into_the_one_gap_mode():
    # Three never-asked gaps + one denial: before L-1 the count was 4 (≥ 3 → name only
    # the most urgent, which could be an unasked one). Now the count is 1.
    ledger = [_row("Apache Kafka"), _row("Kubernetes"), _row("SBAR"),
              _row("PSD2", status="denied", denied_by="PSD2 licence")]
    key_gaps, named = letter_gap_ladder(ledger, DENIED, JD)
    assert len(key_gaps) == 1 and named[0].label == "PSD2 licence"


def test_seam_the_generation_run_computes_the_ladder_through_letter_gap_ladder():
    from applire.services import cover_letter as svc

    src = inspect.getsource(svc._render_cover_letter_body)
    assert "letter_gap_ladder(" in src
    assert "key_requirement_gaps(keyword_ledger" not in src


# ── G-1 ripple (relayed from G): a row tied to a recorded "no" only by containment ──
# Since G-1 such a row no longer floors (status ``gap``, ``evidence == ""``), so the
# bare ledger predicate counts it as ADR-074 Restfall. In the letter that changes
# nothing: the unaddressed-requirements selector admits only claimable ADJACENT rows,
# and the ladder only recorded denials — pinned here on the two round-3 shapes.

def _containment_ledger():
    return [
        # Felix: the requirement "Security"; his recorded no was the narrower scope.
        _row("Security"),
        _row("PSD2", status="denied", denied_by="PSD2 security audits"),
        # Priya: the requirement "ECMO"; her recorded no was the certification.
        _row("ECMO"),
        _row("ECMO certification", status="denied", denied_by="ECMO certification"),
    ]


def test_a_containment_only_row_is_never_named_and_never_an_unaddressed_requirement():
    from applire.services.cross_document import find_unaddressed_hard_requirements
    from applire.services.keyword_ledger import is_unasked_requirement

    ledger = _containment_ledger()
    assert is_unasked_requirement(ledger[0]) and is_unasked_requirement(ledger[2])  # the ripple
    letter = {"body": {"paragraphs": ["Dear Hiring Manager,", "I build payment services."]}}
    assert find_unaddressed_hard_requirements(ledger, letter) == []
    assert find_unaddressed_hard_requirements(ledger, None) == []
    jd = JD + "- Security-minded engineering\n- ECMO experience\n"
    denied = [{"concept": "PSD2 security audits", "statement": "PSD2 security audits I have not run."},
              {"concept": "ECMO certification", "statement": "I hold no ECMO certification."}]
    key_gaps, named = letter_gap_ladder(ledger, denied, jd)
    labels = [g.label for g in key_gaps]
    assert "Security" not in labels and "ECMO" not in labels
    block = render_gap_ladder_block(key_gaps, named, "en")
    assert "Security —" not in block and "\n  - ECMO —" not in block
