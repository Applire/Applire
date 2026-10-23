# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 3, WP-FR (adversarial A-9 / delivery F3) — the per-item language
predicate read a plainly English sentence as German.

`detect_language` scores stopwords and DEFAULTS A TIE TO 'de' (DACH-first; right
for a whole JD). `item_language_mismatch` used that default as evidence: an
English item with no listed English function word ("I also mentor two mid-level
engineers through weekly design reviews.") scores 0 : 0, is classified 'de', and
in an ENGLISH document that read as "not written in the document language". The
#724 floor note measured only German documents, where the tie protects the item
for free; in English documents it is the opposite.

Measured on the D2 offline corpus (1,219 docs): 314 / 1,854 items of English CVs
and 33 / 1,629 sentences of English letters were flagged at exactly 0 : 0 — all
English. The rule now: an item is foreign only when the OTHER language scores
strictly higher — no evidence (a tie) is no finding, in both directions.
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text
from applire.services.letter_v2 import letter_language_fact
from applire.utils.language_detection import (
    detect_language,
    item_language_mismatch,
    language_scores,
)

# Daniel's delivered letter (delivery run 2026-10-22, `it_backend_daniel`): the
# sentence the `document-language` row and the terminal review both flagged.
DANIEL_SENTENCE = "I also mentor two mid-level engineers through weekly design reviews."
# The adversarial pass's CV bullet shape (A-9): English, no listed function word.
DANIEL_BULLET = (
    "Implemented an invoice-dunning workflow engine processing approximately "
    "200,000 invoices per month."
)
# An English sentence whose only German signal is an umlaut in a company name.
UMLAUT_NAME_SENTENCE = (
    "At Schwarzwald Präzision GmbH, I onboard a junior controller and support "
    "two working students."
)
# True positives that must survive: German items in an English document.
DE_BULLET = "Einführung eines neuen ERP-Systems für die Fertigung mit drei Werken."
DE_BULLET_NO_UMLAUT = "Leitung der Instandhaltung mit acht Mitarbeitern im Dreischichtbetrieb."
# And the mirror: an English bullet in a German document.
EN_BULLET = "Led the migration of the ticketing system to a new cloud platform."


def test_fixtures_have_the_scores_they_claim():
    """Each fixture's shape is checked by the real scorer, not asserted by name."""
    assert language_scores(DANIEL_SENTENCE) == (0, 0)
    assert detect_language(DANIEL_SENTENCE) == "de"  # the tie default — the bug's input
    assert language_scores(DANIEL_BULLET) == (0, 0)
    de, en = language_scores(UMLAUT_NAME_SENTENCE)
    assert de == en and de > 0
    de, en = language_scores(DE_BULLET)
    assert de > en
    de, en = language_scores(DE_BULLET_NO_UMLAUT)
    assert de > en and "ä" not in DE_BULLET_NO_UMLAUT.lower()


def test_an_english_item_without_function_words_is_not_foreign_in_an_english_document():
    assert not item_language_mismatch(DANIEL_SENTENCE, "en")
    assert not item_language_mismatch(DANIEL_BULLET, "en")


def test_a_tie_is_no_finding_in_either_direction():
    assert not item_language_mismatch(UMLAUT_NAME_SENTENCE, "en")
    assert not item_language_mismatch(UMLAUT_NAME_SENTENCE, "de")


def test_a_german_bullet_in_an_english_document_still_fails():
    assert item_language_mismatch(DE_BULLET, "en")
    assert item_language_mismatch(DE_BULLET_NO_UMLAUT, "en")


def test_an_english_bullet_in_a_german_document_still_fails():
    assert item_language_mismatch(EN_BULLET, "de")


def test_detect_language_keeps_its_tie_default():
    """The whole-text detector (JD routing, vault dominance) is unchanged: a tie
    still routes to German. Only the per-item predicate stops reading it as evidence."""
    assert detect_language("") == "de"
    assert detect_language(DANIEL_SENTENCE) == "de"


def _letter(paragraphs: list[str]) -> dict:
    return {
        "header": {"name": "Daniel Kovac"},
        "recipient": {"company": "NovaPay GmbH"},
        "body": {"paragraphs": paragraphs},
        "signature": {"name": "Daniel Kovac"},
    }


def test_daniels_delivered_letter_passes_the_document_language_row():
    """The delivery point: the letter audit's `document-language` row on the
    letter shape that failed in the delivery run."""
    letter = _letter([
        "Dear Hiring Manager,",
        "I have not worked for a payment institution or in a formal compliance "
        "process with documented controls or audits; what I bring is "
        "correctness-critical financial data modelling. " + DANIEL_SENTENCE,
    ])
    text = "Daniel Kovac\n" + "\n".join(letter["body"]["paragraphs"])
    report = _audit_letter_text(text, letter, [], document_language="en")
    row = next(c for c in report.checks if c.id == "document-language")
    assert row.status == "pass", row.details
    assert letter_language_fact(letter, "en").clean


def test_a_german_sentence_in_an_english_letter_still_fails_the_row():
    letter = _letter(["Dear Hiring Manager,", DE_BULLET + " " + DANIEL_SENTENCE])
    text = "Daniel Kovac\n" + "\n".join(letter["body"]["paragraphs"])
    report = _audit_letter_text(text, letter, [], document_language="en")
    row = next(c for c in report.checks if c.id == "document-language")
    assert row.status == "fail"
    assert "Einführung" in row.details and "mentor" not in row.details


def test_an_english_cv_with_such_a_bullet_passes():
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Daniel Kovac"},
        "summary": "Backend engineer with nine years of Python and PostgreSQL experience.",
        "work_history": [{
            "company": "Finleap Build GmbH",
            "role": "Backend Engineer",
            "start_date": "2016-01",
            "bullets": [DANIEL_BULLET],
        }],
        "skills": [],
    })
    text = "\n".join(["Daniel Kovac", cv.summary, DANIEL_BULLET])
    report = _audit_cv_text(text, cv, keywords=[], document_language="en")
    row = next(c for c in report.checks if c.id == "document-language")
    assert row.status == "pass", row.details


# ── the item-level evidence that keeps the true positives (corpus-measured) ────

# A German vault bullet with NO function word and no umlaut — the #724 preseed's
# own class (cross-language vault into an English CV). Under the bare tie rule it
# scored 0 : 0 and would have passed unnoticed; its derivational ending
# ("Monatliches") and participle are German evidence.
DE_NO_FUNCTION_WORD = "Monatliches Vertriebsreporting in Excel aufgebaut."
# An acronym that spells a German function word ("ALS" ≠ "als").
EN_WITH_ACRONYM = "I also hold Advanced Life Support (ALS) certification."
# A German sentence whose only listed stopword is the English "team".
DE_WITH_TEAM = "Teilnahme am Code-Team zur Notfallversorgung."


def test_a_german_item_without_function_words_still_fails_in_an_english_document():
    assert language_scores(DE_NO_FUNCTION_WORD) == (0, 0)
    assert item_language_mismatch(DE_NO_FUNCTION_WORD, "en")


def test_an_all_caps_acronym_is_not_a_function_word():
    de, en = language_scores(EN_WITH_ACRONYM)
    assert de > en  # the bare detector reads "ALS" as German "als"
    assert not item_language_mismatch(EN_WITH_ACRONYM, "en")


def test_a_german_sentence_with_the_word_team_is_not_english():
    de, en = language_scores(DE_WITH_TEAM)
    assert en > de  # the bare detector's "team" outweighs the German
    assert not item_language_mismatch(DE_WITH_TEAM, "de")


DE_FUNCTION_WORDS_ONLY = "Deutsch ist meine Muttersprache seit Geburt."


def test_german_only_function_words_count_as_german_evidence():
    assert language_scores(DE_FUNCTION_WORDS_ONLY) == (0, 0)
    assert item_language_mismatch(DE_FUNCTION_WORDS_ONLY, "en")
