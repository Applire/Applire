# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-068 — bounded equivalence judgement (cross-language + restatement
seams), fail-safe polarity, batching, and the ``judgement_unavailable``
report field.

Hermetic throughout: every provider here is a small, targeted stub — never a
real network call. ``MockLLMProvider`` recognition itself is covered by
``tests/unit/test_mock_reviewer_chain_recognition.py``.
"""
from __future__ import annotations

import re

import pytest

from applire.schemas.oracle import ORACLE_REPORT_VERSION, TruthfulnessReport
from applire.services.oracle import audit_document
from applire.services.oracle.audit import (
    _resolve_seam_candidate,
    _run_judgement_batches,
    _SeamCandidate,
    ClaimVerdict,
)
from applire.services.oracle.matchers.vault import EvidenceUnit

# ── fixtures shared by the cross-language tests ──────────────────────────────
# The real #394 pair: a German vault, an English CV skill claim.
CROSS_LANG_PROFILE = {
    "personal_info": {"name": "Petra Muster"},
    "professional_summary": {
        "de": (
            "Erfahrene Finanzanalystin mit Fokus auf Budgetierung und "
            "Forecast-Erstellung fuer internationale Teams."
        )
    },
    "skills": [{"name": "Budgetierung & Forecast", "category": "technical"}],
    "work_experience": [
        {
            "id": "w1",
            "company": "Muster GmbH",
            "role": "Finanzanalystin",
            "responsibilities": [
                "Verantwortlich fuer die jaehrliche Budgetierung und das "
                "Forecasting des Bereichs."
            ],
        }
    ],
}
CROSS_LANG_TAILORED = {"skills": ["Budgeting & Forecasting"]}


class _GrantCrossLanguageProvider:
    """Judgement stub: grants the #394 EN/DE skill pair, citing the vault
    skill unit verbatim."""

    def __init__(self):
        self.calls: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.calls.append(prompt)
        return {
            "items": [
                {"index": 0, "corresponds": True, "vault_quote": "Budgetierung & Forecast"}
            ]
        }


class _FabricatedCitationProvider:
    """Judgement stub: grants, but the cited quote is NOT a real vault span."""

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        return {
            "items": [
                {"index": 0, "corresponds": True, "vault_quote": "a quote nobody ever wrote"}
            ]
        }


class _NeverCalledProvider:
    """Fails the test the moment the judgement layer is consulted at all —
    for the trigger-negative assertions."""

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        raise AssertionError("the judgement seam must not fire on this fixture")


# ── Seam A: cross-language grant / citation drop / triggers ─────────────────


@pytest.mark.asyncio
async def test_cross_language_grant_grounds_via_judgement():
    provider = _GrantCrossLanguageProvider()
    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data=CROSS_LANG_TAILORED,
        provider=provider,
        document_language="en",
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "grounded", result.verdict
    assert result.verdict.checker == "cross_language_judgement"
    assert result.verdict.evidence
    assert provider.calls, "the judgement provider must have been consulted"
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_cross_language_citation_drop_is_unavailable_and_counted():
    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data=CROSS_LANG_TAILORED,
        provider=_FabricatedCitationProvider(),
        document_language="en",
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "unverifiable", result.verdict
    assert result.verdict.checker == "cross_language_judgement"
    assert report.judgement_unavailable == 1


@pytest.mark.asyncio
async def test_cross_language_deny_keeps_the_deterministic_unbacked():
    """corresponds=false — the ADR-068 "deterministic verdict stands" branch
    (Seam A's own polarity, distinct from Seam B): the pre-existing skill-miss
    verdict, unchanged, and NOT counted as unavailable (a clean decision)."""

    class _DenyProvider:
        async def aparse_json(self, prompt, *, system=None, **kwargs):
            return {"items": [{"index": 0, "corresponds": False, "vault_quote": ""}]}

    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data=CROSS_LANG_TAILORED,
        provider=_DenyProvider(),
        document_language="en",
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "unbacked", result.verdict
    assert result.verdict.checker == "grounding"  # the ORIGINAL deterministic checker
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_same_language_miss_never_calls_the_judgement_provider():
    """Trigger negative: document_language == vault dominant_language — the
    deterministic miss stands exactly as pre-ADR-068, no judgement call."""
    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data=CROSS_LANG_TAILORED,
        provider=_NeverCalledProvider(),
        document_language="de",
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "unbacked"
    assert result.verdict.checker == "grounding"
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_document_language_none_keeps_the_seam_off():
    """Trigger negative: ``document_language=None`` (the default) fails OPEN
    to pre-ADR-068 behaviour regardless of the provider passed."""
    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data=CROSS_LANG_TAILORED,
        provider=_NeverCalledProvider(),
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "unbacked"
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_grounded_skill_never_reaches_the_judgement_seam():
    """A skill the deterministic matcher already grounds must never even
    consider the judgement layer — the seam only fires on a genuine miss."""
    report = await audit_document(
        "cv",
        CROSS_LANG_PROFILE,
        tailored_data={"skills": ["Budgetierung & Forecast"]},
        provider=_NeverCalledProvider(),
        document_language="en",
    )
    (result,) = [r for r in report.claims if r.claim.location == "skills[0]"]
    assert result.verdict.verdict == "grounded"
    assert result.verdict.checker == "grounding"


# ── Seam B: restatement (the unanchored-figure escalation) ──────────────────
# Mirrors the live shape ``test_oracle_run4_regression.py`` pins: a long
# tenure at company A (spelled-figure "five"), a second employer B named
# elsewhere, and a bare unanchored sentence borrowing A's figure.
RESTATEMENT_PROFILE = {
    "personal_info": {"name": "Jordan Weiss"},
    "work_experience": [
        {
            "id": "w-alpha",
            "company": "Alpha Systems GmbH",
            "role": "Engineering Lead",
            "is_current": True,
            "responsibilities": [
                "Lead a team of five platform engineers responsible for "
                "checkout reliability and on-call rotation.",
            ],
        },
        {
            "id": "w-beta",
            "company": "Beta Consulting AG",
            "role": "Advisor",
            "is_current": True,
            "responsibilities": [
                "Advise enterprise clients on data governance frameworks.",
            ],
        },
    ],
}
RESTATEMENT_LETTER = {
    "body": {
        "paragraphs": [
            "At Alpha Systems, I own platform reliability end to end.",
            "At Beta Consulting, I advise clients on governance programmes.",
            "My track record mentoring engineering teams of 5+ speaks for "
            "itself in any organisation.",
        ]
    }
}


def _restatement_claim(report):
    hits = [r for r in report.claims if "5+" in r.claim.text]
    assert hits, "expected a decomposed claim carrying the '5+' figure"
    return hits


class _RestatementDenyProvider:
    """corresponds=false, citing the owning unit's real text verbatim."""

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        return {
            "items": [
                {
                    "index": 0,
                    "corresponds": False,
                    "vault_quote": (
                        "Lead a team of five platform engineers responsible for "
                        "checkout reliability and on-call rotation."
                    ),
                }
            ]
        }


class _RestatementUncertainProvider:
    async def aparse_json(self, prompt, *, system=None, **kwargs):
        return {
            "items": [
                {
                    "index": 0,
                    "corresponds": "uncertain",
                    "vault_quote": (
                        "Lead a team of five platform engineers responsible for "
                        "checkout reliability and on-call rotation."
                    ),
                }
            ]
        }


@pytest.mark.asyncio
async def test_restatement_deny_with_citation_escalates_to_unbacked():
    report = await audit_document(
        "cover_letter",
        RESTATEMENT_PROFILE,
        letter_data=RESTATEMENT_LETTER,
        provider=_RestatementDenyProvider(),
    )
    for r in _restatement_claim(report):
        assert r.verdict.verdict == "unbacked", (r.claim.text, r.verdict)
        assert r.verdict.checker == "restatement_judgement"
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_restatement_uncertain_stays_unverifiable():
    report = await audit_document(
        "cover_letter",
        RESTATEMENT_PROFILE,
        letter_data=RESTATEMENT_LETTER,
        provider=_RestatementUncertainProvider(),
    )
    for r in _restatement_claim(report):
        assert r.verdict.verdict == "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "restatement_judgement"
    # a clean "uncertain" decision, not an unavailable one
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_restatement_provider_raises_is_unverifiable_and_counted():
    class _BoomProvider:
        async def aparse_json(self, prompt, *, system=None, **kwargs):
            raise RuntimeError("provider unavailable")

    report = await audit_document(
        "cover_letter",
        RESTATEMENT_PROFILE,
        letter_data=RESTATEMENT_LETTER,
        provider=_BoomProvider(),
    )
    for r in _restatement_claim(report):
        assert r.verdict.verdict == "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "restatement_judgement"
    assert report.judgement_unavailable >= 1


@pytest.mark.asyncio
async def test_restatement_no_provider_is_the_failsafe():
    report = await audit_document(
        "cover_letter", RESTATEMENT_PROFILE, letter_data=RESTATEMENT_LETTER
    )
    for r in _restatement_claim(report):
        assert r.verdict.verdict == "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "restatement_judgement"
    assert report.judgement_unavailable >= 1


# ── unchanged-by-design guards ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_canonical_figure_miss_is_unaffected():
    """A figure with NO vault occurrence at all (checker "numbers", §1) never
    touches the judgement layer, seam or no seam."""
    profile = {"personal_info": {"name": "X"}, "professional_summary": {"en": "Engineer."}}
    report = await audit_document(
        "cv",
        profile,
        tailored_data={"summary": "Delivered a 47% improvement in throughput."},
        provider=_NeverCalledProvider(),
        document_language="en",
    )
    (result,) = report.claims
    assert result.verdict.verdict == "unbacked"
    assert result.verdict.checker == "numbers"
    assert report.judgement_unavailable == 0


# ── batching (clause 6) ──────────────────────────────────────────────────────


def _fake_unit(text: str) -> EvidenceUnit:
    return EvidenceUnit(path="fake.path", text=text, text_norm=text.lower())


_ITEM_RE = re.compile(
    r"ITEM (\d+) \(mode: \w+\):.*?VAULT EVIDENCE:\n(.*?)(?=\n\nITEM \d+|\Z)", re.DOTALL
)
_EVIDENCE_LINE_RE = re.compile(r"^\s*\[\d+\] (.+)$", re.MULTILINE)


def _uncertain_response_citing_real_evidence(prompt: str) -> dict:
    """Generic judgement-stub response builder: every item comes back
    ``corresponds="uncertain"``, citing the FIRST evidence line the prompt
    itself carries for that item — always a real, verifiable quote,
    regardless of batch-local vs. global candidate numbering."""
    items = []
    for idx_str, evidence_block in _ITEM_RE.findall(prompt):
        lines = _EVIDENCE_LINE_RE.findall(evidence_block)
        items.append(
            {"index": int(idx_str), "corresponds": "uncertain", "vault_quote": lines[0] if lines else ""}
        )
    return {"items": items}


class _BatchCountingProvider:
    def __init__(self, response_builder=None):
        self.batch_sizes: list[int] = []
        self._response_builder = response_builder or (
            lambda prompt: _uncertain_response_citing_real_evidence(prompt)
        )

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        n_items = prompt.count("ITEM ")
        self.batch_sizes.append(n_items)
        return self._response_builder(prompt)


@pytest.mark.asyncio
async def test_more_than_eight_candidates_split_into_two_calls():
    candidates = [
        _SeamCandidate(
            seam="restatement",
            claim_text=f"claim {i}",
            units=[_fake_unit(f"evidence unit {i}")],
            deny_verdict=None,
            unavailable_verdict=ClaimVerdict(verdict="unverifiable", checker="restatement_judgement"),
        )
        for i in range(9)
    ]

    provider = _BatchCountingProvider()
    resolved = await _run_judgement_batches(candidates, provider)
    assert len(resolved) == 9
    assert provider.batch_sizes == [8, 1], provider.batch_sizes
    for verdict, unavailable in resolved:
        assert unavailable is False
        assert verdict.verdict == "unverifiable"


@pytest.mark.asyncio
async def test_malformed_response_degrades_only_that_subbatch():
    """A truncated/malformed response for one sub-batch degrades only that
    sub-batch (fail-safe); the other sub-batch, given a clean response,
    finalizes normally."""
    candidates = [
        _SeamCandidate(
            seam="restatement",
            claim_text=f"claim {i}",
            units=[_fake_unit(f"evidence unit {i}")],
            deny_verdict=None,
            unavailable_verdict=ClaimVerdict(verdict="unverifiable", checker="restatement_judgement"),
        )
        for i in range(9)
    ]

    calls = {"n": 0}

    async def aparse_json(prompt, *, system=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"not_items": "malformed"}  # first sub-batch (8 items): malformed
        return _uncertain_response_citing_real_evidence(prompt)

    class _Provider:
        pass

    provider = _Provider()
    provider.aparse_json = aparse_json

    resolved = await _run_judgement_batches(candidates, provider)
    assert len(resolved) == 9
    first_batch, second_batch = resolved[:8], resolved[8:]
    assert all(unavailable is True for _, unavailable in first_batch)
    assert all(unavailable is False for _, unavailable in second_batch)


# ── _resolve_seam_candidate unit-level coverage ─────────────────────────────


def test_resolve_seam_candidate_malformed_entry_is_unavailable():
    candidate = _SeamCandidate(
        seam="cross_language",
        claim_text="x",
        units=[_fake_unit("y")],
        deny_verdict=ClaimVerdict(verdict="unbacked", checker="grounding"),
        unavailable_verdict=ClaimVerdict(verdict="unverifiable", checker="cross_language_judgement"),
    )
    verdict, unavailable, reason = _resolve_seam_candidate(candidate, None)
    assert unavailable is True
    assert reason == "malformed_item"
    assert verdict is candidate.unavailable_verdict


# ── report schema (version, judgement_unavailable, counts math) ─────────────


def test_report_version_bumped_and_judgement_unavailable_serializes():
    assert ORACLE_REPORT_VERSION == "1.3"
    report = TruthfulnessReport.from_results("cv", [], judgement_unavailable=2)
    assert report.version == "1.3"
    dumped = report.model_dump(mode="json")
    assert dumped["judgement_unavailable"] == 2
    # counts math is unaffected by the new field
    assert sum(report.counts.values()) == 0
