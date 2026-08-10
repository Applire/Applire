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

"""F4 (blind PQ 2026-07-02) — pipeline-level prompt capture for negation stance.

Drives the REAL ``analyze_gaps`` path with a profile that lacks Azure but whose
``metadata.enrichment_history`` carries an interview record quoting the
candidate's written denial. Captures the exact prompt sent to the LLM and pins:

* the denial DOES reach the gap classifier (the vector: the whole profile JSONB
  — enrichment trail included — is serialised into the user prompt), and
* after the fix it reaches it ONLY inside the labeled CANDIDATE INTERVIEW
  STATEMENTS section (never as unlabeled profile text), and
* the fingerprint hashes the profile CONTENT, so the prompt-shape change itself
  does not destabilise the reuse/idempotency path.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps
from tests.support.profile_factory import make_master_profile

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f4")

_DENIAL = "My cloud experience is AWS, not Azure - I have no hands-on Azure experience."


class _CaptureProvider(MockLLMProvider):
    """MockLLMProvider that records every (prompt, system) pair it is asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def aparse_json(self, prompt, **kwargs):  # type: ignore[override]
        self.calls.append((prompt, kwargs.get("system") or ""))
        return await super().aparse_json(prompt, **kwargs)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _profile_json_with_denial() -> dict:
    """A profile that has AWS but NOT Azure, plus an interview enrichment record
    quoting the candidate's written denial (the reconciler's audit trail)."""
    return {
        "work_experience": [
            {"company": "Rheinpharm", "role": "IT Quality Lead", "start_date": "2018-01"}
        ],
        "education": [],
        "skills": [{"name": "AWS", "category": "technical", "proficiency": "advanced"}],
        "languages": [],
        "personal_info": {"first_name": "Max", "last_name": "Muster", "email": "max@test.de"},
        "professional_summary": {"de": "", "en": ""},
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
        "metadata": {
            "completeness_score": 0.7,
            "enrichment_history": [
                {
                    "source": "interview",
                    "changes": [
                        {
                            "section": "work_experience",
                            "field": "achievements",
                            "action": "merged",
                            "new_value": ["Qualified the company's first GxP cloud environment (AWS)"],
                            "rationale": f'Candidate answered: "{_DENIAL}"',
                        }
                    ],
                }
            ],
        },
    }


@pytest_asyncio.fixture
async def seeded(db):
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-f4",
        raw_text="IT Quality Manager (Cloud)",
        role_title="IT Quality Manager",
        required_skills=["Cloud environment qualification (AWS, Azure)"],
        nice_to_have_skills=[],
        keywords=["AWS", "Azure"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json_with_denial())
    db.add_all([user, job, profile])
    await db.commit()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step="gap_analysis",
        user_type="new",
        available_actions={"next": "interview", "skip": "cv_generation"},
    )
    db.add(flow)
    await db.commit()
    return job, profile


def _gap_prompt(provider: _CaptureProvider) -> str:
    gap_calls = [
        p for (p, system) in provider.calls if "three-category gap analysis" in system.lower()
    ]
    assert gap_calls, "analyze_gaps must send the gap-classification call"
    return gap_calls[0]


@pytest.mark.asyncio
async def test_denial_reaches_the_gap_llm_only_inside_the_labeled_section(db, seeded):
    job, _profile = seeded
    provider = _CaptureProvider()

    await analyze_gaps(job.id, db, provider)

    prompt = _gap_prompt(provider)
    # The vector: prior-answer content DOES reach the classifier via the profile.
    assert _DENIAL in prompt, (
        "the interview denial must reach the gap classifier (as labeled statements)"
    )
    # …but only under the explicit stance label, never as unlabeled profile text.
    assert "CANDIDATE INTERVIEW STATEMENTS" in prompt, (
        "denial text reaches the LLM without any stance labeling (F4 vector)"
    )
    i_profile = prompt.index("CANDIDATE PROFILE:")
    i_stmts = prompt.index("CANDIDATE INTERVIEW STATEMENTS")
    i_pre = prompt.index("PRE-CLASSIFICATION:")
    assert i_profile < i_stmts < i_pre
    assert _DENIAL not in prompt[i_profile:i_stmts], (
        "the denial must not sit unlabeled inside the CANDIDATE PROFILE dump"
    )


_AGENT_DENIAL = (
    "I did not personally configure the embedding models, the vector store "
    "or any reranking."
)


def _profile_json_with_agent_denial() -> dict:
    """Same shape as `_profile_json_with_denial`, but the denial was recorded
    through the AGENT door (submit_claims/resolve_gap, `agent_interview`) —
    #231's double bug: denials weren't persisted at all, AND even once
    persisted the gap-analysis prompt's `_interview_statements` filter only
    looked for `source == "interview"`, silently excluding agent-door
    records."""
    return {
        "work_experience": [
            {"company": "Rheinpharm", "role": "IT Quality Lead", "start_date": "2018-01"}
        ],
        "education": [],
        "skills": [{"name": "RAG", "category": "technical", "proficiency": "advanced"}],
        "languages": [],
        "personal_info": {"first_name": "Max", "last_name": "Muster", "email": "max@test.de"},
        "professional_summary": {"de": "", "en": ""},
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
        "metadata": {
            "completeness_score": 0.7,
            "enrichment_history": [
                {
                    "source": "agent_interview",
                    "changes": [
                        {
                            "section": "metadata",
                            "field": "denied_concepts",
                            "action": "added",
                            "new_value": "embeddings",
                            "rationale": f'Candidate answered: "{_AGENT_DENIAL}"',
                        }
                    ],
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_agent_door_denial_reaches_the_gap_llm_same_as_interview_door(db):
    """#231 — `_interview_statements` must surface `agent_interview` records
    too, not only the built-in `interview` door, or the v4 stance rule can
    never apply to a denial elicited by an agent."""
    from applire.models.flow import FlowSession
    from applire.models.user import User

    user = User(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000f8"),
        email="local2@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-f8",
        raw_text="Backend Engineer (RAG/Embeddings)",
        role_title="Backend Engineer",
        required_skills=["Embeddings"],
        nice_to_have_skills=[],
        keywords=["RAG", "Embeddings"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json_with_agent_denial())
    db.add_all([user, job, profile])
    await db.commit()
    flow = FlowSession(
        user_id=user.id, job_id=job.id, current_step="gap_analysis",
        user_type="new", available_actions={"next": "interview", "skip": "cv_generation"},
    )
    db.add(flow)
    await db.commit()

    provider = _CaptureProvider()
    await analyze_gaps(job.id, db, provider)

    prompt = _gap_prompt(provider)
    assert "CANDIDATE INTERVIEW STATEMENTS" in prompt
    assert _AGENT_DENIAL in prompt
    i_profile = prompt.index("CANDIDATE PROFILE:")
    i_stmts = prompt.index("CANDIDATE INTERVIEW STATEMENTS")
    assert _AGENT_DENIAL not in prompt[i_profile:i_stmts]


@pytest.mark.asyncio
async def test_prompt_shape_change_does_not_destabilise_the_fingerprint(db, seeded):
    """Fingerprint hashes {job, profile_json} — not the prompt text. Same inputs
    must still reuse the stored row without a second LLM call."""
    job, _profile = seeded
    provider = _CaptureProvider()

    r1 = await analyze_gaps(job.id, db, provider)
    calls_after_first = len(provider.calls)
    r2 = await analyze_gaps(job.id, db, provider)

    assert r2.id == r1.id, "unchanged inputs must reuse the existing analysis row"
    assert len(provider.calls) == calls_after_first, "reuse must not re-call the LLM"
