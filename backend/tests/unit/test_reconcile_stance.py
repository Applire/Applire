# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Stance guard for the ADR-046 reconciler (#127, blind PQ 2026-07-04).

The blind run produced two truthfulness violations (logs/llm/2026-07-04.jsonl):

* 21:56:31Z — the answer affirmed LLM work but explicitly denied production RAG
  experience ("produktionsreife RAG-Erfahrung fehlt mir aber"); the reconciler
  emitted add_bullets technologies=["Large Language Models", "RAG"], writing the
  denied token onto the work entity and from there onto the tailored CV.
* 22:11:18Z — an off-topic answer ("We reduced churn by 3% through quarterly
  business reviews.") to a CI/CD question yielded upsert_skill Python (advanced)
  — a token appearing nowhere in gap/question/answer.

Mirror of the F4 gap-analysis fix (2026-07-02, Azure): the model's own denial
verdict outranks its ops (never-claim beats claim, ADR-040), enforced
deterministically; interview-turn token claims must be grounded in the turn's
text. Presence matching reuses THE shared predicate (surface_present, US212).
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileResult,
    UpsertCertification,
    UpsertLanguage,
    UpsertSkill,
    UpsertWork,
)
from applire.services.profile.reconcile.stance import enforce_stance


# Verbatim gap/question/answer of the 21:56:31Z blind-PQ turn (denial inversion).
_RAG_TURN = {
    "gap": "AI, LLMs & RAG Systems",
    "question": (
        "Can you share an example where you worked with or explored AI-driven "
        "systems, such as LLMs or RAG architectures, to solve a business or "
        "technical challenge?"
    ),
    "answer": (
        "Ja: Bei BioNTech leite ich aktuell ein KI-Automatisierungsprojekt auf "
        "Basis von Databricks und Large Language Models, das die Erstellung "
        "regulatorischer Dokumente automatisiert — Ziel ist eine "
        "Effizienzsteigerung von über 60%. Dabei baue ich zugleich "
        "AI-Governance-Strukturen für den Einsatz von KI in compliance-kritischen "
        "Umgebungen auf. Mit RAG-Architekturen habe ich bisher kein eigenes "
        "produktives Projekt umgesetzt; das Konzept kenne ich aus der "
        "Praxisnutzung von KI-Tools, produktionsreife RAG-Erfahrung fehlt mir aber."
    ),
}

# Verbatim gap/question/answer of the 22:11:18Z blind-PQ turn (fabrication).
_CHURN_TURN = {
    "gap": "team_size: Associate Director E2E Supply Chain Systems @ BioNTech SE",
    "question": (
        "Can you describe a specific project where you implemented CI/CD "
        "pipelines and explain the tools and processes you used?"
    ),
    "answer": "We reduced churn by 3% through quarterly business reviews.",
}


# ── Denial strip (all sources): the model's own denial verdict wins ───────────


def test_denied_technology_stripped_from_add_bullets() -> None:
    # Exact 21:56:31Z op shape: the affirmed token survives, the denied one dies.
    ops = [
        AddBullets(
            target="f0842567-2485-4a7f-a05d-f509998372a5",
            technologies=["Large Language Models", "RAG"],
        )
    ]
    out = enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    assert out[0].technologies == ["Large Language Models"]


def test_denied_skill_op_dropped_entirely() -> None:
    ops = [UpsertSkill(name="RAG", category="technical", proficiency="basic")]
    out = enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert out == []


def test_bullet_text_claiming_denied_token_dropped() -> None:
    # A denial must not resurface as free-text achievement/responsibility either.
    ops = [
        AddBullets(
            target="w1",
            achievements=[
                "Built a production RAG pipeline for regulatory documents",
                "Automated regulatory document creation with LLMs",
            ],
        )
    ]
    out = enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    assert out[0].achievements == [
        "Automated regulatory document creation with LLMs"
    ]


def test_denial_matching_uses_shared_normalisation() -> None:
    # Case fold + containment both directions: "azure" denies "Microsoft Azure".
    ops = [
        UpsertSkill(name="Microsoft Azure"),
        AddBullets(target="w1", technologies=["AZURE", "AWS"]),
    ]
    out = enforce_stance(
        ops,
        denials=["azure"],
        new_info={"gap": "cloud", "question": "Azure or AWS?", "answer": "AWS, not Azure"},
        source="interview",
    )
    assert len(out) == 1
    assert isinstance(out[0], AddBullets)
    assert out[0].technologies == ["AWS"]


def test_denial_strip_applies_to_cv_upload_source_too() -> None:
    ops = [UpsertSkill(name="Kubernetes")]
    out = enforce_stance(
        ops, denials=["Kubernetes"], new_info="…full CV text…", source="cv_upload"
    )
    assert out == []


def test_add_bullets_left_empty_by_strip_is_dropped() -> None:
    ops = [AddBullets(target="w1", technologies=["RAG"])]
    out = enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert out == []


# ── Interview grounding: token claims must appear in gap+question+answer ─────


def test_ungrounded_interview_skill_dropped() -> None:
    # Exact 22:11:18Z shape: Python appears nowhere in the turn -> fabrication.
    ops = [UpsertSkill(name="Python", category="technical", proficiency="advanced")]
    out = enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
    assert out == []


def test_skill_grounded_by_question_is_kept() -> None:
    # "Yes, six years, daily." affirms the question's token without repeating it.
    turn = {
        "gap": "Python experience",
        "question": "Do you have professional Python experience?",
        "answer": "Yes — six years, daily, in production.",
    }
    ops = [UpsertSkill(name="Python", category="technical")]
    out = enforce_stance(ops, denials=[], new_info=turn, source="interview")
    assert len(out) == 1


def test_ungrounded_technology_stripped_from_interview_bullets() -> None:
    ops = [
        AddBullets(
            target="w1",
            technologies=["Databricks", "Terraform"],
            achievements=["Led an AI automation project"],
        )
    ]
    out = enforce_stance(ops, denials=[], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    # Databricks is in the answer; Terraform is not.
    assert out[0].technologies == ["Databricks"]
    # Free-text bullets are NOT grounding-checked (legitimate paraphrase).
    assert out[0].achievements == ["Led an AI automation project"]


def test_ungrounded_language_and_certification_dropped_on_interview() -> None:
    ops = [
        UpsertLanguage(language="French", level="B2"),
        UpsertCertification(name="AWS Solutions Architect"),
    ]
    out = enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
    assert out == []


def test_grounding_not_applied_to_non_interview_sources() -> None:
    # CV import reconciles a whole staged extraction; token-presence grounding is
    # an interview-turn instrument only (scope decision, #127).
    ops = [UpsertSkill(name="Python")]
    out = enforce_stance(ops, denials=[], new_info={"cv": "…"}, source="cv_upload")
    assert len(out) == 1


def test_entity_ops_are_not_grounding_checked() -> None:
    # Work/project/volunteer upserts legitimately echo profile knowledge
    # (target merges, alternate titles — rule 7); they stay out of scope.
    ops = [UpsertWork(ref="w1", company="BioNTech SE", role="Associate Director")]
    out = enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
    assert len(out) == 1


# ── Agent-interview grounding (E045): corpus is the statement ONLY ───────────
#
# In the built-in interview, gap+question are Applire-authored, so they may
# legitimately ground a token ("Yes, six years" affirms the question's Python).
# In submit_claims ALL fields are claimant-authored — an agent could smuggle
# "Kubernetes" through its own question and pass surface-grounding without the
# candidate ever affirming it (#127's fabrication class, adversarial B3).

_AGENT_TURN = {
    "gap": "Kubernetes",
    "question": "Do you have Kubernetes experience?",
    "answer": "Yes — I ran our container platform for three years.",
}


def test_agent_interview_token_absent_from_statement_dropped() -> None:
    ops = [UpsertSkill(name="Terraform", category="technical")]
    out = enforce_stance(
        ops, denials=[], new_info=_AGENT_TURN, source="agent_interview"
    )
    assert out == []


def test_agent_interview_question_smuggle_dropped() -> None:
    # "Kubernetes" appears only in the agent-authored question/gap — on the
    # built-in interview this would ground; on the agent door it must NOT.
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    out = enforce_stance(
        ops, denials=[], new_info=_AGENT_TURN, source="agent_interview"
    )
    assert out == []
    # Sanity: the identical turn grounds on the built-in interview.
    assert len(enforce_stance(ops, denials=[], new_info=_AGENT_TURN, source="interview")) == 1


def test_agent_interview_token_in_statement_kept() -> None:
    turn = dict(_AGENT_TURN, answer="Yes, I administered Kubernetes clusters.")
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    out = enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


def test_agent_interview_denials_still_enforced() -> None:
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    turn = dict(_AGENT_TURN, answer="I have used Kubernetes only in tutorials, no real experience.")
    out = enforce_stance(
        ops, denials=["Kubernetes"], new_info=turn, source="agent_interview"
    )
    assert out == []


# ── Story upserts (E045 / US261, ADR-055 gap): denials + figure grounding ────
#
# Story `outcome`/`benchmark` figures become citable Oracle number provenance
# (ADR-052) — a fabricated benchmark would launder itself into `grounded`
# verdicts. PO ruling 2026-07-19: the guard applies to BOTH corpus-bearing
# sources (interview and agent_interview). Prose is never token-grounded
# (paraphrase is the notary's job); only figures and denials are enforced.

_STORY_TURN = {
    "gap": "Platform reliability",
    "question": "Tell me about a time you improved reliability.",
    "answer": (
        "At Acme our deploys kept failing — roughly every fifth one. I "
        "introduced canary releases and automated rollbacks. Failed deploys "
        "went from 20% to 2% within six months, measured across 40 services."
    ),
}


def _story(**over: Any):
    from applire.services.profile.reconcile.ops import UpsertStory

    base = dict(
        title="Canary releases at Acme",
        challenge="Deploys kept failing, roughly every fifth one",
        mechanism="Introduced canary releases and automated rollbacks",
        outcome="Failed deploys went from 20% to 2% within six months",
        benchmark="Measured across 40 services",
        evidence=["w1"],
    )
    base.update(over)
    return UpsertStory(**base)


@pytest.mark.parametrize("source", ["interview", "agent_interview"])
def test_grounded_story_passes(source: str) -> None:
    out = enforce_stance([_story()], denials=[], new_info=_STORY_TURN, source=source)
    assert len(out) == 1


@pytest.mark.parametrize("source", ["interview", "agent_interview"])
def test_fabricated_outcome_figure_drops_story(source: str) -> None:
    # "99.9%" appears nowhere in the turn — the whole op dies (no silent
    # figure-stripping: a benchmark-less mutation of the model's story would
    # be editorializing; the claim reports no_change and the caller restates).
    ops = [_story(outcome="Achieved 99.9% deploy success")]
    out = enforce_stance(ops, denials=[], new_info=_STORY_TURN, source=source)
    assert out == []


def test_fabricated_benchmark_figure_drops_story() -> None:
    ops = [_story(benchmark="Top 5 of 300 teams company-wide")]
    out = enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert out == []


def test_story_prose_is_not_token_grounded() -> None:
    # Paraphrase is legitimate: none of these exact words appear in the turn,
    # no figures involved -> passes.
    ops = [
        _story(
            title="Deployment stabilisation",
            challenge="Release process was unreliable",
            mechanism="Progressive delivery with automatic reversion",
            outcome="Release failures dropped substantially",
            benchmark=None,
        )
    ]
    out = enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert len(out) == 1


@pytest.mark.parametrize("source", ["interview", "agent_interview", "cv_upload"])
def test_denied_token_in_story_prose_drops_story(source: str) -> None:
    ops = [_story(mechanism="Introduced canary releases on Kubernetes")]
    out = enforce_stance(
        ops, denials=["Kubernetes"], new_info=_STORY_TURN, source=source
    )
    assert out == []


def test_figure_check_inert_without_corpus_denials_still_active() -> None:
    # cv_upload has no grounding corpus: figures pass (import-scope decision,
    # #127 parity), but denials still kill the op.
    ops = [_story(outcome="Improved uptime to 99.99%")]
    out = enforce_stance(ops, denials=[], new_info={"cv": "…"}, source="cv_upload")
    assert len(out) == 1


def test_figures_in_challenge_and_mechanism_not_checked() -> None:
    # Only outcome/benchmark feed Oracle number provenance; scene-setting
    # figures in challenge/mechanism stay paraphrasable.
    ops = [
        _story(
            challenge="Around 100 engineers were blocked weekly",
            mechanism="Rolled out in 3 phases",
            outcome="Failed deploys went from 20% to 2%",
            benchmark=None,
        )
    ]
    out = enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert len(out) == 1


def test_decimal_separator_normalised() -> None:
    turn = dict(_STORY_TURN, answer="We cut latency from 1,5 seconds to 300 ms.")
    ops = [_story(outcome="Cut latency from 1.5s to 300ms", benchmark=None)]
    out = enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


# ── Engine wiring: denials parsed defensively, guard applied in reconcile() ──


class _StubProvider:
    """Must absorb the full provider-ABC signature via **kwargs."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


@pytest.mark.asyncio
async def test_engine_parses_denials_and_strips_contradicting_ops() -> None:
    # The 21:56:31Z failure as it should now resolve: even if the model echoes
    # the denied token in its ops, its own denial verdict outranks them.
    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "add_bullets",
                    "target": "f0842567-2485-4a7f-a05d-f509998372a5",
                    "technologies": ["Large Language Models", "RAG"],
                }
            ],
            "ambiguities": [],
            "denials": ["RAG"],
        }
    )
    result = await reconcile(MasterProfileData(), _RAG_TURN, "interview", provider)
    assert result.denials == ["RAG"]
    assert len(result.ops) == 1
    assert result.ops[0].technologies == ["Large Language Models"]


@pytest.mark.asyncio
async def test_engine_defaults_and_garbage_denials() -> None:
    for denials in (None, "RAG", 42, ["ok", 7, None]):
        provider = _StubProvider({"ops": [], "ambiguities": [], "denials": denials})
        result = await reconcile(MasterProfileData(), "info", "manual", provider)
        assert isinstance(result, ReconcileResult)
        assert all(isinstance(d, str) for d in result.denials)


@pytest.mark.asyncio
async def test_engine_drops_ungrounded_interview_claims() -> None:
    # The 22:11:18Z fabrication as it should now resolve.
    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "upsert_skill",
                    "name": "Python",
                    "category": "technical",
                    "proficiency": "advanced",
                    "evidence": [],
                }
            ],
            "ambiguities": [],
        }
    )
    result = await reconcile(MasterProfileData(), _CHURN_TURN, "interview", provider)
    assert result.ops == []


def test_system_prompt_carries_stance_rule() -> None:
    # The prompt half of the two-layer fix: an explicit stance/denial rule and
    # the denials envelope key. (The mock fingerprint must survive the edit.)
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    lowered = RECONCILE_SYSTEM_PROMPT.lower()
    assert "profile reconciler" in lowered  # mock fingerprint (mock.py keys on it)
    assert "denial" in lowered
    assert '"denials"' in RECONCILE_SYSTEM_PROMPT
