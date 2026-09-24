# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""N-1 (re-probe on eebd534d, covered by RULING E-1) — numbers with a time or
data-size unit are figures of their own kind, canonicalised to ms / bytes.

Before: ``extract_figures("from 1.8s to 240ms") == []`` and "45 min to 8 min"
yielded only 45 (as a plain count), so a vault saying "1.8s" never backed a
document's "1.8 seconds" and the Oracle flagged a true figure as unbacked.
"""
import pytest

from applire.services.oracle.matchers.figures import extract_figures, extract_spelled_figures
from applire.services.oracle.matchers.grounding import match_figures
from applire.services.oracle.matchers.vault import build_vault_index


def _vals(text):
    return [(f.kind, f.value) for f in extract_figures(text)]


@pytest.mark.parametrize("text, expected", [
    ("from 1.8s to 240ms", [("duration", "1800"), ("duration", "240")]),
    ("1.8 seconds to 240 milliseconds", [("duration", "1800"), ("duration", "240")]),
    ("1800 ms", [("duration", "1800")]),
    ("45 min to 8 min", [("duration", "2700000"), ("duration", "480000")]),
    ("2 hours", [("duration", "7200000")]),
    ("in 2h", [("duration", "7200000")]),
    ("24 h Bereitschaft", [("duration", "86400000")]),
    ("von 3 Sek. auf 800 Millisekunden", [("duration", "3000"), ("duration", "800")]),
    ("4 Std. pro Woche", [("duration", "14400000")]),
    ("15 Minuten", [("duration", "900000")]),
    ("1.5 GB", [("datasize", "1500000000")]),
    ("1500 MB", [("datasize", "1500000000")]),
])
def test_unit_figures_are_canonicalised(text, expected):
    assert _vals(text) == expected


@pytest.mark.parametrize("text", [
    "10m sprint", "3D printing", "B2B platform", "the 1990s", "10s of thousands",
    "5 minor bugs", "200 Mb",
])
def test_unit_traps_yield_no_duration_or_datasize(text):
    assert not [k for k, _ in _vals(text) if k in ("duration", "datasize")], _vals(text)


def test_five_min_is_a_duration_never_a_multiplied_count():
    assert _vals("5 min") == [("duration", "300000")]


def _index(text):
    return build_vault_index({
        "personal_info": {"name": "Daniel Kovač"},
        "work_experience": [{
            "id": "w1", "company": "Finleap Build GmbH", "role": "Backend Engineer",
            "start_date": "2019-01", "end_date": None, "responsibilities": [text],
        }],
    })


def _backs(vault_text, doc_text):
    res = match_figures(extract_figures(doc_text), _index(vault_text))
    return [f.raw for f, _ in res.matched], [f.raw for f in res.unmatched]


def test_vault_1_8s_backs_1_8_seconds_and_240ms_backs_240_milliseconds():
    matched, unmatched = _backs("Cut p95 latency from 1.8s to 240ms.",
                                "reduced latency from 1.8 seconds to 240 milliseconds")
    assert matched == ["1.8 seconds", "240 milliseconds"] and unmatched == []


def test_vault_2_hours_does_not_back_a_count_of_2_or_20():
    _, unmatched = _backs("Nightly job runs in 2 hours.", "led 20 engineers")
    assert unmatched == ["20"]
    idx = _index("Nightly job runs in 2 hours.")
    assert ("number", "2") not in idx.figure_map


def test_vault_count_does_not_back_a_duration():
    matched, unmatched = _backs("Led 45 engineers.", "cut review time to 45 min")
    assert matched == [] and unmatched == ["45 min"]


def test_spelled_vault_duration_backs_a_digit_duration():
    assert ("duration", "7200000") in [(f.kind, f.value) for f in extract_spelled_figures("in two hours")]
    matched, unmatched = _backs("The rebuild took two hours.", "rebuilt in 2 hours")
    assert matched == ["2 hours"] and unmatched == []


def test_a_count_with_the_same_canonical_digits_never_backs_a_duration():
    """Cross-kind rule (#215): 1800 records are not 1.8 seconds (= 1800 ms)."""
    matched, unmatched = _backs("Migrated 1800 records.", "p95 of 1.8s")
    assert matched == [] and unmatched == ["1.8s"]
