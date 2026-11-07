# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UX audit 4 (b): a letter sentence the Oracle marked as built from the letter
form (``not_applicable`` + ``fromLetterForm``, ruling B3-10 — the candidate's own
statement for THIS letter, never checked against nor added to the profile) is no
unsupported claim of a posting term it carries. The motivation sentence "…
Entwicklungsperspektive zur Teamleitung, die Ihre Ausschreibung bietet" got
``stop_and_fix: true`` although the Oracle had already judged it neutral.

One reader of the Oracle's persisted verdict (ADR-066), not a second judgement:
the term leaves ``present_unsupported`` for ``present_unsupported_form_input``
(class S, no card). The sentence itself is already shown in the review's neutral
"Aus Deiner Eingabe im Formular" section (FO-1 = A: no buttons, not counted). A
term that ALSO sits in any other claim sentence stays red. Synthetic texts.
"""
from __future__ import annotations

from applire.services import review_state as rs
from applire.services.ats_audit import _norm, apply_letter_employer_facts

MOTIVATION = (
    "Hauptgrund für meinen Wechsel ist die Aussicht auf Teamleitung, "
    "die Ihre Stelle bietet."
)
CLAIM = "Ich habe acht Jahre Teamleitung in der Buchhaltung übernommen."
EMPLOYER = "Ihr Haus verbindet Teamleitung mit kurzen Wegen."


def _letter_report(terms, sentences, es=(), fi=()):
    return {
        "document": "cover_letter",
        "keywords": {
            "present_unsupported": list(terms),
            "present_unsupported_employer_sentence": list(es),
            "present_unsupported_form_input": list(fi),
            "present_unsupported_sentences": sentences,
            "present_unsupported_matches": {t: [{"form": t, "stem": False}] for t in terms},
        },
    }


def _truth(form=(), employer=(), graded=(), graded_key="fromLetterForm"):
    return {"claims": [
        {"claim": {"text": x}, "verdict": {"verdict": "not_applicable", "detail_key": "fromLetterForm"}}
        for x in form
    ] + [
        {"claim": {"text": x}, "verdict": {"verdict": "not_applicable", "detail_key": "triageEmployerFact"}}
        for x in employer
    ] + [
        {"claim": {"text": x}, "verdict": {"verdict": "unbacked", "detail_key": graded_key}} for x in graded
    ]}


def test_a_term_only_in_the_form_sentence_leaves_the_claim_list():
    rep = _letter_report(["Teamleitung", "Bilanzierung"], {
        "Teamleitung": [_norm(MOTIVATION)], "Bilanzierung": [_norm("Ich habe Bilanzierung verantwortet.")]})
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION]))
    kw = out["keywords"]
    assert kw["present_unsupported_form_input"] == ["Teamleitung"]
    assert kw["present_unsupported"] == ["Bilanzierung"]
    assert set(kw["present_unsupported_matches"]) == {"Bilanzierung"}
    assert set(kw["present_unsupported_sentences"]) == {"Bilanzierung"}
    assert kw["present_unsupported_employer_sentence"] == []
    assert rep["keywords"]["present_unsupported"] == ["Teamleitung", "Bilanzierung"]  # input untouched


def test_a_second_claim_sentence_keeps_the_red_row():
    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION), _norm(CLAIM)]})
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION]))
    assert out["keywords"]["present_unsupported_form_input"] == []


def test_a_real_claim_sentence_never_moves():
    """The same sentence text graded (not the form mark) is a claim: red."""
    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(CLAIM)]})
    assert apply_letter_employer_facts(rep, _truth(graded=[CLAIM]))["keywords"][
        "present_unsupported_form_input"] == []
    # a not_applicable verdict of another kind (e.g. a triage no-claim) is no form mark
    other = {"claims": [{"claim": {"text": CLAIM},
                         "verdict": {"verdict": "not_applicable", "detail_key": "noCheckableContent"}}]}
    assert apply_letter_employer_facts(rep, other)["keywords"]["present_unsupported_form_input"] == []
    # the form key on a graded verdict is no mark either
    rep2 = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION)]})
    assert apply_letter_employer_facts(rep2, _truth(graded=[MOTIVATION]))["keywords"][
        "present_unsupported_form_input"] == []


def test_the_form_mark_covers_only_the_marked_text():
    """A claim the writer appended to the form sentence stays outside the mark."""
    long = MOTIVATION[:-1] + " und Teamleitung habe ich bereits zehn Jahre ausgeübt."
    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(long)]})
    out = apply_letter_employer_facts(rep, _truth(form=["Hauptgrund für meinen Wechsel ist die Aussicht"]))
    assert out["keywords"]["present_unsupported_form_input"] == []


def test_employer_and_form_sentences_together_move_to_the_form_row():
    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION), _norm(EMPLOYER)]})
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION], employer=[EMPLOYER]))
    kw = out["keywords"]
    assert kw["present_unsupported_form_input"] == ["Teamleitung"]
    assert kw["present_unsupported"] == []
    assert kw["present_unsupported_employer_sentence"] == []


def test_employer_only_still_takes_the_employer_row():
    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(EMPLOYER)]})
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION], employer=[EMPLOYER]))
    kw = out["keywords"]
    assert kw["present_unsupported_employer_sentence"] == ["Teamleitung"]
    assert kw["present_unsupported_form_input"] == []


def test_letters_only_and_never_raises():
    cv = dict(_letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION)]}), document="cv")
    assert apply_letter_employer_facts(cv, _truth(form=[MOTIVATION])) is cv
    garbage = {"document": "cover_letter", "keywords": {"present_unsupported": ["X"],
                                                        "present_unsupported_sentences": "nope"}}
    assert apply_letter_employer_facts(garbage, _truth(form=["x"])) is garbage


def test_the_moved_term_is_no_review_row_and_not_counted_for_the_agent():
    from applire.schemas.oracle import TruthfulnessReport
    from applire.services.truthfulness_summary import summarize

    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION)]})
    rep["keywords"]["claimable_concepts"] = []
    empty = TruthfulnessReport.from_results("cover_letter", []).model_dump(mode="json")
    assert summarize(empty, rep).flagged == 1 and summarize(empty, rep).stop_and_fix is True
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION]))
    assert rs.group_one_findings(out, {"claims": []}) == []
    s = summarize(empty, out)
    assert s.available and s.flagged == 0 and s.stop_and_fix is False


def test_the_report_validates_with_the_new_bucket():
    from applire.schemas.ats import ATSKeywordCoverage

    rep = _letter_report(["Teamleitung"], {"Teamleitung": [_norm(MOTIVATION)]})
    out = apply_letter_employer_facts(rep, _truth(form=[MOTIVATION]))
    kw = {k: v for k, v in out["keywords"].items()}
    cov = ATSKeywordCoverage(present=["Teamleitung"], missing=[], **kw)
    assert cov.present_unsupported_form_input == ["Teamleitung"]
