# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-068 clause 7 — ``build_self_audit_report`` gains the judgement seams.

The deterministic core stays hermetic and unchanged; ``provider``/
``document_language`` are additive, optional, and generation never fails or
blocks on a judgement problem either way (ADR-052 §4's original guarantee,
amended not broken).
"""
from __future__ import annotations

import pytest

from applire.services.oracle.selfaudit import build_self_audit_report

PROFILE = {
    "personal_info": {"name": "Petra Muster"},
    "professional_summary": {
        "de": "Erfahrene Finanzanalystin mit Fokus auf Budgetierung und Forecast."
    },
    "skills": [{"name": "Budgetierung & Forecast", "category": "technical"}],
}


@pytest.mark.asyncio
async def test_no_provider_no_trigger_judgement_unavailable_stays_zero():
    """provider=None AND no seam ever triggers (document_language=None, or a
    same-language document) — ``judgement_unavailable`` is 0, not merely
    "not incremented because nothing ran"."""
    report = await build_self_audit_report(
        PROFILE,
        tailored_data={"skills": ["Budgetierung & Forecast"]},  # grounds deterministically
        provider=None,
    )
    assert report is not None
    assert report["judgement_unavailable"] == 0


@pytest.mark.asyncio
async def test_trigger_without_provider_is_counted():
    """provider=None but a seam DOES trigger (cross-language miss) — the
    fail-safe still applies, and it IS counted."""
    report = await build_self_audit_report(
        PROFILE,
        tailored_data={"skills": ["Budgeting & Forecasting"]},  # EN vs. DE vault, deterministic miss
        provider=None,
        document_language="en",
    )
    assert report is not None
    assert report["judgement_unavailable"] >= 1
    by_loc = {c["claim"]["location"]: c for c in report["claims"]}
    assert by_loc["skills[0]"]["verdict"]["verdict"] == "unverifiable"


@pytest.mark.asyncio
async def test_provider_present_resolves_the_seam():
    class _GrantProvider:
        async def aparse_json(self, prompt, *, system=None, **kwargs):
            return {
                "items": [
                    {"index": 0, "corresponds": True, "vault_quote": "Budgetierung & Forecast"}
                ]
            }

    report = await build_self_audit_report(
        PROFILE,
        tailored_data={"skills": ["Budgeting & Forecasting"]},
        provider=_GrantProvider(),
        document_language="en",
    )
    assert report is not None
    by_loc = {c["claim"]["location"]: c for c in report["claims"]}
    assert by_loc["skills[0]"]["verdict"]["verdict"] == "grounded"
    assert by_loc["skills[0]"]["verdict"]["checker"] == "cross_language_judgement"
    assert report["judgement_unavailable"] == 0


@pytest.mark.asyncio
async def test_deterministic_core_unaffected_by_provider_presence():
    """A claim the deterministic layer already decides (no seam trigger)
    verdicts identically whether or not a provider is supplied."""

    class _NeverCalledProvider:
        async def aparse_json(self, prompt, *, system=None, **kwargs):
            raise AssertionError("must not be consulted — no seam should trigger here")

    without = await build_self_audit_report(
        PROFILE, tailored_data={"skills": ["Budgetierung & Forecast"]}, provider=None
    )
    with_provider = await build_self_audit_report(
        PROFILE,
        tailored_data={"skills": ["Budgetierung & Forecast"]},
        provider=_NeverCalledProvider(),
        document_language="de",  # same as vault — trigger fails open
    )
    assert without is not None and with_provider is not None
    assert without["claims"][0]["verdict"]["verdict"] == "grounded"
    assert with_provider["claims"][0]["verdict"]["verdict"] == "grounded"


@pytest.mark.asyncio
async def test_none_source_returns_none():
    assert await build_self_audit_report(PROFILE) is None
