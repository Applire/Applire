# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Tobias Rosenbaum
"""ADR-060 third amendment (E049 49.6) — one critic engine, two mounts.

Pins the NEW load-bearing behaviour:

1. **Citation verification (SF-CRITIC.11)** — a finding survives only on
   spans provably in the documents, verified under punctuation-folded
   normalisation (the U+2019 class: a model quotes typographic punctuation,
   a document may hold ASCII, and vice versa); a fabricated quote is dropped
   AND counted, never surfaced and never fatal.
2. **Kind/mount validity** — internal_inconsistency is Pass A's only kind;
   the letter kinds are invalid there, and vice versa.
3. **Pass A** — the assembled CV judged alone; letter-less by design; report
   carries mount="cv".
4. **The MockLLMProvider fingerprint** — end-to-end through the real mock:
   before 2026-07-31 the critic had NO fingerprint, fell to the generic
   ``{"mock": ...}`` fallback on every mock-stack run, and ended
   ``judgement_error`` — the mock suites never exercised the advisory path
   at all. These tests make that regression impossible to reintroduce
   silently.
"""

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.outcome_critic import (  # noqa: E402
    _advisories_from_judgement,
    _citation_present,
    run_pass_a,
    run_pass_b,
)

_CV = {
    "professional_summary": "Verantwortlich für Gruppenkonsolidierung und Budgetprozesse.",
    "work_history": [
        {
            "company": "Arnold GmbH",
            "bullets": [
                "Verantwortlich für das Teilprojekt Intercompany-Abstimmung bei der Gruppenkonsolidierung."
            ],
        }
    ],
}
_LETTER = {
    "body": {
        "paragraphs": [
            "Mit neun Jahren Controlling-Erfahrung bringe ich die geforderte Tiefe mit."
        ]
    }
}


class _Provider:
    def __init__(self, result):
        self._result = result

    async def aparse_json(self, prompt, **kwargs):
        return self._result


# ── 1. citation verification ────────────────────────────────────────────────


def test_typographic_punctuation_does_not_defeat_the_citation_check():
    """The documented U+2019 class: the model quotes ’ where the document has
    ' (or vice versa) — a raw substring check silently drops the true finding;
    the punctuation-folded check must not."""
    units = ["Als Teamleiter verantwortete ich das 'Phoenix'-Projekt."]
    assert _citation_present("das ’Phoenix’-Projekt", units)


def test_whitespace_and_case_do_not_defeat_the_citation_check():
    units = ["Mit  neun   Jahren Controlling-Erfahrung."]
    assert _citation_present("mit neun Jahren controlling-erfahrung", units)


def test_a_fabricated_quote_is_not_present():
    assert not _citation_present("zwölf Jahre SAP-Erfahrung", ["Etwas völlig anderes."])
    assert not _citation_present("", ["Irgendein Text."])
    assert not _citation_present(None, ["Irgendein Text."])


def test_a_fabricated_quote_is_dropped_counted_and_not_fatal():
    """One hallucinated citation must not take down the round's real findings
    (SF-CRITIC.11: drops are visible — counted — never silent, never fatal)."""
    result = {
        "findings": [
            {
                "kind": "letter_only",
                "concept": "Erfundenes",
                "letter_quote": "Dieser Satz steht nirgends im Anschreiben.",
                "worth_surfacing": True,
            },
            {
                "kind": "letter_only",
                "concept": "Controlling-Erfahrung",
                "letter_quote": "Mit neun Jahren Controlling-Erfahrung bringe ich die geforderte Tiefe mit.",
                "worth_surfacing": True,
            },
        ]
    }
    advisories, dropped = _advisories_from_judgement(
        result,
        mount="letter",
        cv_units=["Ganz anderer CV-Inhalt."],
        letter_units=_LETTER["body"]["paragraphs"],
    )
    assert dropped == 1
    assert len(advisories) == 1
    assert advisories[0].concept == "Controlling-Erfahrung"


def test_numeric_inconsistency_requires_both_citations():
    """A cross-document figure finding must cite BOTH documents — one
    verified side alone is exactly the half-checked advisory SF-CRITIC.2
    warns about."""
    finding = {
        "kind": "numeric_inconsistency",
        "concept": "Controlling-Erfahrung",
        "cv_quote": "5 Jahre Controlling-Erfahrung",
        "letter_quote": "Mit neun Jahren Controlling-Erfahrung bringe ich die geforderte Tiefe mit.",
        "worth_surfacing": True,
    }
    # CV quote not in the CV units → dropped.
    advisories, dropped = _advisories_from_judgement(
        {"findings": [finding]},
        mount="letter",
        cv_units=["Ganz anderer CV-Inhalt."],
        letter_units=_LETTER["body"]["paragraphs"],
    )
    assert (len(advisories), dropped) == (0, 1)
    # Both sides verify → surfaced, with both spans in the advisory.
    advisories, dropped = _advisories_from_judgement(
        {"findings": [finding]},
        mount="letter",
        cv_units=["Kenntnisse: 5 Jahre Controlling-Erfahrung."],
        letter_units=_LETTER["body"]["paragraphs"],
    )
    assert (len(advisories), dropped) == (1, 0)
    adv = advisories[0]
    assert adv.kind == "numeric_inconsistency"
    assert "5 Jahre" in (adv.cv_state or "")
    assert "neun Jahren" in (adv.letter_state or "")
    assert "5 Jahre" in adv.message["de"] and "neun Jahren" in adv.message["de"]
    assert adv.message["en"].strip()


# ── 2. kind/mount validity ──────────────────────────────────────────────────


def test_letter_kinds_are_invalid_on_the_cv_mount_and_vice_versa():
    letter_finding = {
        "kind": "letter_only",
        "concept": "X",
        "letter_quote": _LETTER["body"]["paragraphs"][0],
        "worth_surfacing": True,
    }
    advisories, dropped = _advisories_from_judgement(
        {"findings": [letter_finding]},
        mount="cv",
        cv_units=["Egal."],
        letter_units=_LETTER["body"]["paragraphs"],
    )
    assert (len(advisories), dropped) == (0, 1)

    internal_finding = {
        "kind": "internal_inconsistency",
        "concept": "Gruppenkonsolidierung",
        "cv_quote": "Verantwortlich für Gruppenkonsolidierung und Budgetprozesse.",
        "cv_detail_quote": "Verantwortlich für das Teilprojekt Intercompany-Abstimmung bei der Gruppenkonsolidierung.",
        "worth_surfacing": True,
    }
    advisories, dropped = _advisories_from_judgement(
        {"findings": [internal_finding]},
        mount="letter",
        cv_units=[
            _CV["professional_summary"],
            _CV["work_history"][0]["bullets"][0],
        ],
        letter_units=_LETTER["body"]["paragraphs"],
    )
    assert (len(advisories), dropped) == (0, 1)


# ── 3. Pass A ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pass_a_surfaces_a_summary_broader_than_its_own_detail():
    """Run-11's Emma case: PROFIL claims blanket Gruppenkonsolidierung, the
    bullet scopes it to a Teilprojekt. Both spans citation-verified; the
    advisory names both; mount recorded."""
    result = {
        "findings": [
            {
                "kind": "internal_inconsistency",
                "concept": "Gruppenkonsolidierung",
                "cv_quote": "Verantwortlich für Gruppenkonsolidierung und Budgetprozesse.",
                "cv_detail_quote": "Verantwortlich für das Teilprojekt Intercompany-Abstimmung bei der Gruppenkonsolidierung.",
                "worth_surfacing": True,
            }
        ]
    }
    report = await run_pass_a(
        cv_tailored=_CV,
        job_role_title="Group Controller",
        jd_excerpt=None,
        provider=_Provider(result),
        enabled=True,
    )
    assert report.ran is True
    assert report.mount == "cv"
    assert report.reason is None
    assert len(report.advisories) == 1
    adv = report.advisories[0]
    assert adv.kind == "internal_inconsistency"
    assert adv.letter_state is None
    assert "Teilprojekt" in (adv.cv_detail or "")
    assert "Teilprojekt" in adv.message["de"]


@pytest.mark.asyncio
async def test_pass_a_short_circuits_without_a_cv():
    report = await run_pass_a(
        cv_tailored=None,
        job_role_title=None,
        jd_excerpt=None,
        provider=_Provider({"findings": []}),
        enabled=True,
    )
    assert report.ran is False
    assert report.reason == "missing_cv"
    assert report.mount == "cv"


# ── 4. end-to-end through the real MockLLMProvider ──────────────────────────


@pytest.mark.asyncio
async def test_mock_provider_exercises_the_pass_b_advisory_path():
    """The regression this file exists for: with no fingerprint the mock fell
    to ``{"mock": ...}`` → judgement_error on every mock-stack run. The
    fingerprinted mock must yield a RAN report with a citation-verified
    advisory."""
    from applire.providers.llm.mock import MockLLMProvider

    report = await run_pass_b(
        cv_tailored=_CV,
        letter_data=_LETTER,
        keyword_ledger=None,
        job_role_title="Group Controller",
        jd_excerpt=None,
        provider=MockLLMProvider(),
        enabled=True,
    )
    assert report.ran is True
    assert report.reason is None, (
        "the mock's critic response was treated as malformed — the "
        "fingerprint regressed"
    )
    assert report.mount == "letter"
    assert len(report.advisories) == 1
    assert report.dropped_citations == 0
    # The mock's quote is lifted verbatim from the letter, so it must appear
    # in the advisory untouched.
    assert report.advisories[0].letter_state in _LETTER["body"]["paragraphs"][0]


@pytest.mark.asyncio
async def test_mock_provider_yields_a_clean_pass_a_report():
    from applire.providers.llm.mock import MockLLMProvider

    report = await run_pass_a(
        cv_tailored=_CV,
        job_role_title="Group Controller",
        jd_excerpt=None,
        provider=MockLLMProvider(),
        enabled=True,
    )
    assert report.ran is True
    assert report.reason is None
    assert report.mount == "cv"
    assert report.advisories == []
