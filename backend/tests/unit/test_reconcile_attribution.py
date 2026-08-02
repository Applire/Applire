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

"""#243 — deterministic employer-attribution guard for the ADR-046 reconciler.

Ground truth (live-reproduced 2026-07-24, founder charter re-run, main @
53ffa85): a single multi-employer interview answer —

    "At NordPharm I led an agentic GenAI system that automated
    regulatory submission documentation... For production-grade rigor
    specifically, my clearest example is Applire: I built a deterministic
    verification layer, the Truthfulness Oracle, that audits every LLM output
    against the source data, backed by full LLM exchange observability
    logging and over 2,600 tests gating CI, so failures are caught before
    they reach a user."

— was reconciled by the MODEL (logs/llm/2026-07-24.jsonl, line 467; the exact
add_bullets ops the mock provider below reproduces) into FOUR add_bullets ops,
two of which wrongly targeted NordPharm entities with Applire-only clauses:

* target=NordPharm's "Associate Director Platform..." role, achievements=["Built
  deterministic verification layer (Truthfulness Oracle) auditing every LLM
  output against source data"] — WRONG, this is the Applire clause.
* target=NordPharm's "Agentic GenAI System for Regulatory Submission
  Documentation" project, achievements=["Backed by full LLM exchange
  observability logging and over 2,600 tests gating CI"] — WRONG (the
  reported #243 vault mutation).

Both bullets are prompt-side (model-emitted) misattributions: the applier
(apply.py) faithfully applied exactly the ops the model handed it, and the
model itself put the Applire clause's content on a NordPharm target. This is a
belt-and-braces deterministic guard in the applier layer, mirroring
``services.oracle.extract``'s employer-anchoring pattern (kept as an
independent copy, not a cross-package import — the reconcile layer is
foundational to the profile write path, the oracle package depends on it, not
the other way around).
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import MasterProfileData, ProjectEntry, WorkEntry
from applire.services.profile.reconcile.attribution import (
    _split_sentences,
    enforce_attribution,
)
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import (
    AddBullets,
    RequestConfirmation,
)

APPLIRE_ID = "29824d5a-c97f-47fc-9efc-84439244a34a"
NORDPHARM_ROLE_ID = "aafa340a-bab3-48af-8770-542fba56c5de"
NORDPHARM_PROJECT_ID = "ca10ed5e-5acc-4171-b728-5c04517ff6ea"

# The verbatim live turn (logs/llm/2026-07-24.jsonl denied_concepts entry).
_LIVE_ANSWER = (
    "At NordPharm I led an agentic GenAI system that automated "
    "regulatory submission documentation, built with LangGraph and "
    "LangChain plus RAG over our reference docs and SOPs, running on "
    "Databricks — targeting roughly a 70% reduction in manual "
    "authoring/review effort. My contribution there was the architecture, "
    "database design, and product ownership; our system engineer handled "
    "the actual embedding and vector-store configuration, that's an honest "
    "limit on my hands-on side. For production-grade rigor specifically, my "
    "clearest example is Applire: I built a deterministic verification "
    "layer, the Truthfulness Oracle, that audits every LLM output against "
    "the source data, backed by full LLM exchange observability logging and "
    "over 2,600 tests gating CI, so failures are caught before they reach a "
    "user."
)


def _live_profile() -> MasterProfileData:
    """The profile shape from the live incident (trimmed to what the guard needs)."""
    return MasterProfileData(
        work_experience=[
            WorkEntry(id=NORDPHARM_ROLE_ID, company="NordPharm SE",
                      role="Associate Director Platform Systems"),
            WorkEntry(id=APPLIRE_ID, company="Applire", role="Founder & Lead Developer"),
        ],
        projects=[
            ProjectEntry(
                id=NORDPHARM_PROJECT_ID,
                name="Agentic GenAI System for Regulatory Submission Documentation",
                associated_experience=NORDPHARM_ROLE_ID,
            ),
        ],
    )


class _StubProvider:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


# The model's ACTUAL emitted ops (logs/llm/2026-07-24.jsonl line 467), trimmed
# to the two misattributed add_bullets — the reproduction case.
_MODEL_OPS = {
    "ops": [
        {
            "op": "add_bullets",
            "target": NORDPHARM_ROLE_ID,
            "achievements": [
                "Built deterministic verification layer (Truthfulness Oracle) "
                "auditing every LLM output against source data"
            ],
        },
        {
            "op": "add_bullets",
            "target": APPLIRE_ID,
            "responsibilities": [
                "Built deterministic verification layer (Truthfulness Oracle) "
                "auditing every LLM output against source data"
            ],
        },
        {
            "op": "add_bullets",
            "target": NORDPHARM_PROJECT_ID,
            "achievements": [
                "Backed by full LLM exchange observability logging and over "
                "2,600 tests gating CI"
            ],
        },
    ],
    "ambiguities": [],
    "denials": [],
}


@pytest.mark.asyncio
async def test_live_incident_misattributed_ops_route_to_pending_confirmation() -> None:
    """Regression #243: both NordPharm-targeted Applire clauses are guarded."""
    profile = _live_profile()
    provider = _StubProvider(_MODEL_OPS)
    new_info = {"answer": _LIVE_ANSWER}

    result = await reconcile(profile, new_info, "agent_interview", provider)

    add_bullets_ops = [op for op in result.ops if isinstance(op, AddBullets)]
    confirmations = [op for op in result.ops if isinstance(op, RequestConfirmation)]

    # The NordPharm-targeted ops carrying the Applire clause must NOT survive
    # as an add_bullets op that would silently write it onto NordPharm.
    for op in add_bullets_ops:
        if op.target == NORDPHARM_ROLE_ID:
            assert "Built deterministic verification layer" not in " ".join(
                op.achievements + op.responsibilities
            )
        if op.target == NORDPHARM_PROJECT_ID:
            assert "2,600 tests" not in " ".join(op.achievements + op.responsibilities)

    # The correctly-targeted Applire op must survive untouched.
    applire_ops = [op for op in add_bullets_ops if op.target == APPLIRE_ID]
    assert len(applire_ops) == 1
    assert "Built deterministic verification layer" in applire_ops[0].responsibilities[0]

    # Both misattributions must have been routed to a pending confirmation —
    # NOT silently applied, NOT silently dropped.
    assert len(confirmations) == 2
    flagged_texts = " ".join(c.question for c in confirmations)
    assert "Applire" in flagged_texts
    assert "NordPharm" in flagged_texts


@pytest.mark.asyncio
async def test_correctly_targeted_ops_are_untouched() -> None:
    """The Applire-targeted op (correct attribution) must apply normally —
    over-drop discipline: the guard must never touch a correctly-anchored op."""
    profile = _live_profile()
    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "add_bullets",
                    "target": APPLIRE_ID,
                    "achievements": [
                        "Backed by full LLM exchange observability logging "
                        "and over 2,600 tests gating CI"
                    ],
                },
            ],
            "ambiguities": [],
            "denials": [],
        }
    )
    result = await reconcile(profile, {"answer": _LIVE_ANSWER}, "agent_interview", provider)

    assert len(result.ops) == 1
    op = result.ops[0]
    assert isinstance(op, AddBullets)
    assert op.target == APPLIRE_ID
    assert "2,600 tests" in op.achievements[0]


class TestEnforceAttributionUnit:
    """Direct unit tests of ``enforce_attribution`` (no LLM call in the loop)."""

    def test_no_anchor_bullet_keeps_default_behaviour(self) -> None:
        """Over-drop negative test: a bullet naming NO employer, merged into the
        contextual (ambient) target the interview turn was about, must apply
        unchanged — this is the normal, legitimate enrichment case."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_ROLE_ID,
                responsibilities=["Mentored two junior engineers on CI/CD practices."],
            )
        ]
        answer = (
            "I mentored two junior engineers on CI/CD practices and improved "
            "our pipeline reliability."
        )
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": answer}, source="agent_interview"
        )
        assert result == ops

    def test_ambiguous_two_employers_in_one_clause_fails_open(self) -> None:
        """Two employers named in the SAME clause → ambiguous anchor → fail
        open (documented design choice, #243): the op applies unmodified
        rather than guessing which employer the guard should defer to."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_ROLE_ID,
                achievements=["Championed rigorous automated testing as a core practice."],
            )
        ]
        answer = (
            "At both NordPharm and Applire, I championed rigorous automated "
            "testing as a core practice."
        )
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": answer}, source="agent_interview"
        )
        assert result == ops

    def test_legal_form_variant_nordpharm_se_vs_nordpharm(self) -> None:
        """'NordPharm SE' (profile) vs 'NordPharm' (spoken form) must still
        anchor and mismatch correctly."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_PROJECT_ID,
                achievements=[
                    "Backed by full LLM exchange observability logging and "
                    "over 2,600 tests gating CI"
                ],
            )
        ]
        answer = (
            "For production-grade rigor specifically, my clearest example is "
            "Applire: I built a deterministic verification layer, the "
            "Truthfulness Oracle, that audits every LLM output against the "
            "source data, backed by full LLM exchange observability logging "
            "and over 2,600 tests gating CI, so failures are caught before "
            "they reach a user."
        )
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": answer}, source="agent_interview"
        )
        confirmations = [op for op in result if isinstance(op, RequestConfirmation)]
        assert len(confirmations) == 1
        add_bullets = [op for op in result if isinstance(op, AddBullets)]
        assert add_bullets == []  # nothing left to keep on this op

    def test_de_bei_phrasing(self) -> None:
        """German 'bei X' phrasing must still anchor via plain substring
        containment (no special-casing needed — 'bei' is not part of the
        company name pattern)."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_PROJECT_ID,
                achievements=["Backed by full LLM exchange observability logging."],
            )
        ]
        answer = (
            "Mein klarstes Beispiel dafür ist bei Applire entstanden: Ich habe "
            "eine deterministische Verifikationsschicht gebaut, backed by full "
            "LLM exchange observability logging."
        )
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": answer}, source="agent_interview"
        )
        confirmations = [op for op in result if isinstance(op, RequestConfirmation)]
        assert len(confirmations) == 1

    def test_unicode_punctuation_does_not_defeat_matching(self) -> None:
        """A curly apostrophe/dash near the company name must not break the
        word-boundary anchor match (the U+2019 lesson, 2026-07-11)."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_PROJECT_ID,
                achievements=["Backed by full LLM exchange observability logging."],
            )
        ]
        answer = (
            "Applire’s clearest example is the layer I built: backed by "
            "full LLM exchange observability logging."
        )
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": answer}, source="agent_interview"
        )
        confirmations = [op for op in result if isinstance(op, RequestConfirmation)]
        assert len(confirmations) == 1

    def test_cv_upload_source_never_guarded(self) -> None:
        """The grounding corpus is an interview-turn instrument only (mirrors
        stance.py's existing scoping) — a bulk CV import never runs this
        guard (#243 scope decision, consistent with #127)."""
        profile = _live_profile()
        ops = [
            AddBullets(
                target=NORDPHARM_ROLE_ID,
                achievements=[
                    "Backed by full LLM exchange observability logging and "
                    "over 2,600 tests gating CI"
                ],
            )
        ]
        result = enforce_attribution(
            ops,
            profile=profile,
            new_info={"answer": "irrelevant — Applire only, never checked"},
            source="cv_upload",
        )
        assert result == ops

    def test_standalone_project_no_employer_context_never_guarded(self) -> None:
        """A standalone project (no ``associated_experience``) has no employer
        context to guard against — fails open."""
        profile = _live_profile()
        profile.projects.append(
            ProjectEntry(id="standalone-1", name="Open Source Side Project")
        )
        ops = [
            AddBullets(
                target="standalone-1",
                achievements=[
                    "Backed by full LLM exchange observability logging and "
                    "over 2,600 tests gating CI"
                ],
            )
        ]
        result = enforce_attribution(
            ops, profile=profile, new_info={"answer": _LIVE_ANSWER}, source="agent_interview"
        )
        assert result == ops


class TestSplitSentencesDegreeAbbreviations:
    """#416: the reconcile-side independent copy of the sentence splitter
    carried the same gap as ``services.oracle.extract.split_sentences`` —
    no academic degree abbreviations in ``_ABBREVIATIONS``, so a testimony
    answer naming a degree mid-sentence split into unverifiable fragments
    before it ever reached the attribution/grounding corpus."""

    def test_msc_mid_sentence_stays_one_sentence(self) -> None:
        text = (
            "Mit meinem M.Sc. Betriebswirtschaftslehre und neun Jahren "
            "Erfahrung bringe ich fundiertes Fachwissen mit."
        )
        assert _split_sentences(text) == [text]

    def test_two_sentence_text_with_ba_still_splits_in_two(self) -> None:
        text = (
            "Ich leitete das Projekt erfolgreich. Mein B.A. in Wirtschaft "
            "half mir dabei sehr."
        )
        assert _split_sentences(text) == [
            "Ich leitete das Projekt erfolgreich.",
            "Mein B.A. in Wirtschaft half mir dabei sehr.",
        ]

    def test_dr_rer_pol_survives_ordering_hazard(self) -> None:
        """#416 ordering subtlety: "Dr." is already protected and is a
        prefix of "Dr. rer. pol." — if the shorter member runs first in the
        sequential-replace loop it partially sentinel-fies the longer one
        and destroys the match. Protection must apply longest-first."""
        text = (
            "Sie promovierte als Dr. rer. pol. an der Universität und "
            "forschte weiter."
        )
        assert _split_sentences(text) == [text]

    def test_dipl_ing_mid_sentence_stays_one_sentence(self) -> None:
        text = "Er schloss sein Dipl.-Ing. Studium mit Auszeichnung ab."
        assert _split_sentences(text) == [text]

    def test_mba_survives_ba_substring_ordering_hazard(self) -> None:
        """#416 ordering subtlety: "B.A." is a substring of "M.B.A." — the
        same longest-first requirement as the Dr. rer. pol. case above."""
        text = "Nach ihrem M.B.A. wechselte sie in die Beratung."
        assert _split_sentences(text) == [text]
