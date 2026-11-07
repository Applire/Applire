# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#821 (founder ruling G-1, ADR-059 amended 2026-11-07) — a denial floors only
the concept it names; the scope travels with the row to every surface and to
the writers. And 787:167 (D12): a turn's recorded "no" closes the open members
the classifier binds to it, in the same turn.

UX audit round 3 shapes, rebuilt with invented names:
* "formal security certification" denied, "Security" graded direct on a real
  bullet — the containment floor used to floor it (letter: "Security is new to
  my experience");
* "Independent ECMO circuit management" + "ECMO certification" denied, "ECMO"
  graded partial on an interview bullet — sidebar "You said you don't have: ECMO".
"""
from __future__ import annotations

import copy

import pytest

from applire.services import gap_coverage
from applire.services.interview import coverage_judge as cj
from applire.services.keyword_ledger import (
    DENIAL_FLOOR_EVIDENCE,
    DENIED_EVIDENCE,
    _enforce_denial_stance,
    assert_claimable_backed,
    denial_scope_wording,
    is_denial_floored,
    reevaluate_gap_ledger_against_vault,
    render_ledger_prompt_block,
    upgrade_ledger_for_concepts,
)

_SEC_BULLET = "Fixed two externally reported auth issues in the billing portal."
_ECMO_BULLET = "Cared for ECMO patients alongside perfusion-trained colleagues."


def _denial(concept, level="direct", statement="I said no."):
    return {"concept": concept, "statement": statement, "source": "interview",
            "date": "2026-11-07", "denial_level": level}


def _row(concept, status, evidence="", **extra):
    row = {"concept": concept, "surface_forms": [concept], "sources": ["required"],
           "fit_weight": 1.0, "status": status,
           "claimable": status in ("direct", "partial"), "evidence": evidence}
    row.update(extra)
    return row


def _vault(bullets, denials):
    return {
        "personal_info": {"name": "Robin Testperson"},
        "work_experience": [{
            "id": "w-1", "company": "Beispiel GmbH", "role": "Engineer",
            "start_date": "2019-01", "is_current": True,
            "responsibilities": list(bullets), "achievements": [], "technologies": [],
        }],
        "skills": [],
        "metadata": {"denied_concepts": list(denials), "enrichment_history": []},
    }


# ── the rebuild floor (`_enforce_denial_stance`) ─────────────────────────────


def test_a_certification_denial_leaves_the_broader_requirement_as_graded():
    row = _row("Security", "direct", _SEC_BULLET)
    out = _enforce_denial_stance([row], [_denial("formal security certification")])
    assert out[0] == row


def test_a_scoped_ecmo_denial_leaves_bare_ecmo_as_graded():
    row = _row("ECMO", "partial", _ECMO_BULLET)
    denials = [_denial("Independent ECMO circuit management"), _denial("ECMO certification")]
    out = _enforce_denial_stance([row], denials)
    assert out[0]["status"] == "partial" and out[0]["claimable"] is True
    assert not is_denial_floored(out[0])


def test_the_named_concept_is_still_denied_and_carries_the_candidates_wording():
    out = _enforce_denial_stance([_row("ECMO certification", "gap")], [_denial("ECMO certification")])
    assert out[0]["status"] == "denied" and out[0]["evidence"] == DENIED_EVIDENCE
    assert out[0]["denied_by"] == "ECMO certification"
    # same wording as the concept → nothing to quote beyond the concept itself
    assert denial_scope_wording(out[0]) is None


def test_a_broader_named_denial_declares_the_narrower_row_and_is_quoted():
    out = _enforce_denial_stance([_row("Microsoft Azure", "partial", "x")], [_denial("Azure", "partial")])
    assert out[0]["status"] == "denied" and out[0]["denial_level"] == "partial"
    assert out[0]["denied_by"] == "Azure"
    assert denial_scope_wording(out[0]) == "Azure"


def test_a_classifier_binding_keeps_its_record_as_the_scope():
    row = _row("PSD2", "gap", denied_by="PSD2 licence")
    out = _enforce_denial_stance([row], [_denial("PSD2 licence")])
    assert out[0]["status"] == "denied" and out[0]["denied_by"] == "PSD2 licence"
    assert denial_scope_wording(out[0]) == "PSD2 licence"


def test_a_row_the_withdrawn_containment_floor_wrote_goes_back_to_an_unknown():
    stale = _row("Security", "gap", DENIAL_FLOOR_EVIDENCE)
    out = _enforce_denial_stance([stale], [_denial("formal security certification")])
    assert out[0]["status"] == "gap" and out[0]["claimable"] is False
    assert out[0]["evidence"] == "" and not is_denial_floored(out[0])


def test_a_stale_floor_with_a_declared_denial_is_denied_not_released():
    stale = _row("ECMO certification", "gap", DENIAL_FLOOR_EVIDENCE)
    out = _enforce_denial_stance([stale], [_denial("ECMO certification")])
    assert out[0]["status"] == "denied"


# ── the interview turn seam (`upgrade_ledger_for_concepts`) ──────────────────


def test_the_turn_seam_neither_floors_nor_upgrades_a_containment_only_concept():
    row = _row("ECMO", "partial", _ECMO_BULLET)
    ledger, changed = upgrade_ledger_for_concepts(
        [row], ["ECMO"], "I have no ECMO certification.",
        denied_concepts=[_denial("ECMO certification")],
    )
    assert ledger[0] == row and changed is False
    gap_row = _row("ECMO", "gap")
    ledger, changed = upgrade_ledger_for_concepts(
        [gap_row], ["ECMO"], "I have no ECMO certification.",
        denied_concepts=[_denial("ECMO certification")],
    )
    assert ledger[0] == gap_row and changed is False


def test_the_turn_seam_denies_a_named_concept_with_its_wording():
    ledger, changed = upgrade_ledger_for_concepts(
        [_row("Microsoft Azure", "gap")], ["Microsoft Azure"], "No Azure at all.",
        denied_concepts=[_denial("Azure")],
    )
    assert changed and ledger[0]["status"] == "denied" and ledger[0]["denied_by"] == "Azure"


# ── the vault re-evaluation and the persist heal ─────────────────────────────


def test_reevaluation_lifts_a_contained_term_on_a_real_bullet():
    vault = _vault([_ECMO_BULLET], [_denial("ECMO certification")])
    ledger, changed = reevaluate_gap_ledger_against_vault([_row("ECMO", "gap")], vault)
    assert changed and ledger[0]["claimable"] is True


def test_reevaluation_still_skips_a_named_denial():
    vault = _vault(["Worked with Azure DevOps pipelines."], [_denial("Azure")])
    ledger, changed = reevaluate_gap_ledger_against_vault([_row("Azure", "gap")], vault)
    assert not changed and ledger[0]["claimable"] is False


def test_the_persist_heal_keeps_a_backed_claim_beside_a_scoped_denial():
    vault = _vault([_SEC_BULLET], [_denial("formal security certification")])
    row = _row("Security", "direct", _SEC_BULLET, backing={"quote": _SEC_BULLET, "instrument": "quote"})
    healed, violations = assert_claimable_backed([row], vault, seam="test")
    assert violations == [] and healed[0]["status"] == "direct"


def test_the_persist_heal_denies_a_claim_the_candidate_named_with_its_wording():
    vault = _vault(["Worked with Azure DevOps pipelines."], [_denial("Azure")])
    healed, violations = assert_claimable_backed(
        [_row("Microsoft Azure", "direct", "Worked with Azure DevOps pipelines.")], vault, seam="test"
    )
    assert violations and healed[0]["status"] == "denied" and healed[0]["denied_by"] == "Azure"


# ── surfaces: the member chip quotes the scope ───────────────────────────────


def _cluster(members, declined=()):
    return {"id": "c1", "label": "Payments", "category": "B", "gaps": list(members),
            "jd_skills": list(members), "jd_context": "",
            "outcome": {"asked": 1, "covered": [], "declined": list(declined), "session_ids": []}}


def test_a_bound_member_chip_carries_the_candidates_wording():
    ledger = [_row("PSD2", "denied", DENIED_EVIDENCE, denied_by="PSD2 licence")]
    out = gap_coverage.member_statuses(_cluster(["PSD2"]), ledger, [_denial("PSD2 licence")])
    assert out[0]["status"] == "declined" and out[0]["denied_by"] == ["PSD2 licence"]


def test_a_contained_member_is_not_declined_and_carries_no_wording():
    ledger = [_row("ECMO", "partial", _ECMO_BULLET)]
    denials = [_denial("Independent ECMO circuit management"), _denial("ECMO certification")]
    out = gap_coverage.member_statuses(_cluster(["ECMO"]), ledger, denials)
    assert out[0]["status"] == "partial" and "denied_by" not in out[0]


def test_a_recorded_member_declined_by_a_floor_stays_marked_floored():
    """The turn recorded the member declined off a floored row; the record does
    not make the candidate have named it (G-4 kept on the recorded path)."""
    ledger = [_row("Settlement", "gap", DENIAL_FLOOR_EVIDENCE)]
    out = gap_coverage.member_statuses(
        _cluster([], declined=["Settlement"]), ledger, [_denial("settlement rails on chain")]
    )
    assert out[0]["status"] == "declined" and out[0].get("floored") is True
    assert "denied_by" not in out[0]


def test_the_schema_publishes_the_wording_only_when_present():
    from applire.schemas.gap_cluster import GapClusterMemberStatus

    assert "denied_by" not in GapClusterMemberStatus(member="X", status="gap").model_dump()
    dumped = GapClusterMemberStatus(member="PSD2", status="declined", denied_by=["PSD2 licence"]).model_dump()
    assert dumped["denied_by"] == ["PSD2 licence"]


# ── writers: the letter states the scope, the CV is unchanged ────────────────


def test_the_letter_block_states_the_scope_never_the_bare_concept():
    ledger = [_row("PSD2", "denied", DENIED_EVIDENCE, denied_by="PSD2 licence")]
    letter = render_ledger_prompt_block(ledger, audience="letter")
    assert '"PSD2 licence"' in letter and "never the bare PSD2" in letter
    cv = render_ledger_prompt_block(ledger)
    assert "PSD2 licence" not in cv and "\n  - PSD2" in cv


# ── 787:167 (D12): the turn's recorded "no" closes the members it rules out ──


_JOB = {"role_title": "Backend Engineer", "required_skills": ["BaFin-regulated environments", "Python"],
        "nice_to_have_skills": [], "keywords": [], "seniority_level": "senior",
        "company_culture_signals": [], "language_requirement": None, "scope_requirements": []}


class _Provider:
    def __init__(self, classifications):
        self.classifications = classifications
        self.calls: list[str] = []

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"classifications": copy.deepcopy(self.classifications)}


@pytest.mark.asyncio
async def test_the_rejudge_closes_an_open_member_the_classifier_binds_to_a_recorded_denial():
    vault = _vault(["Integrated a card checkout."], [_denial("BaFin supervision")])
    ledger = [_row("BaFin-regulated environments", "gap"), _row("Python", "direct", "x")]
    prov = _Provider([{"requirement": "BaFin-regulated environments", "status": "gap",
                       "reason": "ruled out", "denied_by": "BaFin supervision"}])
    out, moved = await cj.rejudge_open_members(
        ledger, ["BaFin-regulated environments"], job_inputs=_JOB, profile_json=vault,
        provider=prov, session_id="s-1",
    )
    assert "RECORDED DENIALS" in prov.calls[0] and "BaFin supervision" in prov.calls[0]
    row = out[0]
    assert moved == ["BaFin-regulated environments"]
    assert row["status"] == "denied" and row["denied_by"] == "BaFin supervision"
    assert row["concept"] == "BaFin-regulated environments" and row["fit_weight"] == 1.0
    facts = gap_coverage.classify_members(["BaFin-regulated environments"], out, vault["metadata"]["denied_concepts"])
    assert facts == {"BaFin-regulated environments": "declined"}


@pytest.mark.asyncio
async def test_the_rejudge_ignores_a_binding_to_an_id_that_is_no_live_record():
    vault = _vault(["Integrated a card checkout."], [_denial("BaFin supervision")])
    ledger = [_row("BaFin-regulated environments", "gap")]
    prov = _Provider([{"requirement": "BaFin-regulated environments", "status": "gap",
                       "reason": "x", "denied_by": "invented record"}])
    out, moved = await cj.rejudge_open_members(
        ledger, ["BaFin-regulated environments"], job_inputs=_JOB, profile_json=vault,
        provider=prov, session_id="s-1",
    )
    assert moved == [] and out[0]["status"] == "gap"


# ── 787:213 (job-analysis prompt v14): one thing, two names ──────────────────


def test_job_analysis_prompt_keeps_a_bracketed_name_of_the_same_thing_as_one_requirement():
    from applire.prompts import job_analysis

    flat = " ".join(job_analysis.SYSTEM_PROMPT.split())
    assert "ONE THING, TWO NAMES" in flat
    assert '"Structured handovers (SBAR)"' in flat
    assert 'put the bracketed name into "keywords"' in flat
    # the example-list and optional-qualifier shapes keep their own rules
    assert "a bracketed example LIST" in flat


def test_783_242_a_compound_denial_no_longer_floors_the_requirement_it_mentions():
    """783:242 (post-merge E2E, SF-GAP.10/.32): the candidate denied writing
    reference designs FOR integration platforms; "Integration platforms" went
    direct -> gap by containment. Under G-1 the row keeps the classifier's grade."""
    from applire.services.keyword_ledger import _enforce_denial_stance

    denial = {
        "concept": "Referenzdesigns für Integrationsplattformen",
        "statement": "Referenzdesigns für Integrationsplattformen habe ich nie geschrieben.",
        "source": "interview", "date": "2026-11-01", "denial_level": "direct",
    }
    row = {
        "concept": "Integration platforms",
        "surface_forms": ["Integration platforms", "Integrationsplattformen"],
        "sources": ["required"], "fit_weight": 1.0, "status": "direct",
        "claimable": True, "evidence": "Schnittstellenplattform aufgebaut",
    }
    assert _enforce_denial_stance([dict(row)], [denial], None) == [row]
