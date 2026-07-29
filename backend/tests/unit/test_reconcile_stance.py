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
        "Ja: Bei NordPharm leite ich aktuell ein KI-Automatisierungsprojekt auf "
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
    "gap": "team_size: Associate Director Platform Systems @ NordPharm SE",
    "question": (
        "Can you describe a specific project where you implemented CI/CD "
        "pipelines and explain the tools and processes you used?"
    ),
    "answer": "We reduced churn by 3% through quarterly business reviews.",
}


# ── Denial strip (all sources): the model's own denial verdict wins ───────────


@pytest.mark.asyncio
async def test_denied_technology_stripped_from_add_bullets() -> None:
    # Exact 21:56:31Z op shape: the affirmed token survives, the denied one dies.
    ops = [
        AddBullets(
            target="f0842567-2485-4a7f-a05d-f509998372a5",
            technologies=["Large Language Models", "RAG"],
        )
    ]
    out = await enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    assert out[0].technologies == ["Large Language Models"]


@pytest.mark.asyncio
async def test_denied_skill_op_dropped_entirely() -> None:
    ops = [UpsertSkill(name="RAG", category="technical", proficiency="basic")]
    out = await enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert out == []


@pytest.mark.asyncio
async def test_bullet_text_claiming_denied_token_dropped() -> None:
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
    out = await enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    assert out[0].achievements == [
        "Automated regulatory document creation with LLMs"
    ]


@pytest.mark.asyncio
async def test_denial_matching_uses_shared_normalisation() -> None:
    # Case fold + containment both directions: "azure" denies "Microsoft Azure".
    ops = [
        UpsertSkill(name="Microsoft Azure"),
        AddBullets(target="w1", technologies=["AZURE", "AWS"]),
    ]
    out = await enforce_stance(
        ops,
        denials=["azure"],
        new_info={"gap": "cloud", "question": "Azure or AWS?", "answer": "AWS, not Azure"},
        source="interview",
    )
    assert len(out) == 1
    assert isinstance(out[0], AddBullets)
    assert out[0].technologies == ["AWS"]


@pytest.mark.asyncio
async def test_denial_strip_applies_to_cv_upload_source_too() -> None:
    ops = [UpsertSkill(name="Kubernetes")]
    out = await enforce_stance(
        ops, denials=["Kubernetes"], new_info="…full CV text…", source="cv_upload"
    )
    assert out == []


@pytest.mark.asyncio
async def test_add_bullets_left_empty_by_strip_is_dropped() -> None:
    ops = [AddBullets(target="w1", technologies=["RAG"])]
    out = await enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert out == []


# ── Interview grounding: token claims must appear in gap+question+answer ─────


@pytest.mark.asyncio
async def test_ungrounded_interview_skill_dropped() -> None:
    # Exact 22:11:18Z shape: Python appears nowhere in the turn -> fabrication.
    # ADR-061 clause 3: the guard stops deleting — with no provider on hand
    # for adjudication, the op survives as unconfirmed (never claimable)
    # rather than vanishing outright.
    ops = [UpsertSkill(name="Python", category="technical", proficiency="advanced")]
    out = await enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_skill_grounded_by_question_is_kept() -> None:
    # "Yes, six years, daily." affirms the question's token without repeating it.
    turn = {
        "gap": "Python experience",
        "question": "Do you have professional Python experience?",
        "answer": "Yes — six years, daily, in production.",
    }
    ops = [UpsertSkill(name="Python", category="technical")]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="interview")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_ungrounded_technology_stripped_from_interview_bullets() -> None:
    ops = [
        AddBullets(
            target="w1",
            technologies=["Databricks", "Terraform"],
            achievements=["Led an AI automation project"],
        )
    ]
    out = await enforce_stance(ops, denials=[], new_info=_RAG_TURN, source="interview")
    assert len(out) == 1
    # Databricks is in the answer; Terraform is not.
    assert out[0].technologies == ["Databricks"]
    # Free-text bullets are NOT grounding-checked (legitimate paraphrase).
    assert out[0].achievements == ["Led an AI automation project"]


@pytest.mark.asyncio
async def test_ungrounded_language_and_certification_dropped_on_interview() -> None:
    # ADR-061 clause 3: both survive as unconfirmed, not dropped.
    ops = [
        UpsertLanguage(language="French", level="B2"),
        UpsertCertification(name="AWS Solutions Architect"),
    ]
    out = await enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
    assert len(out) == 2
    assert all(op.status == "unconfirmed" for op in out)


@pytest.mark.asyncio
async def test_grounding_not_applied_to_non_interview_sources() -> None:
    # CV import reconciles a whole staged extraction; token-presence grounding is
    # an interview-turn instrument only (scope decision, #127).
    ops = [UpsertSkill(name="Python")]
    out = await enforce_stance(ops, denials=[], new_info={"cv": "…"}, source="cv_upload")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_entity_ops_are_not_grounding_checked() -> None:
    # Work/project/volunteer upserts legitimately echo profile knowledge
    # (target merges, alternate titles — rule 7); they stay out of scope.
    ops = [UpsertWork(ref="w1", company="NordPharm SE", role="Associate Director")]
    out = await enforce_stance(ops, denials=[], new_info=_CHURN_TURN, source="interview")
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


@pytest.mark.asyncio
async def test_agent_interview_token_absent_from_statement_dropped() -> None:
    # ADR-061 clause 3: survives as unconfirmed rather than vanishing.
    ops = [UpsertSkill(name="Terraform", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=_AGENT_TURN, source="agent_interview"
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_agent_interview_question_smuggle_dropped() -> None:
    # "Kubernetes" appears only in the agent-authored question/gap — on the
    # built-in interview this would ground; on the agent door it must NOT
    # (ADR-061 clause 3: unconfirmed rather than dropped, but still never
    # claimable — the smuggle attempt still fails).
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=_AGENT_TURN, source="agent_interview"
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"
    # Sanity: the identical turn grounds (confirmed) on the built-in interview.
    interview_out = await enforce_stance(
        ops, denials=[], new_info=_AGENT_TURN, source="interview"
    )
    assert len(interview_out) == 1
    assert interview_out[0].status == "confirmed"


@pytest.mark.asyncio
async def test_agent_interview_token_in_statement_kept() -> None:
    turn = dict(_AGENT_TURN, answer="Yes, I administered Kubernetes clusters.")
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_agent_interview_denials_still_enforced() -> None:
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    turn = dict(_AGENT_TURN, answer="I have used Kubernetes only in tutorials, no real experience.")
    out = await enforce_stance(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview"])
async def test_grounded_story_passes(source: str) -> None:
    out = await enforce_stance([_story()], denials=[], new_info=_STORY_TURN, source=source)
    assert len(out) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview"])
async def test_fabricated_outcome_figure_drops_story(source: str) -> None:
    # "99.9%" appears nowhere in the turn — the whole op dies (no silent
    # figure-stripping: a benchmark-less mutation of the model's story would
    # be editorializing; the claim reports no_change and the caller restates).
    ops = [_story(outcome="Achieved 99.9% deploy success")]
    out = await enforce_stance(ops, denials=[], new_info=_STORY_TURN, source=source)
    assert out == []


@pytest.mark.asyncio
async def test_fabricated_benchmark_figure_drops_story() -> None:
    ops = [_story(benchmark="Top 5 of 300 teams company-wide")]
    out = await enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert out == []


@pytest.mark.asyncio
async def test_story_prose_is_not_token_grounded() -> None:
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
    out = await enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert len(out) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview", "cv_upload"])
async def test_denied_token_in_story_prose_drops_story(source: str) -> None:
    ops = [_story(mechanism="Introduced canary releases on Kubernetes")]
    out = await enforce_stance(
        ops, denials=["Kubernetes"], new_info=_STORY_TURN, source=source
    )
    assert out == []


@pytest.mark.asyncio
async def test_figure_check_inert_without_corpus_denials_still_active() -> None:
    # cv_upload has no grounding corpus: figures pass (import-scope decision,
    # #127 parity), but denials still kill the op.
    ops = [_story(outcome="Improved uptime to 99.99%")]
    out = await enforce_stance(ops, denials=[], new_info={"cv": "…"}, source="cv_upload")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_figures_in_challenge_and_mechanism_not_checked() -> None:
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
    out = await enforce_stance(
        ops, denials=[], new_info=_STORY_TURN, source="agent_interview"
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_decimal_separator_normalised() -> None:
    turn = dict(_STORY_TURN, answer="We cut latency from 1,5 seconds to 300 ms.")
    ops = [_story(outcome="Cut latency from 1.5s to 300ms", benchmark=None)]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


# ── #207 over-drop bundle: the guard must not drop TRUTHFUL testimony ────────
#
# Three fail-closed matching gaps observed on real model output (all verbatim
# from the dev-stack LLM log, 2026-07-19): (1) a denial of "Tailwind CSS"
# also killed a legitimately affirmed sibling "CSS"; (2) the reconciler
# canonicalized the statement's "Postgres" to "PostgreSQL", which the grounding
# check then couldn't find in the statement; (3) spelled-out figures ("forty
# percent") didn't ground rendered numerals ("40%"). No truthfulness breach —
# but the agent believed it enriched the profile and it didn't.


# Verbatim interview turn, 2026-07-19T07:19:36Z (E046 adversarial pass): the
# model emitted upsert_skill CSS + denials ["Tailwind CSS"] from this answer.
_TAILWIND_TURN = {
    "gap": "Frontend and UI/UX",
    "question": (
        "Can you share any experience you’ve had with styling or UI/UX "
        "practices, even if it wasn’t your main focus?"
    ),
    "answer": (
        "To be honest, we never used Tailwind CSS at StartupXYZ - the design "
        "lead banned utility frameworks. When the marketing site's stylesheet "
        "grew unmaintainable, I solved it manually: I refactored the legacy "
        "styles into a small hand-rolled design-token system in plain CSS and "
        "removed dead rules. The site got noticeably faster and the team could "
        "finally ship UI changes without fear."
    ),
}


@pytest.mark.asyncio
async def test_affirmed_sibling_survives_denied_compound() -> None:
    # Exact 07:19:36Z op shape: "CSS" is affirmed in its own right ("in plain
    # CSS") while the compound "Tailwind CSS" is denied — the sibling lives.
    ops = [
        UpsertSkill(name="CSS", category="technical", proficiency="intermediate"),
        AddBullets(target="p1", technologies=["CSS"]),
    ]
    out = await enforce_stance(
        ops, denials=["Tailwind CSS"], new_info=_TAILWIND_TURN, source="interview"
    )
    assert len(out) == 2
    assert out[1].technologies == ["CSS"]


@pytest.mark.asyncio
async def test_sibling_without_independent_affirmation_still_denied() -> None:
    # The token appears ONLY inside the denied compound — that is not an
    # affirmation, and the guard stays fail-closed.
    turn = dict(
        _TAILWIND_TURN,
        answer=(
            "To be honest, we never used Tailwind CSS at StartupXYZ - the "
            "design lead banned utility frameworks."
        ),
    )
    ops = [UpsertSkill(name="CSS", category="technical")]
    out = await enforce_stance(
        ops, denials=["Tailwind CSS"], new_info=turn, source="interview"
    )
    assert out == []


@pytest.mark.asyncio
async def test_sibling_stays_fail_closed_without_corpus() -> None:
    # cv_upload has no grounding corpus to consult for independent affirmation,
    # so the strict-substring containment keeps its conservative verdict.
    ops = [UpsertSkill(name="CSS", category="technical")]
    out = await enforce_stance(
        ops, denials=["Tailwind CSS"], new_info="…full CV text…", source="cv_upload"
    )
    assert out == []


@pytest.mark.asyncio
async def test_exact_denial_outranks_affirmation() -> None:
    # A denial of the token ITSELF is never overridden by corpus presence —
    # the model's own denial verdict wins (ADR-040/#127, unchanged).
    ops = [UpsertSkill(name="CSS", category="technical")]
    out = await enforce_stance(
        ops, denials=["CSS"], new_info=_TAILWIND_TURN, source="interview"
    )
    assert out == []


# Verbatim agent_interview turn, 2026-07-19T11:18:30Z (E045 adversarial pass):
# the reconciler canonicalized the statement's "Postgres" to "PostgreSQL".
_POSTGRES_TURN = {
    "answer": (
        "I have deep Postgres tuning experience - query plans, index design, "
        "vacuum tuning - from the billing platform work."
    ),
    "question": "Which databases do you know best?",
}


@pytest.mark.asyncio
async def test_canonicalized_claim_grounded_by_statement_surface_form() -> None:
    # Exact 11:18:30Z op shape: the canonical form is grounded because the
    # statement carries a known surface alias ("Postgres").
    ops = [
        AddBullets(target="e1", technologies=["PostgreSQL"]),
        UpsertSkill(name="PostgreSQL", category="technical", proficiency="advanced"),
    ]
    out = await enforce_stance(
        ops, denials=[], new_info=_POSTGRES_TURN, source="agent_interview"
    )
    assert len(out) == 2
    assert out[0].technologies == ["PostgreSQL"]


@pytest.mark.asyncio
async def test_alias_does_not_ground_unrelated_token() -> None:
    # ADR-061 clause 3: unconfirmed (no provider on hand), never dropped.
    ops = [UpsertSkill(name="MySQL", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=_POSTGRES_TURN, source="agent_interview"
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_alias_grounding_is_word_boundary_aware() -> None:
    # "JSON" must not ground "JavaScript" via the "js" alias; a standalone
    # "JS" does.
    json_turn = {"answer": "I build JSON APIs all day.", "question": "Stack?"}
    js_turn = {"answer": "I write JS daily.", "question": "Stack?"}
    ops = [UpsertSkill(name="JavaScript", category="technical")]
    json_out = await enforce_stance(
        ops, denials=[], new_info=json_turn, source="agent_interview"
    )
    assert len(json_out) == 1
    assert json_out[0].status == "unconfirmed"  # ADR-061 clause 3
    js_out = await enforce_stance(ops, denials=[], new_info=js_turn, source="agent_interview")
    assert len(js_out) == 1
    assert js_out[0].status == "confirmed"


@pytest.mark.asyncio
async def test_denial_reaches_alias_forms() -> None:
    # The mirror-image hole: denying "Kubernetes" must also kill a "K8s" op —
    # same-skill-by-another-name works in the fail-closed direction too.
    turn = {
        "answer": "I've only toyed with k8s in tutorials, nothing serious.",
        "question": "Do you run Kubernetes in production?",
    }
    ops = [UpsertSkill(name="K8s", category="technical")]
    out = await enforce_stance(
        ops, denials=["Kubernetes"], new_info=turn, source="agent_interview"
    )
    assert out == []


# ── #231 regression (founder-acceptance adversarial pass, 2026-07-23) ────────
# Live scenario: a candidate denied "machine learning model training" while
# explicitly REAFFIRMING AI/ML integration work in the SAME statement. The
# denial-side match (_is_denied's bare substring containment check) let "ai"
# collide inside "tr-ai-ning", force-killing the unrelated, JD-required
# concept "AI/ML" — the exact collision class #207 excludes ml/ai from
# _ALIAS_GROUPS for.

_ML_TRAINING_DENIAL_TURN = {
    "gap": "AI/ML expertise",
    "question": "Tell us about your machine learning experience.",
    "answer": (
        "I haven't done machine learning model training myself, but I do "
        "have hands-on AI/ML integration experience — wiring LLM APIs into "
        "production services and building retrieval pipelines."
    ),
}


@pytest.mark.asyncio
async def test_ml_training_denial_does_not_suppress_reaffirmed_ai_ml() -> None:
    ops = [UpsertSkill(name="AI/ML", category="technical")]
    out = await enforce_stance(
        ops,
        denials=["machine learning model training"],
        new_info=_ML_TRAINING_DENIAL_TURN,
        source="agent_interview",
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_ml_training_denial_still_suppresses_machine_learning_itself() -> None:
    ops = [UpsertSkill(name="Machine Learning", category="technical")]
    out = await enforce_stance(
        ops,
        denials=["machine learning model training"],
        new_info=_ML_TRAINING_DENIAL_TURN,
        source="agent_interview",
    )
    assert out == []


# Verbatim interview turn, 2026-07-19T07:20:49Z (E046 adversarial pass): every
# figure is spelled out — a story op rendering them as numerals must survive.
_HELPDESK_TURN = {
    "gap": "Regulated Industry Software",
    "question": (
        "Have you ever worked on software projects that needed to meet strict "
        "compliance or regulatory standards, even if they weren’t in the "
        "pharmaceutical or GxP space?"
    ),
    "answer": (
        "Not in pharma, but audit-adjacent work, yes. The thing I'm proudest "
        "of at TechCorp: our helpdesk queue was a mess - around two thousand "
        "support requests every month were being assigned by hand and often "
        "ended up with the wrong team. I developed an automatic classifier in "
        "Python that assigns every incoming request to the correct team. "
        "Assignment latency fell from about four hours to twenty minutes, and "
        "wrong-team assignments dropped by forty percent."
    ),
}


def _helpdesk_story(**over: Any):
    from applire.services.profile.reconcile.ops import UpsertStory

    base = dict(
        title="Helpdesk auto-triage at TechCorp",
        challenge="Support requests were assigned by hand and often misrouted",
        mechanism="Built an automatic Python classifier for incoming requests",
        outcome="Cut wrong-team ticket assignments by 40%",
        benchmark=(
            "Assignment latency fell from 4 hours to 20 minutes across "
            "2,000 monthly requests"
        ),
        evidence=["w1"],
    )
    base.update(over)
    return UpsertStory(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["interview", "agent_interview"])
async def test_spelled_out_figures_ground_numerals(source: str) -> None:
    # "forty percent" grounds "40%", "twenty minutes" grounds "20 minutes",
    # "two thousand" grounds "2,000" (thousands separator included).
    out = await enforce_stance(
        [_helpdesk_story()], denials=[], new_info=_HELPDESK_TURN, source=source
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_spelled_out_german_figures_ground_numerals() -> None:
    # DACH testimony spells figures the German way: bare tens ("vierzig"),
    # unit+hundert compounds ("zweihundert"), unit+und+tens ("fünfundzwanzig").
    turn = {
        "answer": (
            "Wir haben die Fehlerquote um vierzig Prozent gesenkt - von rund "
            "zweihundert Tickets pro Monat auf etwa fünfundzwanzig."
        ),
        "question": "What did you improve?",
    }
    ops = [
        _helpdesk_story(
            outcome="Reduced the error rate by 40%",
            benchmark="From roughly 200 tickets per month down to 25",
        )
    ]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fabricated_figure_still_drops_story() -> None:
    # The guard's teeth stay in: "60%" is spelled nowhere in the turn.
    ops = [_helpdesk_story(outcome="Cut wrong-team ticket assignments by 60%")]
    out = await enforce_stance(
        ops, denials=[], new_info=_HELPDESK_TURN, source="interview"
    )
    assert out == []


@pytest.mark.asyncio
async def test_article_number_words_do_not_ground() -> None:
    # "ein"/"one" double as articles — parsing them would ground a fabricated
    # "1" from almost any sentence, so they stay excluded (fail-closed).
    for answer in (
        "Ich habe ein Projekt zur Automatisierung geleitet.",
        "In one of the projects I led, we automated the intake.",
    ):
        ops = [
            _helpdesk_story(
                outcome="Delivered 1 automation project", benchmark=None
            )
        ]
        out = await enforce_stance(
            ops,
            denials=[],
            new_info={"answer": answer, "question": "Projects?"},
            source="agent_interview",
        )
        assert out == []


# ── #207 adversarial-pass findings (real-LLM pass, 2026-07-19) ───────────────


@pytest.mark.asyncio
async def test_denial_order_does_not_reopen_sibling_exemption() -> None:
    # B1: blanking "Tailwind" before "Tailwind CSS" left an orphaned " css"
    # that read as an independent affirmation. The verdict must not depend on
    # the order the model lists its denials in.
    turn = {
        "answer": "We never used Tailwind CSS, the lead banned utility frameworks.",
        "question": "Styling experience?",
    }
    ops = [UpsertSkill(name="CSS", category="technical")]
    for denials in (["Tailwind", "Tailwind CSS"], ["Tailwind CSS", "Tailwind"]):
        out = await enforce_stance(
            ops, denials=denials, new_info=turn, source="agent_interview"
        )
        assert out == [], denials


@pytest.mark.asyncio
async def test_run_together_compound_does_not_affirm_sibling() -> None:
    # B2: "TailwindCSS" written as one word must not count as an independent
    # "CSS" affirmation when the model denies the spaced compound.
    turn = {
        "answer": "We never used TailwindCSS, the lead banned utility frameworks.",
        "question": "Styling experience?",
    }
    ops = [UpsertSkill(name="CSS", category="technical")]
    out = await enforce_stance(
        ops, denials=["Tailwind CSS"], new_info=turn, source="agent_interview"
    )
    assert out == []


@pytest.mark.asyncio
async def test_denied_alias_does_not_kill_substring_sibling() -> None:
    # O3: denying "JavaScript" must not kill an affirmed "JSON" op just
    # because the "js" alias is a substring of "json".
    turn = {"answer": "I build JSON APIs all day.", "question": "Stack?"}
    ops = [UpsertSkill(name="JSON", category="technical")]
    out = await enforce_stance(
        ops, denials=["JavaScript"], new_info=turn, source="agent_interview"
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_homonym_units_do_not_ground_expansions() -> None:
    # N1: "500 ml" (milliliters) must not ground a fabricated "Machine
    # Learning"; ditto "ai" (domains, French "ai") for "Artificial
    # Intelligence" — those alias pairs are excluded as homonym-risky.
    turn = {
        "answer": "I calibrated dosing pumps handling 500 ml per cycle.",
        "question": "Lab automation?",
    }
    ops = [UpsertSkill(name="Machine Learning", category="technical")]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    # ADR-061 clause 3: unconfirmed rather than dropped — never claimable
    # either way, so the homonym trap still cannot fabricate a claim.
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_multigroup_thousands_numeral_grounds() -> None:
    # O1: "a million events" grounds 1000000, and the numeral "1,000,000"
    # must tokenize as ONE figure, not "1,000" + "000".
    turn = {
        "answer": "At peak we process a million events per day.",
        "question": "Scale?",
    }
    ops = [
        _helpdesk_story(
            outcome="Handled 1,000,000 events per day", benchmark=None
        )
    ]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_composed_spelled_thousands_ground() -> None:
    # O2: "twenty-five thousand" composes to 25000 and grounds "25,000".
    turn = {
        "answer": "We grew the platform to about twenty-five thousand users.",
        "question": "Growth?",
    }
    ops = [_helpdesk_story(outcome="Grew the platform to 25,000 users", benchmark=None)]
    out = await enforce_stance(ops, denials=[], new_info=turn, source="agent_interview")
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
    # The 22:11:18Z fabrication as it should now resolve. `_StubProvider`
    # returns the SAME reconcile-shaped payload for the adjudication call
    # too, which is missing "answer"/"quote" — a malformed adjudication
    # response, so it falls back to unconfirmed (ADR-061 clause 2's
    # asymmetric failure handling), never to confirmed, and the op is no
    # longer dropped outright (clause 3).
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
    assert len(result.ops) == 1
    assert result.ops[0].status == "unconfirmed"


# ── #231 — durable denial persistence + the public ledger-reuse predicate ───


def test_record_denials_persists_and_writes_a_receipt_change() -> None:
    from datetime import datetime, timezone

    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    changes = record_denials(
        meta,
        ["Embeddings"],
        statement="No embeddings work.",
        source="agent_interview",
        when=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    assert len(changes) == 1
    assert changes[0].field == "denied_concepts"
    assert changes[0].action == "added"
    assert len(meta.denied_concepts) == 1
    assert meta.denied_concepts[0].concept == "Embeddings"
    assert meta.denied_concepts[0].statement == "No embeddings work."
    assert meta.denied_concepts[0].source == "agent_interview"
    assert meta.denied_concepts[0].date == "2026-07-23"


def test_record_denials_redenial_updates_in_place_case_insensitively() -> None:
    from datetime import datetime, timezone

    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    record_denials(
        meta, ["Embeddings"], statement="No embeddings work.",
        source="agent_interview", when=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    changes2 = record_denials(
        meta, ["embeddings"], statement="Confirmed: no embeddings work.",
        source="agent_interview", when=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    assert len(meta.denied_concepts) == 1, "re-denial must update, never duplicate"
    assert meta.denied_concepts[0].statement == "Confirmed: no embeddings work."
    assert meta.denied_concepts[0].date == "2026-07-24"
    assert len(changes2) == 1
    assert changes2[0].action == "updated"


def test_record_denials_empty_or_blank_is_a_noop() -> None:
    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    changes = record_denials(meta, ["", "   "], statement="x", source="interview")
    assert changes == []
    assert meta.denied_concepts == []


# ── ADR-064 — denial_level (direct/partial), no-downgrade invariant ─────────


def test_denied_concept_defaults_denial_level_direct_when_key_absent() -> None:
    """Back-compat: every DeniedConcept persisted before ADR-064 has no
    denial_level key in its JSONB row at all — it must load as "direct",
    never crash."""
    from applire.schemas.profile import DeniedConcept

    d = DeniedConcept(
        concept="Embeddings", statement="No embeddings work.",
        source="agent_interview", date="2026-07-23",
    )
    assert d.denial_level == "direct"


def test_record_denials_fresh_concept_writes_the_given_level() -> None:
    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    record_denials(
        meta, ["Embeddings"], statement="No embeddings, and no adjacent work either.",
        source="agent_interview", denial_level="partial",
    )
    assert meta.denied_concepts[0].denial_level == "partial"


def test_record_denials_never_downgrades_partial_to_direct() -> None:
    """No-downgrade invariant (ADR-064): a concept at "partial" re-denied at
    "direct" stays "partial" — a later, weaker probe must never erase that
    elicitation was already exhausted."""
    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    record_denials(
        meta, ["Embeddings"], statement="No embeddings, nor adjacent work.",
        source="agent_interview", denial_level="partial",
    )
    changes = record_denials(
        meta, ["Embeddings"], statement="No embeddings work.",
        source="agent_interview", denial_level="direct",
    )
    assert meta.denied_concepts[0].denial_level == "partial"
    # A re-denial at direct that changes nothing else (level not upgraded)
    # is still a legitimate refresh of statement/date, not necessarily a
    # level-change receipt — but it must never regress the level.
    assert len(meta.denied_concepts) == 1
    assert changes[0].action == "updated"


def test_record_denials_upgrades_direct_to_partial_and_receipt_records_it() -> None:
    """Upgrade: a concept at "direct" re-denied at "partial" becomes
    "partial", and the receipt records the level change (the trail shows the
    probe outcome)."""
    from applire.schemas.profile import ProfileMetadata
    from applire.services.profile.reconcile.stance import record_denials

    meta = ProfileMetadata()
    record_denials(
        meta, ["Embeddings"], statement="No embeddings work.",
        source="agent_interview", denial_level="direct",
    )
    changes = record_denials(
        meta, ["Embeddings"], statement="No embeddings, nor adjacent work either.",
        source="agent_interview", denial_level="partial",
    )
    assert meta.denied_concepts[0].denial_level == "partial"
    assert len(changes) == 1
    assert changes[0].action == "updated"
    assert "partial" in changes[0].rationale.lower() or "adjacent" in changes[0].rationale.lower()


def test_is_denied_concept_reuses_the_same_alias_and_boundary_machinery() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    assert is_denied_concept("Kubernetes", ["k8s"]) is True  # alias group (#207)
    assert is_denied_concept("K8s", ["Kubernetes"]) is True
    # concept-scoped, not topic-radius (#207 over-drop lesson): an unrelated
    # concept must survive a denial of something else entirely.
    assert is_denied_concept("RAG", ["embeddings"]) is False
    assert is_denied_concept("", ["embeddings"]) is False


# ── #231 regression (founder-acceptance adversarial pass, 2026-07-23) ───────
#
# A candidate denied "machine learning model training" while explicitly
# REAFFIRMING AI/ML integration work in the same statement. The denial-side
# match was a bare substring search (surface_present) with no word-boundary
# guard — the exact collision class #207 deliberately excludes ml/ai from
# _ALIAS_GROUPS for ("tr-AI-ning" contains "ai"). The grounding side
# (_word_present) was already guarded; the denial side ("token strictly
# inside the denied compound") was not. Both directions of the containment
# check share the same hazard and both must be boundary-safe.


def test_short_token_does_not_collide_inside_an_unrelated_word_in_the_denial() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # "AI" must NOT be considered denied just because "ai" appears embedded in
    # "training" — the live scenario: denying "machine learning model
    # training" while reaffirming AI/ML in the same breath.
    assert is_denied_concept("AI", ["machine learning model training"]) is False
    assert is_denied_concept("ML", ["machine learning model training"]) is False
    assert is_denied_concept("AI/ML", ["machine learning model training"]) is False


def test_denial_still_suppresses_the_concept_it_actually_names_as_a_whole_word() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # The legitimate #231 behavior stays: "Machine learning" IS a whole-word
    # substring of "machine learning model training" and must still be denied.
    assert is_denied_concept("Machine learning", ["machine learning model training"]) is True


def test_whole_word_denial_of_ai_still_suppresses_ai_ml_concepts() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # A denial that names "AI" as a genuine whole word ("I have no AI
    # experience") must still reach AI/ML concepts — only the ambiguous
    # embedded-substring collision is excluded, not legitimate whole-word hits.
    assert is_denied_concept("AI/ML", ["AI"]) is True
    assert is_denied_concept("AI", ["I have no AI experience"]) is True


def test_denial_word_boundary_reverse_direction_also_guarded() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # Same hazard, opposite direction: a short single-word denial ("ai") must
    # not collide with a substring embedded inside an unrelated concept name
    # ("Maintenance" contains "ai" but not as a whole word).
    assert is_denied_concept("Maintenance", ["ai"]) is False
    assert is_denied_concept("AI Governance", ["ai"]) is True  # whole word "ai"


def test_hyphenated_and_slashed_forms_stay_word_boundary_safe() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # Hyphens fold to spaces before matching; slashes are already boundaries
    # (not in [a-z0-9]) — both must behave as genuine word edges.
    assert is_denied_concept("AI-driven", ["AI"]) is True
    assert is_denied_concept("AI/ML", ["AI"]) is True
    assert is_denied_concept("Training", ["AI"]) is False


def test_embeddings_word_boundary_denial_unchanged() -> None:
    from applire.services.profile.reconcile.stance import is_denied_concept

    # #231's legitimate behavior, pinned again at the is_denied_concept level:
    # denying "embeddings" still suppresses "Embeddings" (word-boundary hit).
    assert is_denied_concept("Embeddings", ["embeddings"]) is True


def test_system_prompt_carries_stance_rule() -> None:
    # The prompt half of the two-layer fix: an explicit stance/denial rule and
    # the denials envelope key. (The mock fingerprint must survive the edit.)
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    lowered = RECONCILE_SYSTEM_PROMPT.lower()
    assert "profile reconciler" in lowered  # mock fingerprint (mock.py keys on it)
    assert "denial" in lowered
    assert '"denials"' in RECONCILE_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_bullet_with_substring_collision_word_is_not_dropped() -> None:
    # Adversarial-pass follow-up (2026-07-23): _text_claims_denied shared the
    # bare-substring hazard — a denial of "AI" must not drop a truthful bullet
    # whose only "match" is the substring inside "training"/"maintenance".
    ops = [
        AddBullets(
            target="w1",
            achievements=[
                "Led maintenance and training programs for the QC team",
                "Built AI pipelines for document classification",
            ],
        )
    ]
    turn = {
        "gap": "AI experience",
        "question": "Do you have AI experience?",
        "answer": "I have not built AI systems myself; I led maintenance and training programs for the QC team.",
    }
    out = await enforce_stance(ops, denials=["AI"], new_info=turn, source="interview")
    assert len(out) == 1
    # The whole-word AI bullet dies; the training/maintenance bullet survives.
    assert out[0].achievements == [
        "Led maintenance and training programs for the QC team"
    ]


# ═══════════════════════════════════════════════════════════════════════════
# ADR-061 (#316, closes #305) — the testimony predicate, LLM adjudication with
# a deterministically verified citation, and `unconfirmed` as a third state.
# ═══════════════════════════════════════════════════════════════════════════


class _AdjudicationProvider:
    """Scripted test double for the ADR-061 clause 2 adjudication call ONLY —
    ``enforce_stance`` never calls ``aparse_json`` for anything else. Each
    call pops the next scripted item; an ``Exception`` instance is raised
    instead of returned, so provider-outage/timeout paths reuse the same
    script mechanism as the (mal)formed-response paths."""

    def __init__(self, *script: Any) -> None:
        self._script = list(script)
        self.calls: list[str] = []

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append(prompt)
        assert self._script, "adjudication called more times than scripted"
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


# ── Clause 1: surface_present is byte-for-byte unchanged ────────────────────
# This is the invariant clause 1 exists to protect — the coverage predicate
# the ATS ring, ledger presence, and ADR-051 §3 retention scoring all share
# must never be quietly made morphology-aware by this change. Source-hash
# pinned so a future edit to this file cannot loosen it without this test
# failing loudly (a plain "it still imports" check would not catch a body
# edit).


def test_surface_present_byte_for_byte_unchanged() -> None:
    import hashlib
    import inspect

    from applire.services.ats_audit import surface_present

    src = inspect.getsource(surface_present)
    assert (
        hashlib.sha256(src.encode()).hexdigest()
        == "d7a9d039c67007d6fb069922566d909f1ba1286ad0da7092bcb14a6c0f79ff14"
    ), (
        "surface_present's body changed — ADR-061 clause 1 requires it stay "
        "byte-for-byte unchanged (it stays the sole coverage predicate for "
        "the ATS ring / ledger presence / ADR-051 §3 retention scoring); the "
        "testimony predicate is a SEPARATE, explicitly-named instrument."
    )


# ── Clause 2: the three #305 morphological misses, verbatim from ground     ─
# truth (backend/logs/llm/2026-07-27.jsonl, the operations_marcus_de run) —
# the exact turn text and op shape the reconciler LLM actually produced.


@pytest.mark.asyncio
async def test_german_qualifier_prefix_confirmed_via_adjudication() -> None:
    # "PP" scoped by an earlier "SAP-Rollout" clause; op says "SAP PP" — dies
    # on the deterministic check (no contiguous "SAP PP" substring), rescued
    # by adjudication with a verified citation.
    turn = {
        "gap": "Digital Transformation",
        "question": (
            "Können Sie uns Beispiele nennen, in denen Sie digitale "
            "Initiativen vorangetrieben haben?"
        ),
        "answer": (
            "Beim SAP-Rollout bei Rasselstein war ich Key-User für PP; bei "
            "Weberit arbeite ich täglich mit PP und MM und habe die "
            "Stammdatenbereinigung für die Arbeitspläne geleitet."
        ),
    }
    provider = _AdjudicationProvider(
        {"answer": "yes", "quote": "Key-User für PP"},
    )
    ops = [UpsertSkill(name="SAP PP", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "confirmed"
    assert len(provider.calls) == 1  # deterministic accept never reaches here for SAP/PP alone


@pytest.mark.asyncio
async def test_german_parenthetical_gloss_confirmed_via_adjudication() -> None:
    # Turn says bare "OEE"; op glosses it out to "OEE (Overall Equipment
    # Effectiveness)" — the full phrase never appears literally.
    turn = {
        "gap": "Manufacturing Execution Systems",
        "question": "Haben Sie bereits mit MES gearbeitet?",
        "answer": (
            "Ergebnis war OEE-Transparenz in Echtzeit direkt am "
            "Shopfloor-Board; die OEE im Spritzguss ist in 18 Monaten von "
            "61 % auf 73 % gestiegen."
        ),
    }
    provider = _AdjudicationProvider(
        {"answer": "yes", "quote": "die OEE im Spritzguss"},
    )
    ops = [UpsertSkill(name="OEE (Overall Equipment Effectiveness)", category="domain")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "confirmed"


@pytest.mark.asyncio
async def test_german_compound_tail_confirmed_via_adjudication() -> None:
    # Turn writes the compound "Sauberraumbereich"; op says "Sauberraum-
    # Management" — no literal substring match either direction.
    turn = {
        "gap": "Packaging Industry",
        "question": "Haben Sie Berührungspunkte mit Verpackungsprozessen gehabt?",
        "answer": (
            "Bei Weberit fertigen wir unter anderem Kosmetik-Verpackungen; "
            "dafür haben wir seit 2021 einen Sauberraumbereich, den ich "
            "verantworte."
        ),
    }
    provider = _AdjudicationProvider(
        {"answer": "yes", "quote": "einen Sauberraumbereich, den ich verantworte"},
    )
    ops = [UpsertSkill(name="Sauberraum-Management", category="domain")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "confirmed"


# ── Clause 2: the citation check as a CONTROL, not a detail ─────────────────


@pytest.mark.asyncio
async def test_adjudication_fabricated_quote_rejected() -> None:
    """ADVERSARIAL (mandatory per #316's acceptance criteria): the model
    answers "yes" with a plausible-but-fabricated quote — text that reads
    like something the candidate could have said, but is not literally in
    the turn. The deterministic citation check must reject it regardless of
    how confident the model sounds; #306, from the same run, is this
    codebase's own live evidence that an LLM in a control path can go wrong
    this way."""
    turn = dict(_CHURN_TURN)  # gap/question never mention "Selenium" literally
    provider = _AdjudicationProvider(
        {
            "answer": "yes",
            # Plausible, well-formed, and NOT a substring of the turn above.
            "quote": "I have run our Selenium test suite in production for years.",
        },
    )
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_near_miss_quote_rejected() -> None:
    # A quote that is almost right (paraphrased, not copied) must still fail
    # the LITERAL substring check — "close" is not "verified".
    turn = {"answer": "I led the rollout of PP at Rasselstein.", "question": "SAP?"}
    provider = _AdjudicationProvider(
        {"answer": "yes", "quote": "I led the PP rollout at Rasselstein"},  # reordered
    )
    ops = [UpsertSkill(name="SAP PP", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_yes_with_empty_quote_rejected() -> None:
    turn = {"answer": "I mostly did QA testing this quarter.", "question": "Skills?"}
    provider = _AdjudicationProvider({"answer": "yes", "quote": ""})
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_no_answer_never_confirmed() -> None:
    # The model correctly reads the turn as NOT affirming the token — still
    # unconfirmed, never a silent drop, even with a perfectly valid citation.
    # Neither field names the token literally, so the deterministic accept
    # path genuinely misses and adjudication is actually reached.
    turn = {"answer": "I have no experience in that particular area.", "question": "Which languages do you know?"}
    provider = _AdjudicationProvider(
        {"answer": "no", "quote": "I have no experience in that particular area."},
    )
    ops = [UpsertSkill(name="Rust", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_unclear_answer_never_confirmed() -> None:
    turn = {"answer": "Maybe, I'd have to check.", "question": "Which languages do you know?"}
    provider = _AdjudicationProvider(
        {"answer": "unclear", "quote": ""},
    )
    ops = [UpsertSkill(name="Rust", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


# ── Clause 2: failure handling is ASYMMETRIC — every path lands unconfirmed,
# never confirmed. Each path asserted SEPARATELY (#316 requirement).


@pytest.mark.asyncio
async def test_adjudication_provider_outage_never_confirmed() -> None:
    from applire.exceptions import LLMProviderUnavailableError

    turn = {"answer": "I mostly did QA testing.", "question": "Skills?"}
    provider = _AdjudicationProvider(LLMProviderUnavailableError("provider down"))
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_timeout_never_confirmed() -> None:
    from applire.exceptions import LLMTimeoutError

    turn = {"answer": "I mostly did QA testing.", "question": "Skills?"}
    provider = _AdjudicationProvider(LLMTimeoutError("timed out"))
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_non_dict_response_never_confirmed() -> None:
    # "Malformed JSON" as the caller sees it: aparse_json's own JSON-repair
    # gave up and returned something that isn't a dict at all.
    turn = {"answer": "I mostly did QA testing.", "question": "Skills?"}
    provider = _AdjudicationProvider("not a dict")
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_missing_keys_never_confirmed() -> None:
    turn = {"answer": "I mostly did QA testing.", "question": "Skills?"}
    provider = _AdjudicationProvider({"unexpected": "shape"})
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


@pytest.mark.asyncio
async def test_adjudication_invalid_answer_enum_never_confirmed() -> None:
    turn = {"answer": "I mostly did QA testing.", "question": "Skills?"}
    provider = _AdjudicationProvider({"answer": "maybe", "quote": "QA testing"})
    ops = [UpsertSkill(name="Selenium", category="technical")]
    out = await enforce_stance(
        ops, denials=[], new_info=turn, source="interview", provider=provider,
    )
    assert len(out) == 1
    assert out[0].status == "unconfirmed"


# ── Clause 3: denial still outranks — adjudication is never even attempted ─


@pytest.mark.asyncio
async def test_denial_short_circuits_before_any_adjudication_call() -> None:
    turn = {
        "answer": "I have never used Kubernetes, only read about it.",
        "question": "Kubernetes?",
    }
    # Empty script: if the guard mistakenly called aparse_json, the stub's
    # own assertion would fail the test before this one gets a chance to.
    provider = _AdjudicationProvider()
    ops = [UpsertSkill(name="Kubernetes", category="technical")]
    out = await enforce_stance(
        ops, denials=["Kubernetes"], new_info=turn, source="interview", provider=provider,
    )
    assert out == []
    assert provider.calls == []


# ── Clause 3: unconfirmed is never claimable at the concrete consumers ──────


def test_apply_upsert_skill_creates_unconfirmed_entity() -> None:
    from applire.services.profile.reconcile.apply import apply_ops

    profile = MasterProfileData()
    result = apply_ops(
        profile,
        [UpsertSkill(name="SAP PP", category="technical", status="unconfirmed")],
        "interview",
    )
    assert len(result.profile.skills) == 1
    assert result.profile.skills[0].status == "unconfirmed"


def test_apply_upsert_skill_merge_never_downgrades_confirmed() -> None:
    from applire.services.profile.reconcile.apply import apply_ops

    profile = MasterProfileData()
    first = apply_ops(
        profile, [UpsertSkill(name="Python", category="technical", status="confirmed")],
        "interview",
    )
    second = apply_ops(
        first.profile,
        [UpsertSkill(name="Python", category="technical", status="unconfirmed")],
        "interview",
    )
    assert len(second.profile.skills) == 1
    assert second.profile.skills[0].status == "confirmed"


def test_apply_upsert_skill_merge_promotes_unconfirmed_to_confirmed() -> None:
    from applire.services.profile.reconcile.apply import apply_ops

    profile = MasterProfileData()
    first = apply_ops(
        profile, [UpsertSkill(name="Python", category="technical", status="unconfirmed")],
        "interview",
    )
    assert first.profile.skills[0].status == "unconfirmed"
    second = apply_ops(
        first.profile,
        [UpsertSkill(name="Python", category="technical", status="confirmed")],
        "interview",
    )
    assert second.profile.skills[0].status == "confirmed"


def test_unconfirmed_certification_excluded_from_cv_passthrough() -> None:
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _apply_certifications

    profile_json = {
        "certifications": [
            {"name": "AWS Solutions Architect", "status": "unconfirmed"},
            {"name": "PMP", "status": "confirmed"},
        ]
    }
    tailored = TailoredCVData(contact={"name": "Test Candidate"})
    out = _apply_certifications(tailored, profile_json)
    names = [c.name for c in out.certifications]
    assert names == ["PMP"]


def test_unconfirmed_skill_excluded_from_jd_guarantee_restoration() -> None:
    # A JD-required skill that only exists unconfirmed in the vault must NOT
    # be guarantee-restored into the CV skills section (ADR-061 clause 3).
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _tailor_skills_to_jd

    tailored = TailoredCVData(contact={"name": "Test Candidate"}, skills=["Communication"])
    profile_json = {"skills": [{"name": "SAP PP", "status": "unconfirmed"}]}
    job_dict = {
        "role_title": "SAP Consultant",
        "required_skills": ["SAP PP"],
        "nice_to_have_skills": [],
        "keywords": [],
    }
    out = _tailor_skills_to_jd(tailored, profile_json, job_dict, keyword_ledger=None)
    assert "SAP PP" not in (out.skills or [])


def test_exclude_unconfirmed_strips_only_the_three_entity_lists() -> None:
    from applire.services.profile.reconcile.stance import exclude_unconfirmed

    profile_json = {
        "skills": [
            {"name": "SAP PP", "status": "unconfirmed"},
            {"name": "Python", "status": "confirmed"},
        ],
        "languages": [{"language": "French", "status": "unconfirmed"}],
        "certifications": [{"name": "PMP", "status": "confirmed"}],
        "work_experience": [{"company": "Acme"}],  # untouched
    }
    out = exclude_unconfirmed(profile_json)
    assert [s["name"] for s in out["skills"]] == ["Python"]
    assert out["languages"] == []
    assert [c["name"] for c in out["certifications"]] == ["PMP"]
    assert out["work_experience"] == [{"company": "Acme"}]  # untouched
    # Never mutates the input.
    assert len(profile_json["skills"]) == 2


def test_exclude_unconfirmed_tolerates_none_and_non_dict() -> None:
    from applire.services.profile.reconcile.stance import exclude_unconfirmed

    assert exclude_unconfirmed(None) == {}
    assert exclude_unconfirmed({}) == {}


# ── Clause 8: every drop/downgrade logs the turn text ────────────────────────


@pytest.mark.asyncio
async def test_unconfirmed_downgrade_logs_the_turn_text(caplog: Any) -> None:
    turn = {"answer": "We reduced churn by 3% through quarterly reviews.", "question": "CI/CD?"}
    ops = [UpsertSkill(name="Python", category="technical")]
    with caplog.at_level("WARNING", logger="applire.services.profile.reconcile.stance"):
        out = await enforce_stance(ops, denials=[], new_info=turn, source="interview")
    assert len(out) == 1
    assert out[0].status == "unconfirmed"
    assert any("churn by 3%" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_denial_drop_logs_the_turn_text(caplog: Any) -> None:
    ops = [UpsertSkill(name="RAG", category="technical")]
    with caplog.at_level("WARNING", logger="applire.services.profile.reconcile.stance"):
        out = await enforce_stance(ops, denials=["RAG"], new_info=_RAG_TURN, source="interview")
    assert out == []
    # Near the START of the joined gap+question+answer corpus (the log line
    # is bounded — see _LOG_TURN_MAX — so assert on text that survives it).
    assert any("AI, LLMs & RAG Systems" in rec.message for rec in caplog.records)
