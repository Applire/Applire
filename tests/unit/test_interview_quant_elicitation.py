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

"""US265 (E048 / ADR-058 exception b) — quantification + availability elicitation.

The EXISTING interview question generator gains a deterministic quantification
detector (services/interview_quant.py, reusing services/oracle/matchers/figures.py)
plus a prompt-level rule folded into build_question_prompt. No new interview
mode/engine, no ceiling change, no denial-machinery change, no new LLM chain.

Covers:
  - detect_unquantified_concepts: evidenced+figure-free flagged; evidenced+figures
    not flagged; unevidenced not flagged.
  - build_question_prompt: quantification instruction present + capped at one
    concept when flagged; byte-identical to the no-flag call when nothing is
    flagged (prompt stability).
  - Ruling V-4 (Hazelnut build 3, 2026-10-22): the availability/notice-period
    add-on and its trigger are REMOVED — no session, door or prompt builder
    asks the notice period any more (a login info card prompts the profile
    field instead).
  - No-re-ask proof: a real multi-turn exchange (question_generator_with_profile,
    then the follow-up path) never emits the quantification instruction twice —
    it structurally cannot, because build_follow_up_question_prompt carries no
    such instruction at all.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Part A — detect_unquantified_concepts (pure, deterministic)
# ---------------------------------------------------------------------------


def _cluster(gaps, jd_context=""):
    return {
        "id": "cluster-1",
        "label": "Team Leadership",
        "category": "C",
        "gaps": gaps,
        "jd_skills": [],
        "jd_context": jd_context,
    }


def test_evidenced_figure_free_concept_is_flagged():
    """The profile evidences the concept (a technology on a role) with no
    number anywhere in that evidence — this is exactly the quantification gap."""
    from applire.services.interview_quant import detect_unquantified_concepts

    cluster = _cluster(["CI/CD"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": ["Introduced CI/CD practices across the team."],
                "achievements": [],
            }
        ]
    }
    assert detect_unquantified_concepts(cluster, profile) == ["CI/CD"]


def test_evidenced_with_figures_is_not_flagged():
    """The same concept, but the evidence text already carries a number —
    already quantified, nothing to elicit."""
    from applire.services.interview_quant import detect_unquantified_concepts

    cluster = _cluster(["CI/CD"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": [
                    "Introduced CI/CD pipelines for a team of 12 engineers."
                ],
                "achievements": [],
            }
        ]
    }
    assert detect_unquantified_concepts(cluster, profile) == []


def test_unevidenced_concept_is_not_flagged():
    """No evidence anywhere (cluster jd_context or profile bullets) — this is a
    normal gap question, never a quantification prompt."""
    from applire.services.interview_quant import detect_unquantified_concepts

    cluster = _cluster(["Kubernetes"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["Python"],
                "responsibilities": ["Built REST APIs."],
                "achievements": [],
            }
        ]
    }
    assert detect_unquantified_concepts(cluster, profile) == []


def test_jd_context_alone_can_evidence_a_concept():
    """jd_context (the cluster's own description) is evidence too, independent
    of profile bullets."""
    from applire.services.interview_quant import detect_unquantified_concepts

    cluster = _cluster(["stakeholder management"], jd_context="stakeholder management is core to this role")
    profile = {"work_experience": []}
    assert detect_unquantified_concepts(cluster, profile) == ["stakeholder management"]


def test_multiple_gaps_only_unquantified_ones_flagged():
    from applire.services.interview_quant import detect_unquantified_concepts

    cluster = _cluster(["CI/CD", "Kubernetes", "Docker"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD", "Docker"],
                "responsibilities": [
                    "Ran CI/CD for the whole department.",
                    "Migrated 40 services onto Docker.",
                ],
                "achievements": [],
            }
        ]
    }
    flagged = detect_unquantified_concepts(cluster, profile)
    assert "CI/CD" in flagged  # no figure in its evidence
    assert "Docker" not in flagged  # "40 services" quantifies it
    assert "Kubernetes" not in flagged  # unevidenced


# ---------------------------------------------------------------------------
# Part B — build_question_prompt: instruction content + stability
# ---------------------------------------------------------------------------


def test_no_flag_prompt_is_byte_identical_to_default_call():
    """Passing quant_concepts=None (the default) must reproduce EXACTLY what a
    pre-US265 call produced."""
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}
    messages = [{"role": "user", "content": "hi"}]

    baseline = build_question_prompt(cluster, profile, messages)
    explicit_defaults = build_question_prompt(
        cluster, profile, messages, quant_concepts=None
    )
    explicit_empty = build_question_prompt(
        cluster, profile, messages, quant_concepts=[]
    )
    assert baseline == explicit_defaults == explicit_empty


def test_flagged_concept_adds_quantification_instruction():
    from applire.prompts.interview import build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": ["CI/CD"], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [], "work_experience": []}

    out = build_question_prompt(cluster, profile, [], quant_concepts=["CI/CD"])
    assert "quantif" in out.lower()
    assert "CI/CD" in out
    # Terminal-answer rule must be explicit in the instruction.
    assert "valid" in out.lower() and ("final" in out.lower() or "terminal" in out.lower())
    assert "do not" in out.lower() or "never" in out.lower()  # forbids re-asking


def test_multiple_flagged_concepts_capped_at_one_mention():
    """Even if the detector flags several concepts, the instruction names only
    ONE — 'at most ONE follow-up ... may ask for quantification'."""
    from applire.prompts.interview import build_question_prompt

    cluster = {
        "id": "c1", "label": "X", "gaps": ["CI/CD", "Kubernetes"],
        "jd_skills": [], "jd_context": "",
    }
    profile = {"skills": [], "work_experience": []}

    out = build_question_prompt(
        cluster, profile, [], quant_concepts=["CI/CD", "Kubernetes"]
    )
    assert out.lower().count("quantification opportunity") == 1
    # The instruction BLOCK itself names only the one flagged concept it acts
    # on — "Kubernetes" may legitimately appear earlier (constituent gaps
    # listing), but never inside the quantification instruction.
    instruction = out.split("Quantification opportunity:", 1)[1]
    assert "CI/CD" in instruction
    assert "Kubernetes" not in instruction


def test_no_prompt_builder_takes_an_availability_add_on_any_more():
    """Ruling V-4: the add-on is gone, not defaulted off — a caller that still
    passes it fails loudly instead of silently re-introducing the ask."""
    from applire.prompts import interview
    from applire.prompts.interview import build_guided_question_prompt, build_question_prompt

    cluster = {"id": "c1", "label": "X", "gaps": [], "jd_skills": [], "jd_context": ""}
    with pytest.raises(TypeError):
        build_question_prompt(cluster, {}, [], include_availability=True)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        build_guided_question_prompt("skills", {}, [], include_availability=True)  # type: ignore[call-arg]
    assert not hasattr(interview, "_AVAILABILITY_INSTRUCTION")


def test_the_availability_trigger_is_removed():
    from applire.services import interview_quant

    for name in ("should_ask_availability", "detect_availability_signal", "has_multiple_open_roles"):
        assert not hasattr(interview_quant, name), name


# ---------------------------------------------------------------------------
# Part D — no-re-ask proof
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_path_never_carries_the_quantification_instruction():
    """The quantification ask lives ONLY in the once-per-cluster initial prompt
    (build_question_prompt). The follow-up path (build_follow_up_question_prompt)
    is a structurally different prompt builder with no quant parameter at all —
    so even after repeated unaddressed answers on the SAME gap, the follow-up
    question generation can never re-ask for numbers.
    """
    from applire.services.interview_graph import question_generator_with_profile

    cluster = {
        "id": "cluster-cicd", "label": "CI/CD", "category": "C",
        "gaps": ["CI/CD"], "jd_skills": [], "jd_context": "",
    }
    profile = {
        "skills": [],
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": ["Ran CI/CD across the org."],
                "achievements": [],
            }
        ],
    }
    state = {
        "mode": "targeted",
        "critical_gaps": ["cluster-cicd"],
        "current_gap_index": 0,
        "messages": [],
        "gap_clusters_by_id": {"cluster-cicd": cluster},
    }

    # Turn 1: the initial (non-follow-up) question for the cluster — captures
    # the actual user-content prompt handed to the LLM.
    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"question": "Tell me about CI/CD.", "choices": None, "approved": True}
    )
    await question_generator_with_profile(
        state, profile, provider, gap_category="C",
    )
    # First call is the actual question-generation prompt; a later call may be
    # the (approved-on-first-pass) language review — the FIRST call is the one
    # that must carry the quant instruction.
    initial_prompt = provider.aparse_json.call_args_list[0].args[0]
    assert "quantif" in initial_prompt.lower()  # evidenced+figure-free → flagged

    # Turns 2..N: the follow-up path for the SAME gap (as send_message drives it
    # while the reconciler keeps reporting "not addressed").
    for _ in range(3):
        follow_provider = MagicMock()
        follow_provider.acomplete = AsyncMock(return_value="Can you be more specific?")
        follow_provider.aparse_json = AsyncMock(
            return_value={"approved": True, "issues": [], "feedback": ""}
        )
        await question_generator_with_profile(
            state, profile, follow_provider, gap_category="C",
            follow_up_hint="ask for a more specific or concrete example related to cluster-cicd",
        )
        follow_up_prompt = follow_provider.acomplete.call_args.args[0]
        assert "quantif" not in follow_up_prompt.lower()


# ---------------------------------------------------------------------------
# Part E — full sessions never ask the notice period (ruling V-4, Hazelnut
# build 3, 2026-10-22). Until then US265 folded an availability ask into the
# first question of a session whose posting named an availability marker and
# whose profile showed >= 2 open roles; these are that exact founder-charter
# shape, now pinned to carry NO such ask on any session-creation path.
# ---------------------------------------------------------------------------

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest_asyncio


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite async session — no Docker required (mirrors the
    fixture in test_session_service.py; kept local so this file stays
    self-contained)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _availability_job(**kwargs):
    from applire.models.job import JobAnalysis

    defaults = dict(
        raw_text_hash=uuid.uuid4().hex,
        raw_text=(
            "Senior Engineer role. This is a permanent employment position; "
            "please state your notice period."
        ),
        role_title="Senior Engineer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )
    defaults.update(kwargs)
    return JobAnalysis(**defaults)


def _profile_two_open_roles():
    from tests.support.profile_factory import make_master_profile

    return make_master_profile(
        profile_json={
            "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
            "skills": [],
            "work_experience": [
                {"company": "Acme", "role": "Engineer", "is_current": True, "end_date": None},
                {"company": "Beta", "role": "Consultant", "is_current": True, "end_date": None},
            ],
        }
    )


def _spy_provider(question="Tell me about your background."):
    """A provider whose acomplete/aparse_json calls are individually
    inspectable via call_args_list, unlike the fully-mocked
    question_generator_with_profile used elsewhere — this test needs the
    REAL build_guided_question_prompt output to reach the provider."""
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value=question)
    provider.aparse_json = AsyncMock(
        return_value={"approved": True, "issues": [], "feedback": ""}
    )
    provider.__class__.__name__ = "SpyProvider"
    return provider


@pytest.mark.asyncio
async def test_guided_session_first_section_never_asks_the_notice_period(sqlite_session):
    """The founder-charter shape (availability marker + >= 2 open roles) that
    used to fold the ask in: the first section question carries none."""
    from applire.services.session import create_session
    from applire.schemas.session import SessionCreateRequest

    job = _availability_job()
    profile = _profile_two_open_roles()
    sqlite_session.add(job)
    sqlite_session.add(profile)
    await sqlite_session.commit()

    req = SessionCreateRequest(job_id=job.id, mode="guided")
    provider = _spy_provider()

    await create_session(req, sqlite_session, provider)

    first_prompt = provider.acomplete.call_args_list[0].args[0]
    assert "notice period" not in first_prompt.lower()
    assert "availability opportunity" not in first_prompt.lower()


@pytest.mark.asyncio
async def test_guided_session_later_section_never_recarries_availability(sqlite_session):
    """Once the FIRST section's question has (optionally) carried the
    availability instruction, advancing to the SECOND section within the SAME
    full session must never carry it again — the one-shot check is computed
    exactly once, at session creation, never on advance."""
    from applire.services.session import create_session, send_message
    from applire.schemas.session import SessionCreateRequest
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    job = _availability_job()
    profile = _profile_two_open_roles()
    sqlite_session.add(job)
    sqlite_session.add(profile)
    await sqlite_session.commit()

    req = SessionCreateRequest(job_id=job.id, mode="guided")
    create_provider = _spy_provider()
    create_result = await create_session(req, sqlite_session, create_provider)

    first_prompt = create_provider.acomplete.call_args_list[0].args[0]
    assert "notice period" not in first_prompt.lower()

    # Advance to the second section: the reconciler reports a real profile
    # change (addressed=True) so send_message's advance branch fires and
    # generates the NEXT section's question via the real prompt builder.
    updated_profile = dict(profile.profile_json)
    turn = InterviewTurnResult(
        profile_dict=updated_profile,
        changes=[FieldChange(section="work_experience", field="Acme", action="added")],
        addressed=True,
        conflict_summaries=[],
    )
    advance_provider = _spy_provider("Tell me about your education.")
    with patch(
        "applire.services.session.reconcile_interview_turn",
        new=AsyncMock(return_value=turn),
    ):
        result = await send_message(
            create_result.session_id,
            "I worked at Acme as an engineer for 5 years.",
            sqlite_session,
            advance_provider,
        )

    assert result.complete is False
    second_prompt = advance_provider.acomplete.call_args_list[0].args[0]
    assert "notice period" not in second_prompt.lower()
    assert "availability opportunity" not in second_prompt.lower()
