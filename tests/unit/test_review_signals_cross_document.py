# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#702 (ADR-060 amended 2026-10-07) — cross-document advisories grouped per
letter sentence and weighted. Pure, no LLM.

The fixture is the persisted Pass-B critic report of the 2026-09-13 Nougat
delivery run on the synthetic ``operations_marcus_de`` case: seven advisories,
three of which (Kosmetik-Verpackungen / Sauberraumbereich seit 2021 /
ISO-9001-Audit-Praxis) quote ONE sentence — the sentence both blind panelists
named as their strongest concern.
"""
import json
from pathlib import Path

from applire.schemas.outcome_critic import CriticAdvisory, OutcomeCriticReport
from applire.services.outcome_critic import group_cross_document

FIXTURE = Path(__file__).resolve().parents[1] / "files" / "review_signals" / "critic-report-2026-09-13-marcus.json"

TRANSFER_SENTENCE = (
    "Hygiene- und Dokumentationsdisziplin aus Kosmetik-Verpackungen, einem "
    "Sauberraumbereich seit 2021 und zehn Jahren ISO-9001-Audit-Praxis sowie neun "
    "Jahre Kunststofftechnik mit Spritzguss und Montage sind jedoch übertragbare "
    "Grundlagen."
)


def _adv(kind, concept, letter_state, cv_state=None):
    return CriticAdvisory(kind=kind, concept=concept, letter_state=letter_state, cv_state=cv_state)


def _report_0913() -> OutcomeCriticReport:
    return OutcomeCriticReport.model_validate(json.loads(FIXTURE.read_text())["report"])


def test_0913_three_benign_rows_become_one_weighted_item():
    report = _report_0913()
    assert len(report.advisories) == 7
    items = report.cross_document
    first = items[0]
    assert first.weight == "high"
    assert first.letter_state == TRANSFER_SENTENCE
    for concept in ("Kosmetik-Verpackungen", "Sauberraumbereich seit 2021", "ISO-9001-Audit-Praxis"):
        assert concept in first.concepts
    # The other transfer-sentence advisories do not survive as rows of their own.
    other_concepts = [c for it in items[1:] for c in it.concepts]
    assert "Sauberraumbereich seit 2021" not in other_concepts
    assert "ISO-9001-Audit-Praxis" not in other_concepts
    # Exactly one high item on this run.
    assert [it.weight for it in items].count("high") == 1


def test_0913_fragment_quote_folds_into_the_sentence_it_belongs_to():
    """Kunststofftechnik-Erfahrung's letter_state is a clause of the transfer
    sentence; containment groups it there instead of a second row."""
    items = _report_0913().cross_document
    assert "Kunststofftechnik-Erfahrung" in items[0].concepts
    assert len(items) == 4


def test_standard_numbers_are_not_figures():
    """The keyword-list sentence "… ISO 9001, ISO 45001 …" carries no figure."""
    items = _report_0913().cross_document
    feinplanung = next(it for it in items if "Feinplanung" in it.concepts)
    assert feinplanung.weight == "normal"
    assert feinplanung.figures == []


def test_letter_richer_figures_already_in_the_cv_do_not_weigh():
    items = _report_0913().cross_document
    mes = next(it for it in items if "MES-Einführung" in it.concepts)
    assert mes.weight == "normal"


def test_letter_richer_alone_is_never_high_even_with_a_figure():
    """Measured: letter-richer figures doubled the high set without a panel hit."""
    items = group_cross_document([_adv("letter_richer", "ISO 9001", "zehn Jahre ISO-9001-Audit-Praxis", "ISO 9001")])
    assert items[0].weight == "normal"


def test_letter_only_with_a_year_is_high_and_names_the_figure():
    items = group_cross_document([_adv("letter_only", "Sauberraum", "Sauberraumbereich bei Weberit seit 2021")])
    assert items[0].weight == "high"
    assert items[0].figures == ["2021"]


def test_letter_only_without_a_figure_is_normal():
    items = group_cross_document([_adv("letter_only", "Englisch", "Meine Englischpraxis umfasst Werksbesuche.")])
    assert items[0].weight == "normal"


def test_spelled_duration_counts_as_a_figure():
    items = group_cross_document([_adv("letter_only", "Audit", "zehn Jahren Audit-Praxis bei X")])
    assert items[0].weight == "high"
    assert items[0].figures == ["zehn Jahren"]


def test_high_items_come_first_and_order_is_stable():
    advs = [
        _adv("letter_only", "A", "Satz A ohne Zahl."),
        _adv("letter_only", "B", "Satz B seit 2019."),
        _adv("letter_only", "C", "Satz C ohne Zahl."),
    ]
    assert [it.concepts for it in group_cross_document(advs)] == [["B"], ["A"], ["C"]]


def test_numeric_and_internal_inconsistencies_are_not_cross_document_items():
    advs = [
        CriticAdvisory(kind="numeric_inconsistency", concept="Team", cv_state="38", letter_state="40"),
        CriticAdvisory(kind="internal_inconsistency", concept="Summary", cv_state="x", cv_detail="y"),
    ]
    assert group_cross_document(advs) == []


def test_key_is_the_critic_producer_plus_the_quote_fold():
    from applire.services.scope_requirements import _norm_quote

    items = _report_0913().cross_document
    assert items[0].key == f"critic:{_norm_quote(TRANSFER_SENTENCE)}"


def test_legacy_report_without_the_field_derives_it_and_dump_carries_it():
    raw = json.loads(FIXTURE.read_text())["report"]
    assert "cross_document" not in raw
    dumped = OutcomeCriticReport.model_validate(raw).model_dump(mode="json")
    assert dumped["cross_document"][0]["weight"] == "high"


def test_a_stored_cross_document_blob_is_ignored_and_recomputed():
    raw = json.loads(FIXTURE.read_text())["report"]
    raw["cross_document"] = [{"key": "critic:forged", "letter_state": "x", "weight": "high"}]
    report = OutcomeCriticReport.model_validate(raw)
    assert all(it.key != "critic:forged" for it in report.cross_document)


def test_empty_and_not_run_reports_have_no_items():
    assert OutcomeCriticReport(ran=False, reason="disabled").cross_document == []
    assert OutcomeCriticReport(ran=True).cross_document == []
