# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#747 (Oracle half) — a stated duration vs the dated roles naming its subject.

Blind Kaile probe 2026-09-19: the delivered letter said "Seit acht Jahren
arbeite ich mit SAP CO/FI"; the only role naming SAP started 7.6 years earlier.
The #469 envelope ceiling (whole career) could not see it and the role union
grounded it. ADR-052 amended 2026-10-07 + founder ruling T-1 (B): a derived
duration may be stated only hedged and rounded down ("über sieben Jahre",
"knapp acht Jahre"); the Oracle checks floor ≤ span.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from applire.services.oracle import audit_document, verify_claim


def _ago(years: float) -> str:
    today = date.today()
    months = int(round(years * 12))
    y, m = divmod(today.year * 12 + (today.month - 1) - months, 12)
    return f"{y:04d}-{m + 1:02d}"


def _profile(**extra) -> dict:
    p = {
        "professional_summary": {"de": "Controllerin mit 9 Jahren Erfahrung im Mittelstand."},
        "work_experience": [
            {"id": "w-sp", "company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
             "start_date": _ago(7.6), "is_current": True,
             "responsibilities": ["Key-Userin SAP CO/FI", "Werkscontrolling für zwei Standorte"],
             "technologies": ["SAP CO", "SAP FI"]},
            {"id": "w-br", "company": "Brauner & Söhne Maschinenbau GmbH", "role": "Junior Controllerin",
             "start_date": _ago(10.1), "end_date": _ago(7.7),
             "responsibilities": ["Mitarbeit im Monatsabschluss"]},
        ],
        "skills": [
            {"name": "SAP CO", "category": "technical", "years_experience": 8, "source": "computed"},
            {"name": "SAP FI", "category": "technical", "years_experience": 8, "source": "computed"},
            {"name": "SAP CO/FI", "category": "technical", "years_experience": 6,
             "source": "llm_estimated"},
        ],
    }
    p.update(extra)
    return p


PROBE = "Seit acht Jahren arbeite ich mit SAP CO/FI und bin Key-Userin."


def _v(text: str, profile: dict | None = None):
    return asyncio.run(verify_claim(text, profile or _profile()))


def test_probe_sentence_is_unbacked_with_the_span_and_its_role():
    v = _v(PROBE)
    assert (v.verdict, v.checker) == ("unbacked", "numbers"), v
    assert "7.6 years" in v.detail and "Schwarzwald Präzision GmbH" in v.detail
    assert v.figures == ["acht Jahren"]


@pytest.mark.parametrize("text", [
    "Seit über sieben Jahren arbeite ich mit SAP CO/FI.",
    "Seit knapp acht Jahren arbeite ich mit SAP CO.",
    "Seit rund acht Jahren arbeite ich mit SAP CO.",
    "Seit 2019 arbeite ich mit SAP CO/FI.",
    "Over seven years of SAP CO experience.",
])
def test_ruling_t1_hedges_within_the_span_are_not_flagged(text):
    assert _v(text).verdict != "unbacked", text


@pytest.mark.parametrize("text", [
    "Über acht Jahre arbeitete ich mit SAP CO und SAP FI.",
    "Eight years of SAP FI experience.",
    "Seit fast zehn Jahren arbeite ich mit SAP CO.",  # 9 is stated (summary) — escape
])
def test_flat_or_overreaching_counts_are_flagged(text):
    assert _v(text).verdict == "unbacked", text


def test_candidates_own_stated_duration_escapes():
    profile = _profile(signature_stories=[])
    profile["work_experience"][0]["achievements"] = ["Seit acht Jahren arbeite ich mit SAP CO."]
    assert _v(PROBE, profile).verdict != "unbacked"


def test_transcribed_skill_duration_escapes():
    profile = _profile()
    profile["skills"][0] = {"name": "SAP CO", "category": "technical",
                            "years_experience": 8, "source": "transcribed"}
    assert _v("Seit acht Jahren arbeite ich mit SAP CO.", profile).verdict != "unbacked"


def test_denial_statement_duration_escapes():
    profile = _profile(metadata={"denied_concepts": [{
        "concept": "IFS", "statement": "Kein IFS, aber acht Jahre SAP-CO-Praxis.",
        "source": "interview", "date": "2026-09-01"}]})
    assert _v("Dafür bringe ich acht Jahre SAP CO mit.", profile).verdict != "unbacked"


def test_a_skill_far_from_the_duration_is_not_its_subject():
    text = ("Ich erfülle die geforderten acht Jahre Berufserfahrung im Controlling, "
            "zusätzlich bringe ich aus meiner aktuellen Rolle Kenntnisse in SAP CO mit.")
    assert _v(text).verdict != "unbacked", _v(text)


def test_no_subject_skill_leaves_it_to_the_envelope_ceiling():
    assert _v("Mit neun Jahren Erfahrung im Controlling.").verdict != "unbacked"


def test_seam_letter_path():
    letter = {"recipient": {"company": "Arnold Antriebe"}, "body": {"paragraphs": [
        "Sehr geehrte Damen und Herren,",
        "Bei der Schwarzwald Präzision GmbH verantworte ich das Werkscontrolling. " + PROBE,
    ]}}
    report = asyncio.run(audit_document("cover_letter", _profile(), letter_data=letter))
    hits = [cr for cr in report.claims if "acht Jahren" in cr.claim.text]
    assert hits and all(cr.verdict.verdict == "unbacked" for cr in hits), hits


def test_seam_cv_path():
    tailored = {"summary": "Controllerin. Seit acht Jahren Key-Userin für SAP CO.",
                "work_history": [{"id": "w-sp", "company": "Schwarzwald Präzision GmbH",
                                  "role": "Financial Controller",
                                  "bullets": ["Acht Jahre SAP FI im Monatsabschluss eingesetzt."]}],
                "skills": []}
    report = asyncio.run(audit_document("cv", _profile(), tailored_data=tailored))
    hits = [cr for cr in report.claims if "cht Jahre" in cr.claim.text]
    assert len(hits) == 2 and all(cr.verdict.verdict == "unbacked" for cr in hits), hits
