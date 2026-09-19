# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#724 — the delivered document is written in ONE language, and nothing checked
it. On the founder's edge UAT a German CV shipped four English work bullets and
eight English "Branche:" lines while both blind reviewers read the document as
unedited German; the `cv_language` reviewer had already exhausted its two rounds
on that very document, and a reviewer's exhaustion is not a delivered signal.

This pins the new `document-language` ATS check (`_audit_cv_text`, driven by the
`document_language` parameter) and its enumeration, `foreign_language_items`,
which walks the summary, every per-role `industry_context` ("Branche") line, and
every bullet `delivered_bullets()` reaches — nested project, standalone project,
and role bullets alike, in one flat pass rather than a handful of per-axis scans.
It also pins the evidence floor in `item_language_mismatch`
(`ITEM_LANGUAGE_MIN_WORDS = 4`): below four words the predicate stays silent even
when the underlying detector would call the fragment English, because a short
item carries too little signal to act on.
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text
from applire.utils.language_detection import item_language_mismatch

# ── genuinely German / genuinely English strings, each verified against
# `detect_language` in a throwaway script before use (see the task report) ────

DE_SUMMARY = (
    "Erfahrener Teamleiter mit langjaehriger Praxis in der Lagerlogistik und "
    "im Prozessmanagement."
)
DE_BULLET_1 = (
    "Verantwortete die Einfuehrung eines neuen Warenwirtschaftssystems fuer "
    "zwoelf Filialen und schulte das Team in den neuen Ablaeufen."
)
DE_BULLET_2 = (
    "Optimierte die Kommissionierprozesse im Zentrallager und reduzierte die "
    "Durchlaufzeit spuerbar."
)
DE_BULLET_3 = (
    "Koordinierte die Zusammenarbeit zwischen Einkauf, Lager und Versand "
    "waehrend der Hauptsaison."
)
DE_INDUSTRY_CONTEXT = "Handel und Logistik mit Schwerpunkt auf Lagerverwaltung."

EN_BULLET_WORK = (
    "Led the migration of the internal ticketing system to a new cloud "
    "platform for the entire support team."
)
EN_INDUSTRY_CONTEXT = "Open-core platform with logistics and shipment tracking."
EN_BULLET_NESTED_PROJECT = (
    "Implemented an automated testing pipeline that reduced deployment time "
    "by half."
)
EN_BULLET_STANDALONE_PROJECT = (
    "Coordinated the rollout of a new customer support platform across "
    "three countries."
)

EN_SUMMARY = (
    "Experienced team lead with a strong background in warehouse logistics "
    "and process management."
)
EN_BULLET_2 = (
    "Optimized picking processes in the central warehouse and noticeably "
    "reduced cycle time."
)
EN_BULLET_3 = (
    "Coordinated collaboration between purchasing, warehouse and shipping "
    "during peak season."
)
EN_INDUSTRY_CONTEXT_CLEAN = "Retail and logistics with a focus on warehouse management."


def _check(report, check_id: str):
    return next((c for c in report.checks if c.id == check_id), None)


def _audit(cv: TailoredCVData, document_language: str | None):
    """Audit through the real seam, text built FROM the document so the
    unrelated presence checks are satisfied and cannot mask what we assert on
    (same construction as test_document_prose_redundancy.py's `_audit`)."""
    parts = [cv.contact.name or "", cv.summary or ""]
    for w in cv.work_history:
        parts.extend(w.bullets or [])
        if w.industry_context:
            parts.append(w.industry_context)
        for p in w.projects or []:
            parts.extend(p.bullets or [])
    for p in cv.projects or []:
        parts.extend(p.bullets or [])
    text = "\n".join(parts)
    return _audit_cv_text(text, cv, keywords=[], document_language=document_language)


def _de_work_entry(**over) -> dict:
    entry = {
        "company": "Nordwest Handels GmbH",
        "role": "Teamleiter Logistik",
        "start_date": "2019-03",
        "bullets": [DE_BULLET_1, DE_BULLET_2],
        "industry_context": DE_INDUSTRY_CONTEXT,
    }
    entry.update(over)
    return entry


def _clean_german_cv() -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann"},
        "summary": DE_SUMMARY,
        "work_history": [_de_work_entry(bullets=[DE_BULLET_1, DE_BULLET_2, DE_BULLET_3])],
        "skills": [],
    })


# ── 1. an English WORK bullet in a German CV ──────────────────────────────────


def test_an_english_work_bullet_fails_the_check_and_names_its_location():
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann"},
        "summary": DE_SUMMARY,
        "work_history": [_de_work_entry(bullets=[DE_BULLET_1, EN_BULLET_WORK])],
        "skills": [],
    })

    check = _check(_audit(cv, "de"), "document-language")

    assert check is not None
    assert check.status == "fail", check.details
    assert "Nordwest Handels GmbH / Teamleiter Logistik" in check.details, check.details
    assert "migration" in check.details, check.details


# ── 2. an English `industry_context` ("Branche") line ─────────────────────────


def test_an_english_industry_context_fails_and_the_location_names_branche():
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann"},
        "summary": DE_SUMMARY,
        "work_history": [
            _de_work_entry(bullets=[DE_BULLET_1], industry_context=EN_INDUSTRY_CONTEXT)
        ],
        "skills": [],
    })

    check = _check(_audit(cv, "de"), "document-language")

    assert check is not None
    assert check.status == "fail", check.details
    assert "Branche" in check.details, check.details
    assert "logistics" in check.details, check.details


# ── 3. nested AND standalone project bullets are both reached ─────────────────


def test_english_bullets_in_nested_and_standalone_projects_are_both_reported():
    """The reason `foreign_language_items` goes through `delivered_bullets`
    rather than a per-axis scan: a project nested under a role and a project
    rendered standalone are two different containers, and both must be seen."""
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann"},
        "summary": DE_SUMMARY,
        "work_history": [_de_work_entry(
            bullets=[DE_BULLET_1],
            projects=[{"name": "Lagerprojekt", "bullets": [EN_BULLET_NESTED_PROJECT]}],
        )],
        "projects": [{"name": "Support-Rollout", "bullets": [EN_BULLET_STANDALONE_PROJECT]}],
        "skills": [],
    })

    check = _check(_audit(cv, "de"), "document-language")

    assert check is not None
    assert check.status == "fail", check.details
    assert "automated testing pipeline" in check.details, check.details
    assert "customer support platform" in check.details, check.details


# ── 4. a clean, entirely German CV passes ──────────────────────────────────────


def test_a_clean_german_cv_passes_with_no_details():
    cv = _clean_german_cv()

    check = _check(_audit(cv, "de"), "document-language")

    assert check is not None
    assert check.status == "pass"
    assert check.details is None


# ── 5. an entirely English CV, document_language="en", passes ─────────────────


def test_an_entirely_english_cv_passes_when_its_own_language_is_english():
    """The check is about AGREEMENT with the document's own language, not about
    German — an English CV tailored for an English-language job must pass."""
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Jane Miller"},
        "summary": EN_SUMMARY,
        "work_history": [{
            "company": "Northwest Trading Ltd",
            "role": "Warehouse Team Lead",
            "start_date": "2019-03",
            "bullets": [EN_BULLET_2, EN_BULLET_3],
            "industry_context": EN_INDUSTRY_CONTEXT_CLEAN,
        }],
        "skills": [],
    })

    check = _check(_audit(cv, "en"), "document-language")

    assert check is not None
    assert check.status == "pass", check.details
    assert check.details is None


# ── 6. no document_language passed → present, not_applicable (the #634 shape) ─


def test_no_document_language_yields_a_present_not_applicable_check():
    """ADR-079 clause 4 / the #634 failure shape: an omitted check reads as a
    clean audit of something never evaluated. The check must be PRESENT."""
    cv = _clean_german_cv()

    report = _audit(cv, None)
    check = _check(report, "document-language")

    assert check is not None, "the check must not be silently omitted"
    assert check.status == "not_applicable"
    ids = [c.id for c in report.checks]
    assert ids.count("document-language") == 1


# ── 7. the evidence floor in item_language_mismatch ───────────────────────────


def test_a_three_word_english_fragment_below_the_floor_is_not_reported():
    """'for the team' classifies as English under the bare detector (all three
    words are English stopwords, one of them 'team') but carries only 3 words —
    below `ITEM_LANGUAGE_MIN_WORDS` (4) — so the item-level predicate must stay
    silent rather than act on too little evidence."""
    from applire.utils.language_detection import detect_language

    fragment = "for the team"
    assert len(fragment.split()) == 3
    assert detect_language(fragment) == "en", (
        "fixture must actually classify as English under the bare detector"
    )
    assert not item_language_mismatch(fragment, "de")


def test_a_four_word_english_fragment_with_a_function_word_is_reported():
    """One word over the floor, and the same shape (an English function word
    tips the detector), the predicate reports it."""
    from applire.utils.language_detection import detect_language

    fragment = "led the support team"
    assert len(fragment.split()) == 4
    assert detect_language(fragment) == "en"
    assert item_language_mismatch(fragment, "de")


# ── 8. the check never mutates the document ────────────────────────────────────


def test_the_check_never_mutates_the_tailored_document():
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Jana Lehmann"},
        "summary": DE_SUMMARY,
        "work_history": [_de_work_entry(bullets=[DE_BULLET_1, EN_BULLET_WORK])],
        "skills": [],
    })
    before = cv.model_dump(mode="json")

    _audit(cv, "de")

    assert cv.model_dump(mode="json") == before
