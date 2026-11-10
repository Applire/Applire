# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:75 / 786:252 / 786:111 (Hazelnut build 8) — the duplicate-bullet pairs
stop pairing two different facts.

The pairs reach the corrector with "merge them, or drop the weaker wording"
(`bullet_redundancy_signal`), so a false pair risks deleting a true achievement.
Three shapes, each a fact about the strings (ADR-062 cl. 1):

* two bullets under two DIFFERENT employers are two facts (the same task done at
  two companies) — never compared; a standalone project is still compared with
  every bullet (#424's nested-and-standalone copy);
* two bullets that each state a year, or each a percentage, and share none of
  them state two facts;
* "including" is a preposition — it no longer counts as a shared content token.

Measured on 145 human-labelled flagged pairs (two blind raters, 143/145 agreed):
the synthetic 93 go from precision 0.63 to 0.83 at recall 1.00 (22 pairs removed,
all labelled distinct); the one delivered CV of the set that shipped
`duplicate-bullets: fail` (a cross-employer nursing pair) now passes.

Synthetic text only.
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text, bullets_prose_dupe, redundant_bullet_pairs
from applire.services.bullet_redundancy_signal import redundancy_signal_issues

_SAME_TASK = "Monatsabschluss nach HGB erstellt und Kostenstellenrechnung in SAP CO gepflegt."


def _cv(work: list[dict], projects: list[dict] | None = None) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Lena Vogt"},
        "summary": "Controllerin.",
        "work_history": [
            {"company": w["company"], "role": w.get("role", "Controllerin"), "start_date": "2018-01",
             "bullets": w["bullets"], "projects": w.get("projects", [])}
            for w in work
        ],
        "projects": projects or [],
        "skills": [],
    })


def _text(cv: TailoredCVData) -> str:
    return "\n".join(
        [cv.contact.name or "", cv.summary or ""]
        + [b for w in cv.work_history for b in (w.bullets or [])]
        + [b for p in (cv.projects or []) for b in (p.bullets or [])]
    )


def test_the_same_task_at_two_employers_is_two_facts():
    cv = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]},
        {"company": "Brennwerk Antriebe AG", "bullets": [_SAME_TASK]},
    ])
    assert bullets_prose_dupe(_SAME_TASK, _SAME_TASK)  # the predicate still says "same words"
    assert redundant_bullet_pairs(cv) == []


def test_the_same_task_twice_under_one_employer_is_still_a_pair():
    cv = _cv([
        {"company": "Halder Maschinenbau GmbH", "role": "Controllerin", "bullets": [_SAME_TASK]},
        {"company": "Halder Maschinenbau GmbH", "role": "Junior-Controllerin", "bullets": [_SAME_TASK]},
    ])
    assert len(redundant_bullet_pairs(cv)) == 1


def test_one_employer_written_with_and_without_its_legal_form_is_one_employer():
    """ADV-3 (build 8 adversarial pass): the scope is the vault's same-employer rule
    (legal-form tolerant), not the raw company string — two stations at "Halder
    Maschinenbau GmbH" / "Halder Maschinenbau" are one employer, so a repeated
    bullet there is still a pair."""
    for second in ("Halder Maschinenbau", "Halder Maschinenbau AG", "HALDER MASCHINENBAU GmbH & Co. KG"):
        cv = _cv([
            {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]},
            {"company": second, "bullets": [_SAME_TASK]},
        ])
        assert len(redundant_bullet_pairs(cv)) == 1, second
    # a company that only shares a first word is still another employer
    cv = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]},
        {"company": "Halder Logistik GmbH", "bullets": [_SAME_TASK]},
    ])
    assert redundant_bullet_pairs(cv) == []


def test_a_standalone_project_is_compared_with_every_employer():
    cv = _cv(
        [{"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]}],
        projects=[{"name": "Abschlussprojekt", "bullets": [_SAME_TASK]}],
    )
    assert len(redundant_bullet_pairs(cv)) == 1


def test_a_nested_project_belongs_to_its_employer():
    cv = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [],
         "projects": [{"name": "SAP-Einführung", "bullets": [_SAME_TASK]}]},
        {"company": "Brennwerk Antriebe AG", "bullets": [_SAME_TASK]},
    ])
    assert redundant_bullet_pairs(cv) == []
    cv_same = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK],
         "projects": [{"name": "SAP-Einführung", "bullets": [_SAME_TASK]}]},
    ])
    assert len(redundant_bullet_pairs(cv_same)) == 1


def test_two_years_with_none_shared_are_two_facts():
    a = "MES-System 2021 für die Spritzgussfertigung mit Pilotlinie und Maschinendatenerfassung ausgewählt."
    b = "MES-System 2023 für die Spritzgussfertigung mit Pilotlinie und Maschinendatenerfassung ausgerollt."
    assert bullets_prose_dupe(a, b)
    assert redundant_bullet_pairs(_cv([{"company": "Halder GmbH", "bullets": [a, b]}])) == []
    same_year = b.replace("2023", "2021")
    assert len(redundant_bullet_pairs(_cv([{"company": "Halder GmbH", "bullets": [a, same_year]}]))) == 1


def test_two_percentages_with_none_shared_are_two_facts():
    a = "Ausschussquote in der Montage durch Shopfloor-Management und KVP-Routinen um 12 % gesenkt."
    b = "Rüstzeit in der Montage durch Shopfloor-Management und KVP-Routinen um 30 % gesenkt."
    assert bullets_prose_dupe(a, b)
    assert redundant_bullet_pairs(_cv([{"company": "Halder GmbH", "bullets": [a, b]}])) == []
    restated = "Ausschussquote durch Shopfloor-Management und KVP-Routinen um 12 % gesenkt."
    assert len(redundant_bullet_pairs(_cv([{"company": "Halder GmbH", "bullets": [a, restated]}]))) == 1


def test_including_is_no_shared_content_word():
    """786:111's shape, synthetic: two unrelated ICU bullets met the 0.40 bar with
    "including" as one of their four shared tokens."""
    a = ("Precept newly qualified nurses, including four nurses through their first ICU year, "
         "teach the ventilation station on the unit's critical-care study day.")
    b = "Provided nursing care in a mixed adult ICU, including mechanical ventilation and tracheostomy care."
    assert not bullets_prose_dupe(a, b)
    # the same pair with the preposition spelled as a content word reaches the bar
    assert bullets_prose_dupe(a.replace("including", "incl-x"), b.replace("including", "incl-x"))


def test_the_ats_check_passes_on_a_cross_employer_pair():
    """Seam 1: the candidate-facing `duplicate-bullets` row."""
    cv = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]},
        {"company": "Brennwerk Antriebe AG", "bullets": [_SAME_TASK]},
    ])
    report = _audit_cv_text(_text(cv), cv, keywords=[])
    check = next((c for c in report.checks if c.id == "duplicate-bullets"), None)
    assert check is None or check.status == "pass", check


def test_the_corrector_is_not_told_to_merge_a_cross_employer_pair():
    """Seam 2: the review loop's deterministic redundancy signal."""
    cross = _cv([
        {"company": "Halder Maschinenbau GmbH", "bullets": [_SAME_TASK]},
        {"company": "Brennwerk Antriebe AG", "bullets": [_SAME_TASK]},
    ])
    assert redundancy_signal_issues(cross.model_dump(mode="json")) == []
    same = _cv([
        {"company": "Halder Maschinenbau GmbH", "role": "Controllerin", "bullets": [_SAME_TASK]},
        {"company": "Halder Maschinenbau GmbH", "role": "Junior-Controllerin", "bullets": [_SAME_TASK]},
    ])
    assert len(redundancy_signal_issues(same.model_dump(mode="json"))) == 1
