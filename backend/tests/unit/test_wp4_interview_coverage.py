# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-100 cl. 9 amended 2026-11-04 (WP4, Interview #787) — the interview turn
re-judges the answered cluster's still-open members with the gap classifier.

The 11-04 E2E tier: `cluster_coverage.open_concepts` kept LIMS / ERP / API
strategy / Integration governance open after the answer that stated them, and
MES open after "Kopplung des Fertigungsleitsystems …" — the #188 seam upgrades
only a row the answer names LITERALLY and never moves a claimable `partial`.
The re-judge: one classifier call (the gap analysis's own prompt) over the open
members' deciding rows; a verdict lifts a row only UPWARD and only on a quote
`quote_in_vault` verifies; denials outrank it; a failure changes nothing.

Synthetic data only (an invented German lab-automation vault).
"""
from __future__ import annotations

import copy
import json

import pytest

from applire.services import gap_coverage
from applire.services.interview import coverage_judge as cj

_MES_BULLET = "Kopplung des Fertigungsleitsystems der Reagenzienfertigung an die Laborsysteme."
_TEAM = "Führung eines Teams von vier Technikerinnen in zwei Schichten."

VAULT = {
    "personal_info": {"name": "Mara Testfall"},
    "work_experience": [
        {
            "id": "w-1",
            "company": "Probelabor Nord GmbH",
            "role": "Laborautomatisierung",
            "start_date": "2020-01",
            "is_current": True,
            "responsibilities": [_MES_BULLET, _TEAM],
            "achievements": [],
            "technologies": ["Python"],
        }
    ],
    "skills": [{"name": "Python", "status": "confirmed"}],
    "metadata": {
        "denied_concepts": [],
        # the answers of THIS session wrote both bullets (receipts, ADR-046)
        "enrichment_history": [{
            "id": "rec-1", "source": "interview", "source_session_id": "s-1",
            "timestamp": "2026-11-04T10:00:00Z",
            "changes": [{"section": "work_experience", "field": "responsibilities",
                         "action": "added", "new_value": [_MES_BULLET, _TEAM]}],
        }],
    },
}

JOB = {
    "role_title": "Integration Lead",
    "required_skills": ["MES", "Team leadership", "Kubernetes"],
    "nice_to_have_skills": ["TOGAF"],
    "keywords": ["Python"],
    "seniority_level": "senior",
    "company_culture_signals": [],
    "language_requirement": None,
    "scope_requirements": [{"requirement": "Budget", "jd_quote": "a budget of 2m"}],
}


def _row(concept, status, *, sources=("required",), quote=None, **extra):
    row = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": list(sources),
        "fit_weight": 1.0 if "required" in sources else 0.5,
        "status": status,
        "claimable": status in ("direct", "partial"),
        "evidence": "before",
        "jd_phrase": f"You know {concept}.",
        "narrative_backed": False,
    }
    if quote:
        row["backing"] = {"quote": quote, "instrument": "quote"}
    row.update(extra)
    return row


def _ledger():
    return [
        _row("MES", "gap"),
        _row("Team leadership", "partial", quote="Führung eines Teams", adjacent_evidence="leads shifts"),
        _row("Kubernetes", "gap"),
        _row("TOGAF", "gap", sources=("nice_to_have",)),
        _row("Python", "direct", sources=("keyword",), quote="Python", narrative_backed=True),
    ]


class FakeProvider:
    """Records each call's prompt; answers with the classifications given."""

    def __init__(self, classifications=None, *, raises=None):
        self.classifications = classifications or []
        self.raises = raises
        self.calls: list[dict] = []

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.raises:
            raise self.raises
        return {"classifications": copy.deepcopy(self.classifications)}


def _cls(requirement, status, quote=None, reason="judged"):
    c = {"requirement": requirement, "status": status, "reason": reason, "surface_forms": [requirement]}
    if quote:
        c["evidence_quote"] = quote
    return c


def _by(ledger):
    return {r["concept"]: r for r in ledger}


async def _run(ledger, members, provider, vault=VAULT, session_id="s-1"):
    return await cj.rejudge_open_members(
        ledger, members, job_inputs=JOB, profile_json=vault, provider=provider, session_id=session_id
    )


# --- targets & prompt -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_call_classifies_only_the_open_members_deciding_rows_in_their_own_lists():
    prov = FakeProvider([])
    await _run(_ledger(), ["MES", "Team leadership", "Python"], prov)
    assert len(prov.calls) == 1
    prompt = prov.calls[0]["prompt"]
    req = json.loads(prompt.split("REQUIREMENTS")[-1][prompt.split("REQUIREMENTS")[-1].index("{"):].rsplit("}", 1)[0] + "}")
    # Python is direct (covered) — not re-judged; the open ones keep their list
    assert req == {"required": ["MES", "Team leadership"], "nice_to_have": [], "keywords": []}
    # the JOB ANALYSIS block is narrowed too (no second, contradicting list) and
    # carries no scope bars (their own floor, never lifted here)
    assert '"Kubernetes"' not in prompt and '"TOGAF"' not in prompt and "a budget of 2m" not in prompt
    # the gap analysis's own system prompt — one judge for Q1 (ADR-066)
    from applire.prompts.gap_analysis import SYSTEM_PROMPT

    assert SYSTEM_PROMPT in prov.calls[0]["system"]


@pytest.mark.asyncio
async def test_no_open_target_means_no_call():
    prov = FakeProvider([])
    out, lifted = await _run(_ledger(), ["Python"], prov)
    assert prov.calls == [] and lifted == [] and out == _ledger()


# --- the lift ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_verified_quote_lifts_a_gap_row_to_direct_in_place():
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET, "connects the MES")])
    out, lifted = await _run(_ledger(), ["MES"], prov)
    mes = _by(out)["MES"]
    assert lifted == ["MES"]
    assert mes["status"] == "direct" and mes["claimable"] is True
    assert mes["backing"]["quote"] == _MES_BULLET
    assert mes["backing"]["unit_path"] == "work_experience[0].responsibilities[0]"
    assert mes["evidence"] == "connects the MES"
    # identity (score slot, JD phrase) is the existing row's
    assert mes["sources"] == ["required"] and mes["fit_weight"] == 1.0 and mes["jd_phrase"] == "You know MES."
    assert mes["narrative_backed"] is True
    assert gap_coverage.classify_members(["MES"], out, []) == {"MES": "covered"}


@pytest.mark.asyncio
async def test_a_partial_to_direct_lift_drops_the_adjacency_note():
    prov = FakeProvider([_cls("Team leadership", "direct", _TEAM)])
    out, lifted = await _run(_ledger(), ["Team leadership"], prov)
    tl = _by(out)["Team leadership"]
    assert lifted == ["Team leadership"] and tl["status"] == "direct"
    assert "adjacent_evidence" not in tl


@pytest.mark.asyncio
async def test_an_unverifiable_quote_lifts_nothing():
    prov = FakeProvider([_cls("MES", "direct", "Einführung eines MES in drei Werken.")])
    out, lifted = await _run(_ledger(), ["MES"], prov)
    assert lifted == [] and out == _ledger()


@pytest.mark.asyncio
async def test_a_verdict_without_a_quote_lifts_nothing():
    prov = FakeProvider([_cls("MES", "direct", None)])
    out, lifted = await _run(_ledger(), ["MES"], prov)
    assert lifted == [] and out == _ledger()


@pytest.mark.asyncio
async def test_never_lowers_a_row():
    prov = FakeProvider([_cls("Team leadership", "gap", None)])
    out, lifted = await _run(_ledger(), ["Team leadership"], prov)
    assert lifted == [] and _by(out)["Team leadership"]["status"] == "partial"


@pytest.mark.asyncio
async def test_same_rank_is_not_a_lift():
    prov = FakeProvider([_cls("Team leadership", "partial", _TEAM, "other words")])
    out, lifted = await _run(_ledger(), ["Team leadership"], prov)
    assert lifted == [] and _by(out)["Team leadership"]["evidence"] == "before"


@pytest.mark.asyncio
async def test_a_verdict_for_a_concept_that_was_not_asked_is_ignored():
    prov = FakeProvider([_cls("Kubernetes", "direct", _MES_BULLET)])
    out, lifted = await _run(_ledger(), ["MES"], prov)
    assert lifted == [] and _by(out)["Kubernetes"]["status"] == "gap"


@pytest.mark.asyncio
async def test_a_denied_concept_is_never_lifted_even_on_a_verified_quote():
    vault = copy.deepcopy(VAULT)
    vault["metadata"]["denied_concepts"] = [{
        "concept": "MES", "statement": "Ein MES habe ich nie betreut.",
        "source": "interview", "date": "2026-11-04", "denial_level": "direct",
    }]
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET)])
    out, lifted = await _run(_ledger(), ["MES"], prov, vault=vault)
    assert lifted == [] and _by(out)["MES"]["status"] == "gap"
    # the record already reads MES declined — no target, so no call is spent
    assert prov.calls == []


@pytest.mark.asyncio
async def test_a_quote_naming_a_denied_concept_lifts_nothing():
    # second layer: the member is open (the denial is of another concept), but
    # the verdict's quote names what the candidate denied
    vault = copy.deepcopy(VAULT)
    vault["metadata"]["denied_concepts"] = [{
        "concept": "Fertigungsleitsystems", "statement": "Das Fertigungsleitsystem habe ich nie betreut.",
        "source": "interview", "date": "2026-11-04", "denial_level": "direct",
    }]
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET)])
    out, lifted = await _run(_ledger(), ["MES"], prov, vault=vault)
    assert len(prov.calls) == 1
    assert lifted == [] and _by(out)["MES"]["status"] == "gap"


@pytest.mark.asyncio
async def test_a_provider_failure_changes_nothing():
    prov = FakeProvider(raises=RuntimeError("boom"))
    out, lifted = await _run(_ledger(), ["MES"], prov)
    assert lifted == [] and out == _ledger()


@pytest.mark.asyncio
async def test_the_input_ledger_is_not_mutated():
    ledger = _ledger()
    before = copy.deepcopy(ledger)
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET)])
    await _run(ledger, ["MES"], prov)
    assert ledger == before


@pytest.mark.asyncio
async def test_a_quote_in_text_the_vault_held_before_the_session_lifts_nothing():
    """The control replay (11-04, pre-answer vault): Sonnet lifted rows on
    `technologies` / summary units the analysis had graded partial — a re-roll,
    not the answer. Only a unit an answer of THIS session wrote may back a lift."""
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET)])
    out, lifted = await _run(_ledger(), ["MES"], prov, session_id="another-session")
    assert lifted == [] and _by(out)["MES"]["status"] == "gap"


@pytest.mark.asyncio
async def test_a_cv_record_is_not_an_answer():
    vault = copy.deepcopy(VAULT)
    vault["metadata"]["enrichment_history"][0]["source"] = "cv_upload"
    prov = FakeProvider([_cls("MES", "direct", _MES_BULLET)])
    out, lifted = await _run(_ledger(), ["MES"], prov, vault=vault)
    assert lifted == []


# --- scope bars are never re-judged (adversarial pass 2026-11-04) --------------

@pytest.mark.asyncio
async def test_a_scope_bar_row_is_never_lifted():
    led = [_row("Führungsspanne ~120 MA", "gap", bar={"kind": "headcount", "value": 120})]
    prov = FakeProvider([_cls("Führungsspanne ~120 MA", "direct", quote=_TEAM)])
    _out, lifted = await _run(led, ["Führungsspanne ~120 MA"], prov)
    assert lifted == []


@pytest.mark.asyncio
async def test_the_same_row_without_a_bar_is_lifted():
    # Control: the scope test fails only because of the bar, not the fixture.
    led = [_row("Führungsspanne ~120 MA", "gap")]
    prov = FakeProvider([_cls("Führungsspanne ~120 MA", "direct", quote=_TEAM)])
    _out, lifted = await _run(led, ["Führungsspanne ~120 MA"], prov)
    assert lifted == ["Führungsspanne ~120 MA"]
