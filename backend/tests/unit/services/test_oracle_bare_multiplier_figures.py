# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING E-1 (founder, 2026-09-24 delivery run) — a bare number with a
magnitude token is a figure, folded like the currency form.

Before: ``extract_figures("~40k daily users") == []`` while "€40k" worked, so
a vault saying "~40k" never backed a document's true "40,000" and the Oracle
issued a false "no vault evidence for figure" verdict (4 of 8 group-1 findings
on the delivery run).
"""
import pytest

from applire.services.oracle.matchers.figures import extract_figures
from applire.services.oracle.matchers.grounding import match_figures
from applire.services.oracle.matchers.vault import build_vault_index


def _vals(text):
    return [(f.kind, f.value) for f in extract_figures(text)]


@pytest.mark.parametrize("text, expected", [
    ("~40k daily users", [("number", "40000")]),
    ("200k invoices per month", [("number", "200000")]),
    ("1.2M rows", [("number", "1200000")]),
    ("12K+ users", [("number", "12000")]),
    ("3 Mio. Datensätze", [("number", "3000000")]),
    ("1,2 Millionen Sendungen", [("number", "1200000")]),
    ("7 Tsd. Kunden", [("number", "7000")]),
    ("2 Mrd Transaktionen", [("number", "2000000000")]),
])
def test_bare_multiplier_is_a_folded_number_figure(text, expected):
    assert _vals(text) == expected


@pytest.mark.parametrize("text", [
    "10m sprint",        # metres
    "a 5m run",          # metres / minutes
    "5 min stand-up",    # minutes
    "B2B platform",      # a letter, not a magnitude
    "3D printing",
    "Level 2B clearance",  # single B refused outright
    "40kg payload",      # the letter must end the token
    "1.2MB bundle",
])
def test_non_multiplier_suffixes_yield_no_magnitude(text):
    assert all(float(v) < 1000 for k, v in _vals(text)), _vals(text)


def test_currency_form_is_unchanged():
    assert _vals("€40k budget") == [("currency", "40000")]


def _index(text):
    return build_vault_index({
        "personal_info": {"name": "Daniel Kovač"},
        "work_experience": [{
            "id": "w1", "company": "Cargonaut Logistics GmbH", "role": "Senior Backend Engineer",
            "start_date": "2019-01", "end_date": None, "responsibilities": [text],
        }],
    })


def test_vault_tilde_40k_backs_document_40000():
    idx = _index("Operated a tracking backend for ~40k daily active customers.")
    res = match_figures(extract_figures("serving approximately 40,000 daily active customers"), idx)
    assert [f.value for f, _ in res.matched] == ["40000"] and res.unmatched == []


def test_vault_10m_sprint_does_not_back_ten_million():
    idx = _index("Ran a 10m sprint drill with the team.")
    res = match_figures(extract_figures("processed 10,000,000 events"), idx)
    assert res.matched == [] and [f.value for f in res.unmatched] == ["10000000"]
