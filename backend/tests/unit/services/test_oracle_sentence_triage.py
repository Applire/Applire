# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-068 (amended 2026-08-08) — the sentence-triage seam (#309 + #373).

Every claim ``extract_claims_from_letter`` returns is classified
``candidate-claim`` / ``employer-fact`` / ``epistolary-form`` in a
PRE-GRADING pass wired into ``audit_document``. Only ``candidate-claim``
proceeds to vault grading; the other two emit VISIBLE ``not_applicable``
verdicts (checker ``sentence_triage``) quoting the sentence, which leave the
``unverifiable_dominated`` denominator.

**Polarity is permissive-INVERTED (ADR-068 clause 3, extended).** A
mis-classification here exempts a real claim from audit — a hole in the
Oracle, not a false accusation — so unavailability of any kind (no provider,
provider exception, budget exhaustion, citation drop, malformed item) fails
toward AUDITING: the sentence is graded as a candidate-claim and the
non-resolution is counted in ``judgement_unavailable``. Never fail-to-exempt.

**What a mock can and cannot prove (ADR-062 clause 7).** Everything here is
WIRING: mock-registered routing, the visible-verdict shape, the citation-drop
path, the seam-unavailable fallback, and the denominator arithmetic.
Classification CORRECTNESS is charter-run evidence only — re-scoring run 7/8
tallies — and no assertion in this file claims otherwise.

Hermetic: every provider is ``MockLLMProvider`` or a small targeted stub;
no network call, no real provider, synthetic profile data only.
"""
from __future__ import annotations

import re

import pytest

from applire.prompts.oracle_triage import ORACLE_TRIAGE_ITEM_RE
from applire.providers.llm.mock import MockLLMProvider
from applire.services.oracle import audit_document

# ── synthetic vault (no real candidate data) ────────────────────────────────
# Fixed calendar dates only — never "N years ago" — so the derived tenure
# ceiling this fixture leans on cannot drift with the wall clock.
PROFILE = {
    "personal_info": {"name": "Kaile Beispiel"},
    "work_experience": [
        {
            "id": "w-labor",
            "company": "Musterlabor GmbH",
            "role": "Laborleiterin",
            "start_date": "2019-01",
            "end_date": "2026-01",
            "is_current": False,
            "responsibilities": [
                "Leitung eines Analytiklabors mit zwölf Mitarbeitenden.",
            ],
        }
    ],
    "skills": [{"name": "Laborleitung", "category": "technical"}],
}

# #373's counter-example, rebuilt from the issue's PO decision comment: a
# candidate OVERCLAIM (the vault's derivable span is seven years, 2019-01 to
# 2026-01) that is pairwise identical to the #373 employer fact on every
# deterministic signal — no first person, no company name, one figure. It
# must stay a candidate-claim and stay audited.
COUNTER_EXAMPLE = "Über 15 Jahre Erfahrung in der Laborleitung sind die Basis dafür."

# #309's run-8 verbatim non-claims (issue body, charter run 8, 2026-07-28) —
# three epistolary, one motivational, one splitter fragment (#292, whose own
# manufacture is already fixed by #398; kept here because the issue counts it
# among the five uninformative verdicts).
RUN8_NON_CLAIMS = [
    "habe ich Ihre Stellenausschreibung für den Leiter Operations (m/w/d) "
    "bei Rheinwerk Verpackungen GmbH gelesen",
    "Besonders reizt mich die Möglichkeit, meine Erfahrung in der Fertigung "
    "von Kunststoffverpackungen einzubringen",
    "Ich freue mich auf die Gelegenheit, meine Erfahrung in einem "
    "persönlichen Gespräch zu vertiefen",
    "stehe für weitere Fragen sowie zur Besprechung meiner Kündigungsfrist "
    "zur Verfügung.",
    "€ sowie Investitionsentscheidungen.",
]

# #373's run-record employer fact. It names no recipient company and carries
# no first-person pronoun, so the RETAINED deterministic pre-filter cannot
# reach it — this is the gap the seam exists to close.
EMPLOYER_FACT = "Der geplante Verbund, der über 30 Labore tragen soll, wächst weiter."

# Two genuinely checkable candidate sentences, so the denominator is never
# a single claim (a one-claim denominator makes `unverifiable_dominated`
# meaningless).
GROUNDED_SENTENCE = (
    "Bei der Musterlabor GmbH habe ich die Leitung eines Analytiklabors mit "
    "zwölf Mitarbeitenden verantwortet."
)
SUBSTANTIVE_SENTENCE = (
    "Meine Erfahrung mit SAP umfasst die Begleitung der Fertigungseinführung."
)

LETTER = {
    "body": {
        "paragraphs": [
            RUN8_NON_CLAIMS[0] + ".",
            RUN8_NON_CLAIMS[1] + ".",
            EMPLOYER_FACT,
            GROUNDED_SENTENCE,
            SUBSTANTIVE_SENTENCE,
            RUN8_NON_CLAIMS[2] + ".",
            "Gerne " + RUN8_NON_CLAIMS[3],
            RUN8_NON_CLAIMS[4],
        ]
    },
    "recipient": {"company": "Rheinwerk Verpackungen GmbH"},
}

COUNTER_LETTER = {
    "body": {"paragraphs": [COUNTER_EXAMPLE]},
    "recipient": {"company": "Rheinwerk Verpackungen GmbH"},
}


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _exempt_class(item_text: str) -> str | None:
    """Which non-candidate class this fixture sentence belongs to, or None.

    Substring-based on the fixture's own verbatim strings so a clause split
    upstream cannot desync the stub from the extraction.
    """
    t = _norm(item_text)
    for s in RUN8_NON_CLAIMS:
        if t and (t in _norm(s) or _norm(s) in t):
            return "epistolary-form"
    if t and (t in _norm(EMPLOYER_FACT) or _norm(EMPLOYER_FACT) in t):
        return "employer-fact"
    return None


class _TriageStub:
    """Targeted triage provider (the ADR-061/ADR-068 stub precedent): answers
    this fixture's known non-claims with their class, everything else
    ``candidate-claim``, always echoing the sentence VERBATIM so the
    document-side citation verifies honestly."""

    def __init__(self):
        self.systems: list[str | None] = []
        self.max_tokens: list[int] = []

    async def aparse_json(self, prompt, *, system=None, max_tokens=0, **kwargs):
        self.systems.append(system)
        self.max_tokens.append(max_tokens)
        if "sentence triage" not in (system or "").lower():
            # Not the triage chain (the narrow entailment / equivalence
            # judgement calls also arrive here) — stay neutral.
            return {"verdict": "unverifiable"}
        items = []
        for raw_index, text in ORACLE_TRIAGE_ITEM_RE.findall(prompt):
            items.append(
                {
                    "index": int(raw_index),
                    "classification": _exempt_class(text) or "candidate-claim",
                    "sentence_quote": text,
                }
            )
        return {"items": items}


class _ExplodingProvider:
    """Every LLM call fails — the seam-down (outage) fixture."""

    def __init__(self):
        self.calls = 0

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.calls += 1
        raise RuntimeError("provider unavailable")


class _UncitedTriageStub:
    """Classifies every sentence ``epistolary-form`` but quotes something the
    document never says — the citation-drop fixture."""

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        if "sentence triage" not in (system or "").lower():
            return {"verdict": "unverifiable"}
        return {
            "items": [
                {
                    "index": int(i),
                    "classification": "epistolary-form",
                    "sentence_quote": "ein Satz, den niemand geschrieben hat",
                }
                for i, _ in ORACLE_TRIAGE_ITEM_RE.findall(prompt)
            ]
        }


def _triage_results(report):
    return [r for r in report.claims if r.verdict.checker == "sentence_triage"]


# ── 1. #373's counter-example stays a candidate-claim and stays audited ─────


@pytest.mark.asyncio
async def test_counter_example_stays_audited_under_the_mock_stack():
    """#373's overclaim counter-example must never be exempted.

    WIRING pin only: ``MockLLMProvider`` answers every triage item
    ``candidate-claim`` (the permissive-inverted fail-safe — a mock must
    never hand out exemptions), so what this pins is that a candidate-claim
    classification routes the sentence into normal vault grading. That the
    model classifies THIS sentence correctly is charter-run evidence per
    ADR-062 clause 7, not something a mock can assert.
    """
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=COUNTER_LETTER, provider=MockLLMProvider()
    )
    hits = [r for r in report.claims if "15 Jahre" in r.claim.text]
    assert hits, [r.claim.text for r in report.claims]
    for r in hits:
        assert r.verdict.verdict != "not_applicable", r.verdict
        assert r.verdict.checker != "sentence_triage", r.verdict
        assert r.verdict.verdict != "grounded", r.verdict


# ── 2. the run-8 non-claims + #373's employer fact leave the denominator ────


@pytest.mark.asyncio
async def test_run8_non_claims_emit_visible_not_applicable_verdicts():
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_TriageStub()
    )
    exempted = _triage_results(report)
    assert exempted, [(r.claim.text, r.verdict.checker) for r in report.claims]
    for r in exempted:
        assert r.verdict.verdict == "not_applicable", r.verdict
        assert r.verdict.detail
        # The verdict is VISIBLE and names both the class and the sentence.
        assert (
            "epistolary-form" in r.verdict.detail
            or "employer-fact" in r.verdict.detail
        ), r.verdict.detail
        assert _norm(r.claim.text) in _norm(r.verdict.detail), r.verdict.detail


@pytest.mark.asyncio
async def test_every_run8_non_claim_and_the_employer_fact_are_exempted():
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_TriageStub()
    )
    for r in report.claims:
        if _exempt_class(r.claim.text) is None:
            continue
        assert r.verdict.verdict == "not_applicable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "sentence_triage", (r.claim.text, r.verdict)


@pytest.mark.asyncio
async def test_exempted_sentences_leave_the_unverifiable_denominator():
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_TriageStub()
    )
    checkable = len(report.claims) - report.counts["not_applicable"]
    assert checkable >= 2, report.counts
    # Nothing exempted may sit in the checkable denominator.
    assert report.counts["not_applicable"] == len(
        [r for r in report.claims if r.verdict.verdict == "not_applicable"]
    )
    assert report.unverifiable_dominated is False, report.counts


@pytest.mark.asyncio
async def test_triage_is_one_batched_call_with_a_batch_sized_token_cap():
    """ADR-068 clause 6: one list-shaped call per document, ``system=`` set,
    temperature 0, and a cap sized to the batch — never the one-verdict-sized
    ORACLE_ENTAILMENT_MAX_TOKENS."""
    from applire.constants import ORACLE_ENTAILMENT_MAX_TOKENS

    stub = _TriageStub()
    await audit_document("cover_letter", PROFILE, letter_data=LETTER, provider=stub)
    triage_calls = [
        (s, m) for s, m in zip(stub.systems, stub.max_tokens)
        if s and "sentence triage" in s.lower()
    ]
    assert len(triage_calls) == 1, triage_calls
    assert triage_calls[0][1] > ORACLE_ENTAILMENT_MAX_TOKENS


# ── 3. seam down → every sentence is audited (fail-to-audit) ────────────────


@pytest.mark.asyncio
async def test_provider_outage_audits_every_sentence_and_grants_nothing():
    provider = _ExplodingProvider()
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=provider
    )
    assert provider.calls, "the triage seam must have tried"
    assert _triage_results(report) == []
    assert report.counts["not_applicable"] == 0, [
        (r.claim.text, r.verdict) for r in report.claims
        if r.verdict.verdict == "not_applicable"
    ]
    # Every run-8 non-claim is graded rather than exempted.
    for r in report.claims:
        if _exempt_class(r.claim.text) is not None:
            assert r.verdict.verdict != "not_applicable", (r.claim.text, r.verdict)


@pytest.mark.asyncio
async def test_provider_outage_counts_the_full_triage_set_as_unavailable():
    """The magnitude semantics ADR-068's amendment names explicitly: an
    outage counts the letter's FULL triage set, not seams A/B's residual
    few."""
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_ExplodingProvider()
    )
    # No sentence in this fixture is caught by the deterministic pre-filter,
    # so the full triage set is the whole claim list.
    assert report.judgement_unavailable >= len(report.claims), (
        report.judgement_unavailable,
        len(report.claims),
    )


@pytest.mark.asyncio
async def test_no_provider_at_all_still_audits_every_sentence():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    assert _triage_results(report) == []
    assert report.counts["not_applicable"] == 0
    assert report.judgement_unavailable >= 1


# ── 4. citation drop → audited as a candidate-claim ─────────────────────────


@pytest.mark.asyncio
async def test_citation_drop_audits_the_sentence_as_a_candidate_claim():
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_UncitedTriageStub()
    )
    assert _triage_results(report) == []
    assert report.counts["not_applicable"] == 0, [
        (r.claim.text, r.verdict) for r in report.claims
        if r.verdict.verdict == "not_applicable"
    ]
    assert report.judgement_unavailable >= 1


@pytest.mark.asyncio
async def test_citation_drop_is_logged(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="applire.services.oracle.audit"):
        await audit_document(
            "cover_letter", PROFILE, letter_data=LETTER, provider=_UncitedTriageStub()
        )
    assert any(
        "ORACLE_JUDGEMENT_CITATION_DROP" in r.message and "sentence_triage" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


# ── the retained deterministic pre-filter (seam-down degrades to today) ─────


@pytest.mark.asyncio
async def test_prefilter_employer_fact_is_exempted_without_any_provider():
    """The three-signal heuristic (``Claim.is_employer_fact``) is RETAINED:
    a sentence it already marks keeps its visible ``not_applicable`` verdict
    even with the seam completely down — that retention is what makes
    seam-down degrade to today's behaviour instead of worse."""
    letter = {
        "body": {
            "paragraphs": [
                "Rheinwerk Verpackungen GmbH ist ein wachsendes "
                "Verpackungsunternehmen."
            ]
        },
        "recipient": {"company": "Rheinwerk Verpackungen GmbH"},
    }
    report = await audit_document("cover_letter", PROFILE, letter_data=letter)
    assert [r.verdict.verdict for r in report.claims] == ["not_applicable"]


# ── scope: the triage seam is the LETTER path only ─────────────────────────


@pytest.mark.asyncio
async def test_tailored_cv_bullets_are_never_triaged():
    """Tailored-CV bullets are not letter sentences — out of scope."""
    stub = _TriageStub()
    await audit_document(
        "cv",
        PROFILE,
        tailored_data={"skills": ["Laborleitung"], "summary": GROUNDED_SENTENCE},
        provider=stub,
    )
    assert not [s for s in stub.systems if s and "sentence triage" in s.lower()]


# ── prompt/schema contract ─────────────────────────────────────────────────


def test_triage_prompt_states_the_three_classes_and_the_doubt_default():
    from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT as SYS

    for cls in ("candidate-claim", "employer-fact", "epistolary-form"):
        assert cls in SYS
    # The permissive-inverted polarity has to be stated to the model too:
    # doubt resolves toward being audited.
    assert re.search(r"doubt", SYS, re.IGNORECASE)
    assert "sentence_quote" in SYS
