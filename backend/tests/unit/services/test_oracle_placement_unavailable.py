# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G13 — a placement judgement that was asked and gave no usable answer makes the
audit incomplete. The claim keeps its own verdict (an unavailable answer never
accuses, ADR-068), and the report says how many placements went unanswered
(``placement_unavailable``) so the audit seam can hold the document as "not
re-checked" (MD-G9). Before the placement seam, the wrong-employer sentence below was
held without any model; an outage must not be the way it gets released.

"Uncertain" or "no position" IS an answer: it keeps the verdict and is not counted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from applire.prompts.oracle_judgement import ORACLE_JUDGEMENT_SYSTEM_PROMPT
from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT
from applire.services.allowance import AdmissionRefused
from applire.services.oracle import audit_document, incomplete_reason
from applire.services.oracle.selfaudit import build_self_audit_report
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_oracle_letter_placement import (  # noqa: E402
    BRUECKENBAU_LINE,
    FOREIGN,
    MIXED_PROFILE,
    PROFILE,
    RUN,
    _letter,
)

_ITEM_RE = re.compile(r"^ITEM (\d+) \(mode: (\w+)\):\nCLAIM: (.*)$", re.M)

FOREIGN_2 = "I also mentor two mid-level engineers."

DE_PROFILE = {
    "personal_info": {"name": "Dario Beispiel"},
    "work_experience": [
        {**PROFILE["work_experience"][0], "responsibilities": [
            "Verantwortung für die GitLab-CI/CD-Pipelines von vier Services.",
            "Prometheus- und Grafana-Monitoring mit RED-Metrik-Dashboards für alle vier Services aufgebaut.",
            "Betreuung von zwei Mid-Level-Engineers als Mentor.",
        ]},
        {**PROFILE["work_experience"][1], "responsibilities": [
            "Stripe-Checkout mit Webhooks integriert und Auszahlungen mit Rechnungen abgeglichen.",
            "Das Incident-Runbook des Teams geschrieben.",
        ]},
    ],
}
DE_RUN = ("Bei der Brückenbau Fintech GmbH habe ich den Stripe-Checkout mit Webhooks integriert "
          "und Auszahlungen mit Rechnungen abgeglichen. ")
DE_FOREIGN = ("Außerdem habe ich Prometheus- und Grafana-Monitoring mit RED-Metrik-Dashboards "
              "für alle vier Services aufgebaut.")


def _de_letter(*sentences):
    return {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", DE_RUN + " ".join(sentences)]}}


CASES = {
    "en": (PROFILE, _letter(FOREIGN), "en", "I also built Prometheus"),
    "de": (DE_PROFILE, _de_letter(DE_FOREIGN), "de", "Prometheus- und Grafana"),
}


class _Down:
    """Triage: every sentence a candidate. Placement: ``placement(items)`` decides
    (raise, or return a body). Other modes: uncertain."""

    def __init__(self, placement, owner=None):
        self.placement, self.owner = placement, owner
        self.placement_calls = 0

    async def aparse_json(self, prompt, system=None, **kw):
        if system == ORACLE_TRIAGE_SYSTEM_PROMPT:
            ss = re.findall(r"^SENTENCE \d+: (.*)$", prompt, flags=re.M)
            return {"items": [{"index": i, "classification": "candidate-claim", "sentence_quote": s}
                              for i, s in enumerate(ss)]}
        items = _ITEM_RE.findall(prompt)
        if items and all(mode == "placement" for _, mode, _ in items):
            self.placement_calls += 1
            return self.placement(items)
        if self.owner is not None and any(mode == "cross_language" for _, mode, _ in items):
            return self.owner(items)
        return {"items": [{"index": int(i), "corresponds": "uncertain", "vault_quote": ""} for i, _, _ in items]}


def _raise(exc):
    def f(items):
        raise exc
    return f


def _each(answer):
    return lambda items: {"items": [{"index": int(i), **answer(claim)} for i, _, claim in items]}


FAILURES = {
    "timeout": _raise(TimeoutError("provider timeout")),
    "provider_error": _raise(RuntimeError("502")),
    "refused": _raise(AdmissionRefused("allowance_exhausted")),
    "malformed_body": lambda items: {"answer": "Brückenbau"},
    "malformed_item": _each(lambda c: {"corresponds": "maybe", "vault_quote": ""}),
    # "GmbH" stands in both position lines: not exactly one position
    "citation_drop": _each(lambda c: {"corresponds": True, "vault_quote": "GmbH"}),
}


def _verdict(report, needle):
    hits = [r for r in report.claims if needle in r.claim.text]
    assert hits, [r.claim.text for r in report.claims]
    return hits[0].verdict


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("failure", list(FAILURES))
@pytest.mark.asyncio
async def test_an_unanswered_placement_makes_the_audit_incomplete_without_accusing(failure, lang):
    profile, letter, doc_lang, needle = CASES[lang]
    judge = _Down(FAILURES[failure])
    report = await audit_document("cover_letter", profile, letter_data=letter, provider=judge,
                                  document_language=doc_lang)
    assert judge.placement_calls == 1, "the placement judge was never asked"
    assert report.placement_unavailable == 1
    assert _verdict(report, needle).verdict != "misattributed"
    assert incomplete_reason(report) == "placement_unavailable"
    assert incomplete_reason(report.model_dump(mode="json")) == "placement_unavailable"


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("answer", [("uncertain", ""), (False, "")])
@pytest.mark.asyncio
async def test_an_unsure_or_no_position_answer_is_an_answer(answer, lang):
    profile, letter, doc_lang, needle = CASES[lang]
    judge = _Down(_each(lambda c: {"corresponds": answer[0], "vault_quote": answer[1]}))
    report = await audit_document("cover_letter", profile, letter_data=letter, provider=judge,
                                  document_language=doc_lang)
    assert judge.placement_calls == 1
    assert report.placement_unavailable == 0
    assert incomplete_reason(report) is None


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.asyncio
async def test_a_placed_answer_is_complete_and_accuses(lang):
    profile, letter, doc_lang, needle = CASES[lang]
    judge = _Down(_each(lambda c: {"corresponds": True, "vault_quote": BRUECKENBAU_LINE}))
    report = await audit_document("cover_letter", profile, letter_data=letter, provider=judge,
                                  document_language=doc_lang)
    assert report.placement_unavailable == 0 and incomplete_reason(report) is None
    if lang == "en":  # the DE twin re-grades through the owner-language check (uncertain here)
        assert _verdict(report, needle).verdict == "misattributed"


@pytest.mark.asyncio
async def test_one_unanswered_placement_among_several_answered_is_counted_alone():
    def answer(items):
        out = []
        for i, _, claim in items:
            if "mentor" in claim:
                out.append({"index": int(i), "corresponds": "maybe", "vault_quote": ""})
            else:
                out.append({"index": int(i), "corresponds": True, "vault_quote": BRUECKENBAU_LINE})
        return {"items": out}

    judge = _Down(answer)
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN, FOREIGN_2),
                                  provider=judge, document_language="en")
    assert report.placement_unavailable == 1
    assert _verdict(report, "I also built Prometheus").verdict == "misattributed"
    assert _verdict(report, "mentor").verdict != "misattributed"
    assert incomplete_reason(report) == "placement_unavailable"


@pytest.mark.asyncio
async def test_an_answered_placement_then_an_unanswered_owner_language_check_keeps_the_named_rule():
    # The placement was answered; the owner-language check that follows is the
    # named-anchor path's own rule (MD-Q11: no grant, the flag stands) — complete.
    judge = _Down(_each(lambda c: {"corresponds": True, "vault_quote": BRUECKENBAU_LINE}),
                  owner=_raise(TimeoutError("provider timeout")))
    report = await audit_document("cover_letter", MIXED_PROFILE, letter_data=_letter(FOREIGN),
                                  provider=judge, document_language="en")
    assert _verdict(report, "I also built Prometheus").verdict == "misattributed"
    assert report.placement_unavailable == 0


@pytest.mark.asyncio
async def test_a_crashed_placement_step_is_incomplete(monkeypatch):
    from applire.services.oracle import audit as audit_mod

    async def crash(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(audit_mod, "_resolve_letter_placements", crash)
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN),
                                  provider=_Down(_raise(TimeoutError())), document_language="en")
    assert report.placement_unavailable == 1
    assert incomplete_reason(report) == "placement_unavailable"


@pytest.mark.asyncio
async def test_no_provider_counts_as_unanswered():
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter(FOREIGN))
    assert report.placement_unavailable == 1


@pytest.mark.asyncio
async def test_a_letter_with_nothing_to_place_is_complete():
    judge = _Down(_raise(TimeoutError("never called")))
    report = await audit_document("cover_letter", PROFILE, letter_data=_letter("I look forward to discussing the role with you."),
                                  provider=judge, document_language="en")
    assert report.placement_unavailable == 0


@pytest.mark.asyncio
async def test_a_cv_never_runs_placement_and_is_never_incomplete_for_it():
    judge = _Down(_raise(TimeoutError("never called")))
    tailored = {"experience": [{"company": "Brückenbau Fintech GmbH", "role": "Backend Engineer",
                                "bullets": ["Built Prometheus and Grafana monitoring with RED-metrics dashboards."]}]}
    report = await audit_document("cv", PROFILE, tailored_data=tailored, provider=judge, document_language="en")
    assert judge.placement_calls == 0
    assert report.placement_unavailable == 0 and incomplete_reason(report) is None
    as_dict = await build_self_audit_report(PROFILE, tailored_data=tailored, provider=judge, document_language="en")
    assert incomplete_reason(as_dict) is None


def test_incomplete_reason_reads_what_it_is_given():
    assert incomplete_reason(None) is None
    assert incomplete_reason({}) is None
    assert incomplete_reason({"placement_unavailable": 0}) is None
    assert incomplete_reason({"placement_unavailable": 2}) == "placement_unavailable"
    assert incomplete_reason({"placement_unavailable": "2"}) is None  # not a count: unknown, not a hold
