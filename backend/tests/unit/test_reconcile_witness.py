# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#370 — the testimony write-loss witness (`reconcile.witness.compute_not_applied`).

A testimony submission returned `status: applied, changes: 75` while a whole
budget/team-size section was silently discarded — the caller had no way to
tell "all of it landed" from "most of it landed" (#370, folds in #371). The
witness is a PURE, deterministic FACT-checker (ADR-062 clause 1: no semantic
matching, no "probably applied") over the ops the reconcile ENGINE actually
produced — see the module docstring in `witness.py` for the exact scope
(post-parse/stance/attribution ops, NOT post-`apply_ops` state)."""
from __future__ import annotations

from applire.services.profile.reconcile.ops import UpsertSkill, UpsertWork
from applire.services.profile.reconcile.witness import compute_not_applied


def _work_op(**over) -> UpsertWork:
    fields = dict(ref="w1", target=None, company="ACME", role="Engineer")
    fields.update(over)
    return UpsertWork(**fields)


# ── (a) figures ────────────────────────────────────────────────────────────


def test_figure_carried_by_an_op_is_not_reported():
    text = "I manage a budget of 1350000 EUR."
    ops = [_work_op(budget_managed="1350000")]

    result = compute_not_applied(text, ops)

    assert not any(item.kind == "figure" for item in result)


def test_figure_missing_from_every_op_is_reported():
    text = "My team consists of 12 engineers."
    ops = [_work_op()]  # no team_size, no bullet mentioning 12

    result = compute_not_applied(text, ops)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1
    assert figures[0].reason == "figure_not_in_any_op"
    assert "12" in figures[0].span


def test_single_digit_integer_is_below_the_figure_floor():
    # "3" is a single digit with no decimal marker — below the ">= 2 digits"
    # floor, so it must never be reported as a missing FIGURE (it may still
    # surface via the sentence-level check).
    text = "Ok."
    ops: list = []

    result = compute_not_applied("I have 3 kids.", ops)

    assert not any(item.kind == "figure" and item.span.strip() == "3" for item in result)


def test_decimal_single_leading_digit_counts_as_a_figure():
    # "8,2" — one leading digit, but a genuine decimal — DOES count (mirrors
    # the codebase's own figure-extraction floor for single-digit decimals).
    text = "Die Quote lag bei 8,2 Prozent."
    ops = [_work_op()]

    result = compute_not_applied(text, ops)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1


def test_thousands_dot_separator_matches_stripped_digits_in_op():
    text = "Der Umsatz betrug 1.200.000 EUR."
    ops = [_work_op(budget_managed="1200000")]

    result = compute_not_applied(text, ops)

    assert not any(item.kind == "figure" for item in result)


def test_comma_decimal_with_magnitude_word_matches_expanded_op_value():
    text = "Budget: 1,35 Mio EUR."
    ops = [_work_op(budget_managed="1350000")]

    result = compute_not_applied(text, ops)

    assert result == []


def test_comma_decimal_with_magnitude_word_matches_verbatim_op_bullet():
    # Rule 12 of the reconcile prompt: bullets keep the figure VERBATIM
    # ("ca. 6 Mio. EUR"), so the witness must also accept the un-expanded
    # reading, not only the multiplied-out one.
    from applire.services.profile.reconcile.ops import AddBullets

    text = "Budgetverantwortung: 1,35 Mio EUR."
    ops = [AddBullets(target="w1", achievements=["Budgetverantwortung von 1,35 Mio EUR"])]

    result = compute_not_applied(text, ops)

    assert result == []


def test_duplicate_missing_figure_is_reported_once():
    text = "12 people. Again, 12 people total."
    ops: list = []

    result = compute_not_applied(text, ops)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1


# ── (b) sentences ────────────────────────────────────────────────────────


def test_sentence_fully_carried_shares_a_content_token():
    from applire.services.profile.reconcile.ops import AddBullets

    text = "I led the Kubernetes migration for ACME."
    ops = [AddBullets(target="w1", achievements=["Led the Kubernetes migration"])]

    result = compute_not_applied(text, ops)

    assert not any(item.kind == "sentence" for item in result)


def test_sentence_sharing_no_content_token_is_reported():
    from applire.services.profile.reconcile.ops import AddBullets

    text = "I introduced a completely unrelated greenhouse gardening hobby."
    ops = [AddBullets(target="w1", achievements=["Led the Kubernetes migration"])]

    result = compute_not_applied(text, ops)

    sentences = [item for item in result if item.kind == "sentence"]
    assert len(sentences) == 1
    assert sentences[0].reason == "no_op_carried_it"
    assert "greenhouse" in sentences[0].span.lower()


def test_stopwords_alone_do_not_count_as_a_shared_token():
    from applire.services.profile.reconcile.ops import AddBullets

    # "which", "their", "would" are >= 5 chars but stopwords; the only
    # non-stopword content token ("zebras") must not appear in the op.
    text = "These zebras, which their handlers would never expect, escaped."
    ops = [AddBullets(target="w1", achievements=["Completely different content here"])]

    result = compute_not_applied(text, ops)

    assert any(item.kind == "sentence" for item in result)


def test_no_ops_at_all_flags_the_sentence_content():
    text = "This whole testimony describes nothing the reconciler used."
    ops: list = []

    result = compute_not_applied(text, ops)

    assert any(item.kind == "sentence" for item in result)


def test_span_is_truncated_to_200_chars():
    from applire.services.profile.reconcile.ops import AddBullets

    long_sentence = "Absolutely nothing here overlaps with the op content whatsoever, " * 5
    ops = [AddBullets(target="w1", achievements=["unrelated"])]

    result = compute_not_applied(long_sentence.strip() + ".", ops)

    assert all(len(item.span) <= 200 for item in result)


# ── denials fold into "carried" ─────────────────────────────────────────


def test_denied_token_sentence_is_not_reported_when_denial_is_passed():
    text = "I have no blockchain experience though."
    ops: list = []  # a pure denial turn emits no ops at all

    result = compute_not_applied(text, ops, denials=["blockchain"])

    assert result == []


def test_denied_token_sentence_is_reported_without_the_denials_kwarg():
    # Documents WHY `denials` must be threaded through: omitting it makes a
    # perfectly-handled denial read as a loss.
    text = "I have no blockchain experience though."
    ops: list = []

    result = compute_not_applied(text, ops)

    assert any(item.kind == "sentence" for item in result)


# ── (c) parse-rejected ops ──────────────────────────────────────────────


def test_rejected_op_is_reported_with_its_type():
    text = "Hello."
    ops: list = []

    result = compute_not_applied(text, ops, rejected_ops=["upsert_work"])

    op_items = [item for item in result if item.kind == "op"]
    assert len(op_items) == 1
    assert op_items[0].reason == "op_rejected"
    assert op_items[0].span == "upsert_work"


def test_rejected_op_with_unknown_type_is_still_reported():
    result = compute_not_applied("Hi.", [], rejected_ops=["<unknown>"])

    op_items = [item for item in result if item.kind == "op"]
    assert len(op_items) == 1
    assert op_items[0].span == "<unknown>"


def test_no_rejected_ops_yields_no_op_kind_items():
    result = compute_not_applied("Hi there friend.", [_work_op()])

    assert not any(item.kind == "op" for item in result)


# ── everything carried ──────────────────────────────────────────────────


def test_fully_carried_testimony_yields_empty_list():
    from applire.services.profile.reconcile.ops import AddBullets

    text = "I led a team of 12 at ACME, managing a budget of 1350000 EUR."
    ops = [
        _work_op(team_size=12, budget_managed="1350000"),
        AddBullets(
            target="w1",
            achievements=["Led a team of 12 at ACME, managing a budget of 1350000 EUR"],
        ),
    ]

    result = compute_not_applied(text, ops)

    assert result == []
