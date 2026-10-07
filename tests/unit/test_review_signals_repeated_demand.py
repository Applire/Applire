# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#703 (ADR-076 amended 2026-10-07) — a demand the writing loop repeats becomes a
named, counted signal on the terminal-review check. Pure; 0 provider calls.

The replay fixtures are the per-round VERIFIED COVERAGE demand of the three captured
Nougat delivery runs on the synthetic ``operations_marcus_de`` case (extracted from the
debug logs in record order — the exact entries ``coverage_reviewer_prompt_fn`` reported
through ``on_demand``), plus each run's delivered ``letter_data``.
"""
import json
from pathlib import Path

import pytest

from applire.services.terminal_review_outcome import (
    DemandRecord,
    RepeatedDemand,
    TerminalReviewOutcome,
    build_terminal_review_check,
    detect_repeated_demands,
    with_repeated_demands,
)

FILES = Path(__file__).resolve().parents[1] / "files" / "review_signals"


def _replay(run: str) -> tuple[DemandRecord, dict]:
    fx = json.loads((FILES / f"demand-rounds-{run}.json").read_text())
    record = DemandRecord()
    for entries in fx["rounds"]:
        record.record_round(entries)
    return record, fx["delivered_letter_data"]


def _outcome(**kw) -> TerminalReviewOutcome:
    base = dict(
        chain_id="letter_terminal_review",
        path="exhausted",
        approved=False,
        blocking_issues=("x",),
        minor_issues=(),
        rounds=3,
    )
    base.update(kw)
    return TerminalReviewOutcome(**base)


# --------------------------------------------------------------- the captured runs


def test_0910_names_arbeitsvorbereitung_demanded_in_five_rounds():
    record, letter = _replay("2026-09-10")
    by_term = {d.term: d for d in record.repeated(letter)}
    assert "Arbeitsvorbereitung" in by_term
    assert by_term["Arbeitsvorbereitung"].rounds == 5
    assert by_term["Arbeitsvorbereitung"].total_rounds == 8


def test_0913_open_and_landed_terms():
    record, letter = _replay("2026-09-13")
    by_term = {d.term: d for d in record.repeated(letter)}
    assert set(by_term) == {"Arbeitssicherheit", "Arbeitsvorbereitung", "Maschinendatenerfassung"}
    assert by_term["Arbeitssicherheit"].rounds == 3
    # Both landed — in the delivered keyword-list sentence.
    assert by_term["Arbeitssicherheit"].weight == "landed"
    assert by_term["Arbeitsvorbereitung"].weight == "landed"
    # Demanded in the last two terminal rounds, never delivered.
    assert by_term["Maschinendatenerfassung"].weight == "open"
    # Open first.
    assert record.repeated(letter)[0].term == "Maschinendatenerfassung"


@pytest.mark.parametrize("run", ["2026-09-10", "2026-09-11", "2026-09-13"])
def test_every_captured_run_yields_a_signal_on_the_terminal_check(run):
    record, letter = _replay(run)
    outcome = with_repeated_demands(_outcome(), record, letter)
    check = build_terminal_review_check(outcome).model_dump()
    assert check["driver"]["repeated_demands"] == len(outcome.repeated_demands) > 0
    assert "Asked for again and again while writing:" in check["details"]
    # The signal never changes the review's own status.
    assert check["status"] == build_terminal_review_check(_outcome()).status


# --------------------------------------------------------------- the detector


def _present(*terms):
    s = set(terms)
    return lambda t: t in s


def test_one_round_is_not_a_repetition():
    assert detect_repeated_demands([["A"], ["B"], []], present=_present()) == ()


def test_a_term_counts_once_per_round():
    found = detect_repeated_demands([["A", "A"], ["A"]], present=_present())
    assert [(d.term, d.rounds, d.total_rounds) for d in found] == [("A", 2, 2)]


def test_non_consecutive_rounds_still_repeat():
    found = detect_repeated_demands([["A"], [], ["A"]], present=_present("A"))
    assert found[0].rounds == 2 and found[0].weight == "landed"


def test_order_open_first_then_most_rounds():
    rounds = [["L", "O1"], ["L", "O1", "O2"], ["L", "O2"]]
    found = detect_repeated_demands(rounds, present=_present("L"))
    assert [d.term for d in found] == ["O1", "O2", "L"]


def test_empty_rounds_still_count_toward_the_total():
    record = DemandRecord()
    record.record_round([{"concept": "A"}])
    record.record_round([])
    record.record_round(None)
    record.record_round([{"concept": "A"}])
    (d,) = record.repeated({"body": {"paragraphs": ["nothing here"]}})
    assert (d.rounds, d.total_rounds, d.in_document) == (2, 4, False)


def test_presence_uses_the_ledger_forms():
    record = DemandRecord()
    entry = {"concept": "Arbeitsvorbereitung", "surface_forms": ["AV", "Arbeitsvorbereitung"]}
    record.record_round([entry])
    record.record_round([entry])
    (d,) = record.repeated({"body": {"paragraphs": ["Ich leitete die AV im Werk."]}})
    assert d.in_document is True


def test_no_delivered_draft_reads_open():
    record = DemandRecord()
    record.record_round([{"concept": "A"}])
    record.record_round([{"concept": "A"}])
    assert record.repeated(None)[0].weight == "open"


# --------------------------------------------------------------- the report


def test_no_repetition_leaves_the_check_byte_identical():
    plain = build_terminal_review_check(_outcome()).model_dump()
    with_none = build_terminal_review_check(
        with_repeated_demands(_outcome(), DemandRecord(), {"body": {}})
    ).model_dump()
    assert plain == with_none
    assert plain["driver"] is None


def test_driver_counts_open_terms():
    demands = (
        RepeatedDemand("A", 2, 4, in_document=False),
        RepeatedDemand("B", 3, 4, in_document=True),
    )
    check = build_terminal_review_check(_outcome(repeated_demands=demands)).model_dump()
    assert check["driver"] == {"repeated_demands": 2, "open": 1}
    assert "A (2 of 4 rounds, not in the document)" in check["details"]
    assert "B (3 of 4 rounds, now in the document)" in check["details"]


def test_none_outcome_stays_none():
    record = DemandRecord()
    record.record_round([{"concept": "A"}])
    record.record_round([{"concept": "A"}])
    assert with_repeated_demands(None, record, {}) is None


def test_worse_of_keeps_the_signal_from_either_side():
    demands = (RepeatedDemand("A", 2, 2, in_document=False),)
    a = _outcome(repeated_demands=demands)
    b = _outcome(path="approved", approved=True, blocking_issues=())
    assert a.worse_of(b).repeated_demands == demands
    assert b.worse_of(a).repeated_demands == demands


def test_recording_does_not_change_the_reviewer_prompt():
    """ADR-021 cl. 6: the record is a REPORT. The reviewer prompt built with the
    recorder attached is byte-identical to the one built without it, round after
    round — nothing of the record reaches the reviewer."""
    from applire.services.keyword_ledger import coverage_reviewer_prompt_fn

    ledger = [
        {"keyword": "Arbeitsvorbereitung", "concept": "Arbeitsvorbereitung", "status": "claimable",
         "claimable": True, "surface_forms": ["Arbeitsvorbereitung"], "evidence": "AV-Stammdaten bereinigt",
         "fit_weight": 1.0, "requirement_level": "required"},
    ]
    record = DemandRecord()
    base = lambda source, draft: "BASE"  # noqa: E731
    with_rec = coverage_reviewer_prompt_fn(base, ledger, on_demand=record.record_round, max_terms_per_round=2)
    without = coverage_reviewer_prompt_fn(base, ledger, max_terms_per_round=2)
    draft = {"body": {"paragraphs": ["Ich führte Teams."]}}
    for _ in range(3):
        assert with_rec("src", draft) == without("src", draft)
    # the fixture really demands the term every round (a recorder that saw nothing
    # would pass the identity assertion vacuously)
    assert record.rounds == (("Arbeitsvorbereitung",),) * 3
