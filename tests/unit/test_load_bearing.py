# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#306/#315 — unit tests for the shared "load-bearing claim" concept.

The fixture numbers below reproduce the actual figure set from charter run
#7, case 2's cover-letter chain (backend/logs/llm/2026-07-27.jsonl,
chain=cover_letter, 12:11:00-12:15:10): round 0 (the pre-review draft, the
one the evidence-blind substitution fell back to) is missing the
'61 % -> 73 %' OEE arc that every later round carried.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.load_bearing import (
    bullet_carries_figure,
    figures_present,
    load_bearing_fn_from_ledger,
    load_bearing_universe_from_ledger,
    retained_load_bearing_figures,
    stringify_draft,
)

LEDGER = [
    {
        "concept": "Budget- und Investitionsverantwortung",
        "status": "direct",
        "claimable": True,
        "evidence": "Budgetverantwortung ca. 6 Mio. € (Personal, Instandhaltung, Material-Gemeinkosten).",
    },
    {
        "concept": "OEE-Verbesserung",
        "status": "direct",
        "claimable": True,
        "evidence": "OEE im Spritzguss in 18 Monaten von 61 % auf 73 % gesteigert.",
    },
    # A gap concept must never contribute a figure — a gap is not claimable.
    {
        "concept": "Six Sigma",
        "status": "gap",
        "claimable": False,
        "evidence": "",
    },
    # A partial (not direct) concept must never contribute either — only
    # direct+claimable is "load-bearing" per the shared definition.
    {
        "concept": "SAP S/4HANA",
        "status": "partial",
        "claimable": True,
        "evidence": "SAP-Kenntnisse aus 3 Projekten, keine S/4HANA-Zertifizierung.",
    },
]

ROUND_0_LETTER = {
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Die Ausschussquote sank von 4,1 % auf 2,3 %.",
        ]
    }
}
ROUND_5_LETTER = {
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Die Ausschussquote sank von 4,1 % auf 2,3 %. Die OEE im Spritzguss "
            "steigerte ich von 61 % auf 73 %.",
        ]
    }
}


def test_stringify_draft_flattens_nested_dict():
    text = stringify_draft(ROUND_0_LETTER)
    assert "Sehr geehrte Damen und Herren," in text
    assert "4,1 %" in text


def test_stringify_draft_handles_lists_and_scalars():
    draft = {"skills": ["Python", "SAP"], "years": 8, "active": True, "note": None}
    text = stringify_draft(draft)
    assert "Python" in text
    assert "SAP" in text
    # numbers/bools/None contribute no text but must not raise.
    assert "8" not in text.split("\n")  # not stringified as a leaf


def test_universe_only_includes_direct_claimable_evidence():
    universe = load_bearing_universe_from_ledger(LEDGER)
    # 6 Mio. € from the direct+claimable budget concept.
    assert "currency:6m" in universe
    # 61 and 73 (percent) from the direct+claimable OEE concept.
    assert "percent:61" in universe
    assert "percent:73" in universe
    # "18" (Monate) is also a plain-number figure in the OEE evidence text —
    # extract_figures has no notion of "load-bearing", only "quantified value".
    assert "number:18" in universe
    # The gap concept (Six Sigma) contributes nothing — it is not claimable.
    # The partial concept (SAP) contributes nothing either — only "direct".
    assert universe == frozenset({"currency:6m", "percent:61", "percent:73", "number:18"})


def test_universe_is_empty_for_empty_or_none_ledger():
    assert load_bearing_universe_from_ledger([]) == frozenset()
    assert load_bearing_universe_from_ledger(None) == frozenset()


def test_figures_present_extracts_percent_and_currency():
    present = figures_present("Budget von 6 Mio. € und OEE von 61 % auf 73 %.")
    assert "currency:6m" in present
    assert "percent:61" in present
    assert "percent:73" in present


def test_retained_load_bearing_figures_is_a_pure_intersection():
    universe = frozenset({"percent:61", "percent:73", "currency:6m"})
    text = "Die OEE stieg von 61 % auf 73 %."  # no budget figure in this text
    retained = retained_load_bearing_figures(text, universe)
    assert retained == frozenset({"percent:61", "percent:73"})


def test_retained_load_bearing_figures_never_mints():
    """A figure present in the text but NOT in the universe (e.g. a
    completely unrelated number) must never be reported as retained — this
    function measures retention, never grounding/minting."""
    universe = frozenset({"percent:61"})
    text = "Ich habe 99 % Kundenzufriedenheit erreicht."
    retained = retained_load_bearing_figures(text, universe)
    assert retained == frozenset()


def test_load_bearing_fn_from_ledger_scores_run_7_case_2_rounds_correctly():
    """Pins the actual mechanism: round 0 (what the evidence-blind
    substitution picked) scores LOWER than round 5 (the settled draft) on
    the OEE arc — the exact loss the run exhibited."""
    fn = load_bearing_fn_from_ledger(LEDGER)
    round_0_score = fn(ROUND_0_LETTER)
    round_5_score = fn(ROUND_5_LETTER)

    assert "percent:61" not in round_0_score
    assert "percent:73" not in round_0_score
    assert "percent:61" in round_5_score
    assert "percent:73" in round_5_score
    assert len(round_0_score) < len(round_5_score)


def test_load_bearing_fn_from_ledger_with_empty_ledger_scores_everything_empty():
    fn = load_bearing_fn_from_ledger([])
    assert fn(ROUND_5_LETTER) == frozenset()


# ── #377 (ADR-067 clause 4) — bullet_carries_figure ─────────────────────────

def test_bullet_carries_figure_true_for_no_ledger_surface_form_bullet():
    """The exact #377 regression shape: no ledger keyword ('Arbeitssicherheit'/
    'Sicherheitsbeauftragter') appears here at all, but the bullet is
    load-bearing substance — a real safety-ratio improvement."""
    assert bullet_carries_figure("Unfallquote (LTIF) von 8,2 auf 3,1 gesenkt") is True


def test_bullet_carries_figure_true_for_percent_currency_and_plain_number():
    assert bullet_carries_figure("Reduzierte Ausschussquote um 12 %.") is True
    assert bullet_carries_figure("Budgetverantwortung ca. 6 Mio. €.") is True
    assert bullet_carries_figure("Führte ein Team von 12 Mitarbeitenden.") is True


def test_bullet_carries_figure_false_for_bare_year():
    """A bare tenure marker is not quantified substance on its own."""
    assert bullet_carries_figure("Bei Acme GmbH seit 2019 tätig.") is False


def test_bullet_carries_figure_false_for_prose_with_no_figure_at_all():
    assert bullet_carries_figure("Delivered Concept1 work on the platform.") is False
    assert bullet_carries_figure("Generic baseline bullet without any number.") is False


def test_bullet_carries_figure_true_when_year_and_number_both_present():
    """A year alone does not count, but a real figure sitting alongside one
    does — the exclusion is on the FIGURE KIND, not on the whole bullet."""
    assert bullet_carries_figure("Seit 2019 Team von 12 Mitarbeitenden geführt.") is True
