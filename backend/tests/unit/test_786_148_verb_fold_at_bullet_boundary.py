# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:148 (Hazelnut build 8) — the delivered ``narrative-evidence`` check called
"Mentoring" claimed-but-not-evidenced on a CV whose work entry carried "Mentor two
mid-level engineers …" (blind Kaile probe 2026-09-27, three renders).

The check reads the RIGHT corpus (work-entry and project bullets, never the
professional summary the agent suspected). The defect was the shared presence
predicate's verb-form arm over a JOINED corpus: ``join_corpus_fragments`` puts the
fragment boundary between two bullets with no whitespace around it, and the arm
split on whitespace only, so the last word of one bullet and the FIRST word of the
next formed one token ("backend.␞mentor"). A CV bullet's first word is its verb —
exactly the word the fold exists to read.

Synthetic case (the persona shape of the probe, invented employer).
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import (
    _norm,
    _narrative_evidence_check,
    join_corpus_fragments,
    surface_present,
)
from applire.services.cv_gap_hints import underclaim_signal_issues

_MENTOR = "Mentor two mid-level engineers and run the team's weekly design-review session."
_FIRST = "Design and operate the Django-based shipment-tracking backend."


def _cv(bullets: list[str]) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Daniel Novak"},
        "summary": "Backend engineer who enjoys mentoring.",
        "work_history": [{
            "id": "w1",
            "company": "Frachtwerk Logistics GmbH",
            "role": "Senior Backend Engineer",
            "start_date": "2021-03",
            "bullets": bullets,
        }],
        "skills": ["Python", "Django", "Mentoring"],
    })


_LEDGER = [{
    "concept": "Mentoring", "surface_forms": ["Mentoring"], "status": "direct",
    "claimable": True, "fit_weight": 1.0, "evidence": "Mentors two engineers",
}]


def test_the_fold_reads_the_first_word_of_a_later_fragment():
    joined = _norm(join_corpus_fragments([_FIRST, _MENTOR]))
    assert surface_present("Mentoring", _norm(_MENTOR))  # alone it always worked
    assert surface_present("Mentoring", joined)
    assert surface_present("Mentored", _norm(join_corpus_fragments([_FIRST, "Mentoring juniors."])))


def test_the_boundary_still_separates_phrases():
    """#415: two fragments never spell a phrase across their join — unchanged."""
    joined = _norm(join_corpus_fragments(["Zertifiziert nach ISO", "9001 Audits begleitet"]))
    assert not surface_present("ISO 9001", joined)


def test_the_delivered_check_passes_when_a_later_bullet_starts_with_the_verb():
    check = _narrative_evidence_check(_cv([_FIRST, _MENTOR]), _LEDGER)
    assert check.status == "pass", check.details


def test_the_delivered_check_still_fails_when_only_the_tag_carries_it():
    check = _narrative_evidence_check(_cv([_FIRST]), _LEDGER)
    assert check.status == "fail"
    assert check.details_params["tagOnly"] == "Mentoring"


def test_the_corrector_signal_no_longer_demands_a_delivered_concept():
    """The other reader of the same corpus (ADR-076 cl. 5 signal): it demanded a
    bullet the CV already had."""
    draft = _cv([_FIRST, _MENTOR]).model_dump(mode="json")
    assert underclaim_signal_issues(draft, _LEDGER) == []
    assert underclaim_signal_issues(_cv([_FIRST]).model_dump(mode="json"), _LEDGER)
