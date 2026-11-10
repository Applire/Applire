# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""784:61 (Hazelnut build 8, WP-L) — an identifier's number is not a figure.

The figure-ownership finding (``letter_figure_findings``) read the standard
"IFRS 16" as a borrowed figure: the vault states the standard under one
employer, the letter named it in a sentence about another. 11 of 13 firings on
the 10-12 baseline; 4 of 6 on the build-8 captured set (151 quality-bench
letters, ``applire-scratch/hazelnut-b8/l/fig``). The labelled set below is that
captured set's firings, sentences verbatim from the bench letters (synthetic
personas): the four identifier firings must go, the true one must stay.
"""
import json
import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parent.parent.parent
if str(_root / "backend") not in sys.path:
    sys.path.insert(0, str(_root / "backend"))

from applire.services.letter_figure_guard import (  # noqa: E402
    _extract_letter_figures,
    figure_ownership_facts,
    guard_letter_figures,
    letter_figure_findings,
)

_BENCH = _root / "tests" / "files" / "quality_bench"


def _vault(cell: str) -> dict:
    return json.loads((_BENCH / cell / "vault.json").read_text(encoding="utf-8"))


def _letter(paragraphs: list[str]) -> dict:
    return {"body": {"paragraphs": paragraphs}}


# (cell, sentence, label) — label: the finding a correct check gives.
LABELLED = [
    ("controlling_emma_de/cross_de2en",
     "At kleine Gruppe (GmbH plus Vertriebsgesellschaft Schweiz), I assessed IFRS 16 and "
     "revenue recognition in a 2024 unaudited practice calculation and completed an IFRS "
     "certificate course.", "identifier"),
    ("controlling_emma_de/cross_de2en",
     "While I lack direct US GAAP experience, my IFRS 16 leasing evaluation and group "
     "consolidation during LucaNet implementation at Brauner & Söhne Maschinenbau GmbH "
     "demonstrate transferable financial decision-making.", "identifier"),
    ("controlling_emma_de/same_de",
     "IFRS 16 und Umsatzrealisierung bewertete ich dort als nicht testierte Übungsrechnung; "
     "zusätzlich absolviert ich den Zertifikatskurs IFRS.", "identifier"),
    ("controlling_emma_de/same_de",
     "2024 bewertete ich IFRS 16 und Umsatzrealisierung als nicht testierte Übungsrechnung; "
     "IFRS-Abschlüsse erstellte ich nicht in Hauptverantwortung.", "identifier"),
    # A real figure in a sentence naming a different employer: the finding stays.
    ("it_backend_daniel/mixed_en",
     "I also defined SLOs with error budgets for the tracking API, targeting 99.9% "
     "availability, and integrated Stripe Checkout as a PSP integration at Finleap Build "
     "GmbH.", "figure"),
]


@pytest.mark.parametrize("cell,sentence,label", LABELLED)
def test_the_labelled_captured_firings(cell, sentence, label):
    findings = letter_figure_findings(_letter([sentence]), _vault(cell))
    raws = [f["raw"].strip() for f in findings]
    if label == "identifier":
        assert "16" not in raws, sentence
    else:
        assert raws == ["99.9%"], sentence


def test_the_identifier_sentence_is_kept_by_the_v1_floor():
    """The deleting floor (v1) removed the honest IFRS sentence; it no longer does."""
    sentence = LABELLED[1][1]
    letter = _letter([sentence])
    assert guard_letter_figures(letter, _vault("controlling_emma_de/cross_de2en")) is letter


def test_the_reviewer_block_carries_no_identifier_fact():
    facts = figure_ownership_facts(_letter([LABELLED[0][1]]), _vault("controlling_emma_de/cross_de2en"))
    assert all(f.value != "16" for f in facts)


@pytest.mark.parametrize("text", [
    "I assessed IFRS 16 and IAS 17 leasing.",
    "SOC 2 Type II and ISAE 3402 reports.",
    "Die Industrie-4.0-Roadmap und Industrie 4.0 im Werk.",
    "Python 3.12 services behind TLS 1.3.",
    "ISO-9001-Audits, ISO 45001 und DIN EN 9100.",
    "IFRS-16-Bilanzierung.",
])
def test_identifier_numbers_are_not_figures(text):
    assert _extract_letter_figures(text) == [], text


@pytest.mark.parametrize("text,raw", [
    ("CHF 120 Budget.", "120"),           # a currency code is an amount
    ("CAPEX 2 Mio. €.", "2"),             # a magnitude follows: a quantity
    ("den LTIF 8,2 senkte ich.", "8,2"),  # a comma decimal is a measurement
    ("latency from 1.8 seconds.", "1.8"),
    ("a team of 5+ engineers.", "5+"),
    ("99.9% availability.", "99.9%"),
    ("zwei Werkstudierende.", "zwei"),
])
def test_quantities_next_to_names_stay_figures(text, raw):
    assert raw in [f.raw.strip() for f in _extract_letter_figures(text)], text
