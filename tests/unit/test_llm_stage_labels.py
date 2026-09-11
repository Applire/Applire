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

"""Seam tests for the debug-log stage-label leak fix (Nougat build 2, P2 / #contract-2).

Background (build-1's captured logs, ``PROMPT-INVENTORY.md``): four call-site
families used the imperative, NEVER-restoring ``debug_log.set_stage`` (or no
stage label at all), so their debug-log records either carried ``stage: None``
or silently INHERITED a stale label left behind by some earlier, unrelated
call in the same asyncio task. Each test below drives the REAL service
function with a fake provider that records ``debug_log.current_call_site()``
at call time (proving the label), then asserts that after the wrapped call
returns, the label is back to an outer marker set before the call (proving
``llm_log_stage`` — unlike ``set_stage`` — actually restores).

One seam test per call site (five for interview_graph.py's distinct drafting
paths, one each for reconcile, stance adjudication, skill estimation, and
field expectations).
"""

from __future__ import annotations

from typing import Any

import pytest

from applire.constants import FIELD_EXPECTATIONS_MAX_TOKENS
from applire.providers.llm.debug_log import current_call_site, set_stage
from applire.schemas.profile import MasterProfileData


# ---------------------------------------------------------------------------
# 1) reconcile/engine.py::reconcile — stage "reconcile"
# ---------------------------------------------------------------------------


class _StageCapturingProvider:
    """Records the stage label active AT CALL TIME; returns a canned payload.

    MUST accept **kwargs to absorb the full provider-ABC call signature.
    """

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.captured_stage: str | None = None
        self.last_kwargs: dict[str, Any] = {}

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.captured_stage = current_call_site()[0]
        self.last_kwargs = kwargs
        return self.payload

    async def acomplete(self, prompt: str, **kwargs: Any) -> Any:
        self.captured_stage = current_call_site()[0]
        self.last_kwargs = kwargs
        return self.payload


@pytest.mark.asyncio
async def test_reconcile_call_labeled_reconcile_and_restores() -> None:
    from applire.services.profile.reconcile.engine import reconcile

    set_stage("outer_marker")
    provider = _StageCapturingProvider({"ops": [], "ambiguities": []})

    await reconcile(MasterProfileData(), "I joined X as Y.", "interview", provider)

    assert provider.captured_stage == "reconcile"
    assert current_call_site()[0] == "outer_marker"


# ---------------------------------------------------------------------------
# 2) reconcile/stance.py::_adjudicate_testimony — stage "stance_adjudication"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stance_adjudication_call_labeled_and_restores() -> None:
    from applire.services.profile.reconcile.stance import _adjudicate_testimony

    set_stage("outer_marker")
    provider = _StageCapturingProvider({"answer": "yes", "quote": "I used Python daily."})

    result = await _adjudicate_testimony(
        provider, "Python", "skill", "I used Python daily."
    )

    assert result == ("yes", "I used Python daily.")
    assert provider.captured_stage == "stance_adjudication"
    assert current_call_site()[0] == "outer_marker"


# ---------------------------------------------------------------------------
# 3) interview_graph.py — five distinct drafting paths, all labeled
#    "interview_draft" (see report: "interview_question" already belongs to
#    prompts/review_question_language.py's reviewer chain — reusing it here
#    would make the drafting calls indistinguishable from the review calls,
#    exactly the inventory finding this fix removes).
#
#    Each test disables the language-review retry layer
#    (INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES = 0, the documented no-op
#    path already exercised by test_question_language.py's
#    test_review_disabled_returns_draft_unchanged) so EXACTLY ONE provider
#    call happens per invocation — the drafting call under test — making the
#    post-call restoration assertion unambiguous.
# ---------------------------------------------------------------------------


class _DraftCapturingProvider:
    """Records the stage label at every provider call made during one
    ``question_generator_with_profile`` invocation."""

    def __init__(self, question: str = "Frage?", choices: list | None = None) -> None:
        self.stages: list[str | None] = []
        self._question = question
        self._choices = choices

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        self.stages.append(current_call_site()[0])
        return self._question

    async def aparse_json(self, prompt: str, **kwargs: Any) -> dict:
        self.stages.append(current_call_site()[0])
        return {"question": self._question, "choices": self._choices}


def _disable_question_language_review(monkeypatch: pytest.MonkeyPatch) -> None:
    import applire.services.interview_graph as ig

    monkeypatch.setattr(ig, "INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES", 0)


@pytest.mark.asyncio
async def test_interview_denial_probe_question_labeled_interview_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applire.services.interview_graph as ig

    _disable_question_language_review(monkeypatch)
    set_stage("outer_marker")
    state = {
        "mode": "targeted",
        "critical_gaps": ["c1"],
        "current_gap_index": 0,
        "messages": [],
    }
    provider = _DraftCapturingProvider()

    await ig.question_generator_with_profile(
        state, {}, provider, lang="en",
        follow_up_hint="probe whether this transfers", denial_probe=True,
    )

    assert provider.stages == ["interview_draft"]
    assert current_call_site()[0] == "outer_marker"


@pytest.mark.asyncio
async def test_interview_followup_question_labeled_interview_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applire.services.interview_graph as ig

    _disable_question_language_review(monkeypatch)
    set_stage("outer_marker")
    state = {
        "mode": "targeted",
        "critical_gaps": ["c1"],
        "current_gap_index": 0,
        "messages": [],
    }
    provider = _DraftCapturingProvider()

    await ig.question_generator_with_profile(
        state, {}, provider, lang="en", follow_up_hint="be more specific",
    )

    assert provider.stages == ["interview_draft"]
    assert current_call_site()[0] == "outer_marker"


@pytest.mark.asyncio
async def test_interview_field_gap_question_labeled_interview_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applire.services.interview_graph as ig

    _disable_question_language_review(monkeypatch)
    set_stage("outer_marker")
    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["achievements: Team Lead @ Acme"],
        "current_gap_index": 0,
        "messages": [],
    }
    profile = {
        "work_experience": [
            {"role": "Team Lead", "company": "Acme", "responsibilities": ["Ran sprints"]}
        ]
    }
    provider = _DraftCapturingProvider()

    await ig.question_generator_with_profile(state, profile, provider, lang="en")

    assert provider.stages == ["interview_draft"]
    assert current_call_site()[0] == "outer_marker"


@pytest.mark.asyncio
async def test_interview_guided_question_labeled_interview_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applire.services.interview_graph as ig

    _disable_question_language_review(monkeypatch)
    set_stage("outer_marker")
    state = {
        "mode": "guided",
        "critical_gaps": ["skills"],
        "current_gap_index": 0,
        "messages": [],
    }
    provider = _DraftCapturingProvider()

    await ig.question_generator_with_profile(
        state, {}, provider, lang="en", job_context={"role_title": "QA"},
    )

    assert provider.stages == ["interview_draft"]
    assert current_call_site()[0] == "outer_marker"


@pytest.mark.asyncio
async def test_interview_mode_a_cluster_question_labeled_interview_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import applire.services.interview_graph as ig

    _disable_question_language_review(monkeypatch)
    set_stage("outer_marker")
    state = {
        "mode": "targeted",
        "critical_gaps": ["c1"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {
            "c1": {
                "id": "c1",
                "label": "Kubernetes",
                "gaps": ["Kubernetes"],
                "jd_skills": ["Kubernetes"],
                "jd_context": "This role requires Kubernetes experience.",
            }
        },
    }
    provider = _DraftCapturingProvider()

    await ig.question_generator_with_profile(state, {}, provider, lang="en")

    assert provider.stages == ["interview_draft"]
    assert current_call_site()[0] == "outer_marker"


# ---------------------------------------------------------------------------
# 4) skill_enrichment.py::enrich_skills — stage "skill_estimation"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_estimation_call_labeled_and_restores() -> None:
    from applire.schemas.profile import Skill, WorkEntry
    from applire.services.skill_enrichment import enrich_skills

    set_stage("outer_marker")
    profile = MasterProfileData(
        skills=[Skill(name="Agile", category="soft", proficiency="intermediate")],
        work_experience=[
            WorkEntry(
                company="Siemens AG",
                role="Scrum Master",
                start_date="2018-01",
                end_date="2021-01",
                technologies=["Jira"],  # "Agile" not in technologies -> unmatched
            )
        ],
    )
    provider = _StageCapturingProvider({"Agile": 4})

    await enrich_skills(profile, provider)

    assert provider.captured_stage == "skill_estimation"
    assert current_call_site()[0] == "outer_marker"


# ---------------------------------------------------------------------------
# 5) profile/expectations.py::annotate_expected_fields — stage
#    "field_expectations"; also the Task B max_tokens budget.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_field_expectations_call_labeled_and_restores() -> None:
    from applire.services.profile.expectations import annotate_expected_fields

    set_stage("outer_marker")
    profile = {"work_experience": [{"role": "Team Lead", "company": "Acme"}]}
    provider = _StageCapturingProvider({"expected": ["team_size"]})

    await annotate_expected_fields(profile, provider)

    assert provider.captured_stage == "field_expectations"
    assert current_call_site()[0] == "outer_marker"


@pytest.mark.asyncio
async def test_field_expectations_call_uses_dedicated_max_tokens_budget() -> None:
    """Task B: previously the only LLM call in the whole prompt inventory with
    NO max_tokens at all (real captured records: max_tokens: null)."""
    from applire.services.profile.expectations import annotate_expected_fields

    profile = {"work_experience": [{"role": "Team Lead", "company": "Acme"}]}
    provider = _StageCapturingProvider({"expected": ["team_size"]})

    await annotate_expected_fields(profile, provider)

    assert provider.last_kwargs.get("max_tokens") == FIELD_EXPECTATIONS_MAX_TOKENS
