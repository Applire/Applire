# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-5 (founder ruling 2026-10-24, Hazelnut build 4) — the terminal-review row
carries its card as DATA: a state key, a count and structured items.

ux-audit-2 B-2 (marcus-059/061, felix-018/055): the DE review card printed the EN
``details`` verbatim — round counts, check ids ("cv.role_budget"), role UUIDs.
The ``details`` string stays for the agent door; the review surface reads
``details_key`` / ``details_params`` / ``notes`` instead. No Docker, no LLM.
"""

from __future__ import annotations

import re

import pytest

from applire.services.terminal_review_outcome import (
    TERMINAL_REVIEW_DETAIL_KEYS,
    TerminalNote,
    TerminalReviewOutcome,
    build_terminal_review_check,
)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


def _o(path, *, approved=False, blocking=(), minor=(), notes=(), rounds=2, correction=None):
    return TerminalReviewOutcome(
        chain_id="cv_terminal_review", path=path, approved=approved,
        blocking_issues=tuple(blocking), minor_issues=tuple(minor), rounds=rounds,
        notes=tuple(notes), correction=correction,
    )


@pytest.mark.parametrize("outcome,key,count", [
    (None, "terminal-review-not-run", 0),
    (_o(None), "terminal-review-not-run", 0),
    (_o("approved", approved=True), "terminal-review-clean", 0),
    (_o("minor_only", approved=True, minor=("Tighten the summary.",)), "terminal-review-observations", 1),
    # exhausted = a correction landed after the last verdict (F-4): unverified
    (_o("exhausted", blocking=("Claim A is unsupported.", "Claim B is unsupported.")), "terminal-review-unverified", 2),
    (_o("exhausted", blocking=()), "terminal-review-unverified-clean", 0),
    (_o("cycle_detected", blocking=("Claim A is unsupported.",)), "terminal-review-open", 1),
    (_o("cycle_detected", blocking=()), "terminal-review-unnamed", 0),
    (_o("reviewer_call_failed"), "terminal-review-incomplete", 0),
    (_o("generator_call_failed", blocking=("x",)), "terminal-review-incomplete", 1),
])
def test_each_state_has_its_key_and_count(outcome, key, count):
    check = build_terminal_review_check(outcome)
    assert check.details_key == key
    assert key in TERMINAL_REVIEW_DETAIL_KEYS
    assert check.details_params == {"count": count}
    assert check.details  # the EN diagnostic stays for the agent door


def test_a_clean_verdict_with_a_code_check_open_on_the_shipped_cv_is_its_own_state():
    """Marcus-059 shape: the reviewer approved, the delivered-draft check still
    reports a role over budget — not "clean", not "open findings"."""
    note = TerminalNote(kind="code", key="cv.role_budget",
                        text="Over the per-role bullet budget: 1111… (Nivalo GmbH): 7 bullets, budget 5",
                        subjects=("Nivalo GmbH",))
    check = build_terminal_review_check(_o("approved", approved=True, notes=(note,)))
    assert check.status == "pass"
    assert check.details_key == "terminal-review-clean-code-open"
    assert check.details_params == {"count": 1}
    code = [n for n in check.notes if n.kind == "code"]
    assert [(n.key, n.subjects) for n in code] == [("cv.role_budget", ["Nivalo GmbH"])]
    # the EN details keeps the sentence the agent door always read
    assert "Code checks still open on the delivered CV" in check.details
    assert "cv.role_budget" in check.details


def test_items_carry_no_round_count_check_prefix_or_uuid_in_their_data_fields():
    """The card's data fields are what the UI may say; the raw EN text rides
    ``text`` only (the reviewer's words, shown under ruling T-1)."""
    blocking = (
        "[cv.role_budget] Over the per-role bullet budget: 11111111-1111-4111-8111-111111111111 (Nivalo GmbH): 7 bullets",
        "The summary claims SAP S/4HANA, which the profile does not record.",
    )
    check = build_terminal_review_check(_o("exhausted", blocking=blocking, rounds=3))
    kinds = [(n.kind, n.key) for n in check.notes]
    assert kinds == [("finding", None), ("code", "cv.role_budget")]
    for n in check.notes:
        for value in [n.key or "", *n.subjects]:
            assert not _UUID.search(value)
            assert "round" not in value
    # the code fact is recognised as code, its "[cv.role_budget]" prefix is not text
    assert not check.notes[1].text.startswith("[")


def test_a_code_fact_raised_by_the_loop_and_still_open_on_delivery_is_one_item():
    loop = "[cv.role_budget] Over the per-role bullet budget: x (Nivalo GmbH): 7 bullets"
    delivered = TerminalNote(kind="code", key="cv.role_budget", text="Over…", subjects=("Nivalo GmbH",))
    check = build_terminal_review_check(_o("exhausted", blocking=(loop,), notes=(delivered,)))
    code = [n for n in check.notes if n.kind == "code"]
    assert len(code) == 1 and code[0].subjects == ["Nivalo GmbH"]


def test_pipeline_notes_survive_as_keyed_items_and_in_the_en_details():
    cut = TerminalNote(kind="note", key="letter.limit_cut",
                       text="One sentence was REMOVED from the delivered letter…",
                       subjects=("Ich habe keine Erfahrung mit SAP.",))
    post = TerminalNote(kind="finding", key="letter.post_condense", text="Figure '2019' is unanchored.")
    check = build_terminal_review_check(_o("approved", approved=True, notes=(post, cut)))
    keys = [(n.kind, n.key) for n in check.notes]
    assert ("note", "letter.limit_cut") in keys and ("finding", "letter.post_condense") in keys
    assert "The reviewer's notes on the shortened letter" in check.details
    assert "One sentence was REMOVED" in check.details


def test_a_plain_string_note_is_a_generic_item_never_a_keyed_sentence():
    check = build_terminal_review_check(_o("approved", approved=True, notes=("Something happened.",)))
    assert [(n.kind, n.key) for n in check.notes] == [("note", None)]


def test_the_item_lists_are_bounded_with_a_more_marker():
    blocking = tuple(f"Finding number {i} about the summary." for i in range(9))
    check = build_terminal_review_check(_o("exhausted", blocking=blocking))
    findings = [n for n in check.notes if n.kind == "finding"]
    assert len(findings) == 7
    assert findings[-1].key == "more" and findings[-1].count == 3


def test_a_persisted_report_without_the_new_fields_still_validates():
    from applire.schemas.ats import ATSCheck

    legacy = {"id": "terminal-review", "status": "fail", "details": "The terminal review exhausted…"}
    check = ATSCheck.model_validate(legacy)
    assert check.notes is None and check.details_key is None


# ── T-1 = A: the reviewer's plain note reaches the card, never the corrector ──


def test_normalize_issues_reads_the_plain_note_and_keeps_identity_on_text():
    from applire.services.review_issues import ReviewIssue, normalize_issues

    issues = normalize_issues([
        {"severity": "blocking", "issue": "work_history index 0: X is unsupported.", "note": "  Bei Weberit ist X nicht belegt. "},
        {"severity": "minor", "issue": "Tighten the summary."},
        "a bare string issue",
    ])
    assert [i.note for i in issues] == ["Bei Weberit ist X nicht belegt.", "", ""]
    # the note is not part of an issue's identity (every comparison is unchanged)
    assert issues[0] == ReviewIssue(text="work_history index 0: X is unsupported.", severity="blocking")


def test_the_corrector_feedback_never_carries_the_note():
    from applire.services.review_issues import normalize_issues
    from applire.services.reviewer import fold_issues_into_feedback

    issues = normalize_issues([{"severity": "blocking", "issue": "X is unsupported.", "note": "PLAIN-NOTE-MARKER"}])
    assert "PLAIN-NOTE-MARKER" not in fold_issues_into_feedback("Fix X.", issues)


def test_the_settle_carries_the_notes_to_the_card():
    from applire.services.review_issues import ReviewSettle
    from applire.services.terminal_review_outcome import settle_to_outcome

    settle = ReviewSettle(
        path="cycle_detected", approved=False,
        blocking_issues=("work_history index 0: X is unsupported.", "Y is repeated."),
        minor_issues=(), rounds=2, settled={},
        plain_notes=(("work_history index 0: X is unsupported.", "Bei Weberit ist X nicht belegt."),),
    )
    check = build_terminal_review_check(settle_to_outcome(settle, chain_id="cv_terminal_review"))
    findings = [n for n in check.notes if n.kind == "finding"]
    assert [(n.text[:20], n.plain) for n in findings] == [
        ("work_history index 0", "Bei Weberit ist X nicht belegt."),
        ("Y is repeated.", None),
    ]


def test_the_fold_of_two_invocations_keeps_both_sides_notes():
    a = _o("cycle_detected", blocking=("A",))
    b = _o("cycle_detected", blocking=("B",))
    a = TerminalReviewOutcome(**{**a.__dict__, "plain_notes": (("A", "a"),)})
    b = TerminalReviewOutcome(**{**b.__dict__, "plain_notes": (("B", "b"),)})
    assert set(a.worse_of(b).plain_notes) == {("A", "a"), ("B", "b")}


def test_only_the_terminal_doors_ask_for_the_note():
    from applire.prompts import review_cover_letter as letter, review_cv_tailoring as cv

    assert '"note"' in cv.TERMINAL_REVIEW_SYSTEM_PROMPT and '"note"' in cv.V2_TERMINAL_REVIEW_SYSTEM_PROMPT
    assert '"note"' in letter.TERMINAL_REVIEW_SYSTEM_PROMPT and '"note"' in letter.V2_TERMINAL_REVIEW_SYSTEM_PROMPT
    assert '"note"' not in cv.REVIEW_SYSTEM_PROMPT and '"note"' not in letter.REVIEW_SYSTEM_PROMPT
    # the note sits right after the issue it restates (the replayed order)
    s = cv.TERMINAL_REVIEW_SYSTEM_PROMPT
    assert s.index('"issue"') < s.index('"note"') < s.index('"location"')


def test_the_terminal_user_prompts_name_the_note_language():
    """Replay 2026-10-24: with the language left to inference the reviewer wrote
    German notes for 7 of 7 English-letter issues; naming it in the user prompt
    gave 81 of 81 in the document's language (n=25). Both doors, seam-tested."""
    from applire.prompts import review_cover_letter as letter, review_cv_tailoring as cv
    from applire.prompts.review_severity import note_language_line

    assert note_language_line(None) == ""
    assert "German" in note_language_line("de") and "du" in note_language_line("de")
    assert "English" in note_language_line("en") and "du" not in note_language_line("en")
    lp = letter.build_terminal_review_prompt(
        "S", {"a": 1}, page_count=1, letter_pages=1, word_count=3, word_budget=4,
        condense_exhausted=False, document_language="en",
    )
    assert "NOTE LANGUAGE: write every issue's `note` in English." in lp
    assert "NOTE LANGUAGE" not in letter.build_terminal_review_prompt(
        "S", {"a": 1}, page_count=1, letter_pages=1, word_count=3, word_budget=4,
        condense_exhausted=False,
    )
    cp = cv.build_terminal_review_prompt(
        "S", {"a": 1}, page_count=1, target=1, condensation_exhausted=False, document_language="de",
    )
    assert "NOTE LANGUAGE: write every issue's `note` in German and address the candidate as du." in cp


def test_the_letter_terminal_door_passes_the_letter_language():
    """The call site in cover_letter.py hands the letter's pinned language on."""
    import inspect
    from applire.services import cover_letter

    src = inspect.getsource(cover_letter)
    i = src.index("def _terminal_base(")
    assert "document_language=cl.document_language" in src[i : i + 1500]


def test_the_v2_cv_terminal_prompt_names_the_note_language_too():
    from applire.prompts import review_cv_tailoring as cv

    p = cv.build_v2_terminal_review_prompt(
        "S", {"a": 1}, page_count=1, target=1, condensation_exhausted=False, document_language="en",
    )
    assert "NOTE LANGUAGE: write every issue's `note` in English." in p
