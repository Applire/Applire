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

from applire.services.profile.reconcile.ops import UpsertWork
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
    # floor, so it must never be reported as a missing FIGURE. There is no
    # sentence-level fallback any more (ADR-063 amendment) — a bare "3" is
    # simply invisible to this witness.
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


# ── vault fold (false-positive shape 1, closed) ──────────────────────────


def test_figure_in_vault_text_but_no_op_is_not_reported():
    # The model correctly emits NOTHING — the figure is already a fact in
    # the pre-turn vault, unchanged. Simulates a resubmission of an
    # already-landed figure (the real-provider control case, 2026-08-28).
    text = "Mein Budget liegt bei 1350000 EUR."
    ops: list = []
    vault_text = '{"work_experience": [{"budget_managed": "1350000"}]}'

    result = compute_not_applied(text, ops, vault_text=vault_text)

    assert not any(item.kind == "figure" for item in result)


def test_figure_absent_from_both_ops_and_vault_is_reported():
    text = "Mein Budget liegt bei 1350000 EUR."
    ops: list = []
    vault_text = '{"work_experience": [{"budget_managed": "999999"}]}'  # a different figure

    result = compute_not_applied(text, ops, vault_text=vault_text)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1
    assert "1350000" in figures[0].span


def test_figure_only_in_metadata_denial_statement_is_still_reported():
    # Real-provider replay finding (2026-08-28): `metadata.denied_
    # concepts[*].statement` echoes a PRIOR turn's entire raw testimony
    # verbatim. A figure that only ever appeared inside that echoed
    # statement — never written to any content field, because the model
    # correctly dropped it — must still be reported on a resubmission. This
    # pins the CALLER contract (`testimony_bridge.py` passes
    # `prompt_profile_view(profile_json, keep=frozenset())`): `vault_text`
    # here is what a CORRECTLY content-only view looks like (metadata
    # stripped), never the naive full-profile dump a caller must not pass.
    full_profile_with_metadata = (
        '{"work_experience": [{"company": "ACME", "role": "Engineer"}], '
        '"metadata": {"denied_concepts": [{"concept": "budget", '
        '"statement": "Ich habe nie ein Budget von 480.000 EUR verantwortet."}]}}'
    )
    content_only_vault_text = (
        '{"work_experience": [{"company": "ACME", "role": "Engineer"}]}'
    )
    assert "480.000" in full_profile_with_metadata  # sanity: the figure IS in there
    assert "480.000" not in content_only_vault_text  # …but not in the filtered view

    text = "Mein Budget lag bei 480.000 EUR."
    ops: list = []

    result = compute_not_applied(text, ops, vault_text=content_only_vault_text)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1


def test_span_is_truncated_to_200_chars():
    # A figure span can only ever exceed 200 chars via a pathologically long
    # digit run (the magnitude-word tail this module reads is capped at 14
    # chars, so a realistic "1,35 Mio EUR" span never approaches the limit) —
    # this pins the defensive `[:_SPAN_MAX_CHARS]` slice itself.
    long_digit_run = "1" * 250
    ops: list = []

    result = compute_not_applied(f"Betrag: {long_digit_run} EUR.", ops)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1
    assert len(figures[0].span) == 200


# ── denials fold into "carried" (figures inside a denied statement) ──────


def test_denied_figure_is_not_reported_when_denial_is_passed():
    # A denial can itself name a figure ("nie ein Budget von 2,5 Mio
    # verantwortet") — that figure is carried by the denial receipt, not by
    # any op, so the fold must still cover it now that the channel is
    # figure-only.
    text = "Ich habe nie ein Budget von 2,5 Mio verantwortet."
    ops: list = []  # a pure denial turn emits no ops at all

    result = compute_not_applied(text, ops, denials=["Budget von 2,5 Mio"])

    assert result == []


def test_denied_figure_is_reported_without_the_denials_kwarg():
    # Documents WHY `denials` must be threaded through: omitting it makes a
    # perfectly-handled denial read as a loss.
    text = "Ich habe nie ein Budget von 2,5 Mio verantwortet."
    ops: list = []

    result = compute_not_applied(text, ops)

    figures = [item for item in result if item.kind == "figure"]
    assert len(figures) == 1


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
