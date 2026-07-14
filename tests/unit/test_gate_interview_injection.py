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

"""US163 (E033 / ADR-041 amended) — escalate a deferred Tier-1 gate into the
JD-gap interview.

A parked integrity gate (US167: not-a-CV / name divergence) must surface as a
*mandatory, job-irrelevant* interview question the next time the user starts a
flow, so they can never tailor a CV from a profile whose origin they never
confirmed. This file covers:

  - the pure gate-cluster helpers in ``interview_graph`` (deterministic, no LLM)
  - the session wiring that prepends an open gate ahead of every JD gap and
    resolves it from the user's answer (idempotent on resume).
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# US184: import the profile package before applire.services.session. session.py
# imports the reconcile interview-bridge, and applire.services.profile imports
# get_ui_language from session — so importing session FIRST hits a partially
# initialised module. Loading profile first primes the package so the cycle
# resolves. (The underlying module-level cycle is a source-side concern.)
import applire.services.profile  # noqa: E402,F401


# ===========================================================================
# Part 1 — pure gate-cluster helpers (no DB, no LLM)
# ===========================================================================


class TestGateClusterHelpers:
    def test_is_gate_cluster_recognises_the_prefix(self):
        from applire.services.interview_graph import is_gate_cluster

        assert is_gate_cluster("gate:" + str(uuid.uuid4())) is True
        assert is_gate_cluster("cluster-gcp") is False
        assert is_gate_cluster("work_experience") is False

    def test_gate_question_name_divergence_names_both_people(self):
        from applire.services.interview_graph import gate_question

        q = gate_question("name_divergence", account_name="Anna Bauer", cv_name="Boris Schmidt")
        assert "Boris Schmidt" in q["question"]
        assert "Anna Bauer" in q["question"]
        # A blocking confirm offers exactly the two safe outcomes.
        assert len(q["choices"]) == 2

    def test_gate_question_not_a_cv_has_no_name_demand(self):
        from applire.services.interview_graph import gate_question

        q = gate_question("not_a_cv", account_name="Anna Bauer", cv_name=None)
        assert q["question"]
        assert len(q["choices"]) == 2

    def test_gate_question_localises_to_german(self):
        from applire.services.interview_graph import gate_question

        en = gate_question("name_divergence", "Anna", "Boris", lang="en")
        de = gate_question("name_divergence", "Anna", "Boris", lang="de")
        assert en["question"] != de["question"]

    def test_build_gate_clusters_prepends_one_pseudo_gap_per_gate(self):
        from applire.services.interview_graph import build_gate_clusters

        uid = uuid.uuid4()
        ids, categories, by_id = build_gate_clusters(
            [{"upload_id": uid, "gate": "name_divergence",
              "account_name": "Anna", "cv_name": "Boris"}],
            lang="en",
        )
        assert ids == [f"gate:{uid}"]
        # Gate items carry their own category, distinct from JD C/B clusters.
        assert categories[ids[0]] != "C"
        assert categories[ids[0]] != "B"
        entry = by_id[ids[0]]
        assert entry["upload_id"] == str(uid)
        assert entry["gate"] == "name_divergence"
        assert entry["question"]
        assert entry["choices"]

    def test_interpret_gate_answer_maps_the_merge_choice(self):
        from applire.services.interview_graph import gate_question, interpret_gate_answer

        q = gate_question("name_divergence", "Anna", "Boris")
        merge_choice, discard_choice = q["choices"]
        assert interpret_gate_answer(merge_choice) == "merge"
        assert interpret_gate_answer(discard_choice) == "discard"

    def test_interpret_gate_answer_handles_plain_yes_no(self):
        from applire.services.interview_graph import interpret_gate_answer

        assert interpret_gate_answer("yes") == "merge"
        assert interpret_gate_answer("ja") == "merge"
        assert interpret_gate_answer("no") == "discard"
        assert interpret_gate_answer("nein") == "discard"

    def test_interpret_gate_answer_is_unclear_on_ambiguity(self):
        from applire.services.interview_graph import interpret_gate_answer

        assert interpret_gate_answer("what do you mean?") == "unclear"
        assert interpret_gate_answer("") == "unclear"


# ===========================================================================
# Part 2 — session wiring (in-memory SQLite, no Docker)
# ===========================================================================


@pytest_asyncio.fixture
async def sqlite_session():
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
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_job(**kwargs):
    from applire.models.job import JobAnalysis
    defaults = dict(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer role requiring GCP and FastAPI.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "GCP", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )
    defaults.update(kwargs)
    return JobAnalysis(**defaults)


def _make_profile(name="Anna Bauer"):
    from applire.models.profile import MasterProfile
    return MasterProfile(profile_json={
        "personal_info": {"name": name, "email": "anna@example.de"},
        "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "professional_summary": {"en": "Experienced engineer"},
    })


def _make_gap(job_id, profile_id):
    from applire.models.gap import GapAnalysis
    clusters = [{
        "id": "cluster-gcp", "label": "GCP certification", "category": "C",
        "gaps": ["GCP certification"], "jd_skills": [], "jd_context": "",
    }]
    return GapAnalysis(
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=0.6,
        critical_gaps=["GCP certification"],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=["GCP certification"],
        gap_clusters=clusters,
    )


def _park_gate(user_id=None, gate="name_divergence", cv_name="Boris Schmidt"):
    """A held US167 upload: gate_status open, staged extraction parked."""
    from applire.models.uploads import UploadRecord
    return UploadRecord(
        user_id=user_id,
        original_filename="someone_else.pdf",
        content_hash=uuid.uuid4().hex,
        mime_type="application/pdf",
        file_path="/tmp/x.pdf",
        byte_size=1234,
        gate_status=gate,
        staged_extraction={
            "personal_info": {"name": cv_name},
            "work_experience": [{"company": "Globex", "role": "Designer", "start_date": "2021-03"}],
            "skills": [{"name": "Figma", "category": "technical"}],
        },
    )


def _mock_provider(question="Tell me about your GCP experience."):
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value=question)
    _turn = {
        "question": question, "choices": None,
        "gap_resolution": "full", "follow_up_hint": None,
        "skills_to_add": [], "work_history_to_add": [], "certifications_to_add": [],
        "languages_to_add": [], "education_to_add": [],
    }
    # US184: resolving the parked CV merges via the ADR-046 engine. The reconcile
    # call (system prompt = the "profile reconciler") returns ops that fold the
    # staged extraction (Figma skill + the Globex position) into the master
    # profile; every other aparse_json call is the interview turn.
    _reconcile = {
        "ops": [
            {"op": "upsert_skill", "ref": "s1", "name": "Figma", "category": "technical"},
            {"op": "upsert_work", "ref": "w1", "company": "Globex",
             "role": "Designer", "start_date": "2021-03"},
        ],
        "ambiguities": [],
    }

    async def _aparse_json(prompt, *, system=None, **kwargs):
        if "profile reconciler" in (system or "").lower():
            return _reconcile
        return _turn

    provider.aparse_json = AsyncMock(side_effect=_aparse_json)
    provider.__class__.__name__ = "MockProvider"
    return provider


async def _seed(db, gate=True):
    profile = _make_profile()
    db.add(profile)
    await db.flush()
    job = _make_job()
    db.add(job)
    await db.flush()
    db.add(_make_gap(job.id, profile.id))
    if gate:
        db.add(_park_gate())
    await db.commit()
    return job, profile


class TestGateInjectionIntoSession:
    @pytest.mark.asyncio
    async def test_open_gate_is_the_first_blocking_question(self, sqlite_session):
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session

        job, _ = await _seed(sqlite_session, gate=True)
        provider = _mock_provider()

        resp = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted"),
            sqlite_session, provider,
        )

        # The gate is asked first and counts toward the total — regardless of JD.
        assert "Boris Schmidt" in resp.first_question
        assert resp.gaps_total == 2  # gate + the one JD cluster
        # Deterministic gate question — the LLM was NOT consulted for it.
        assert provider.acomplete.await_count == 0
        assert provider.aparse_json.await_count == 0

    @pytest.mark.asyncio
    async def test_answering_yes_merges_the_parked_cv_and_advances(self, sqlite_session):
        from applire.models.uploads import UploadRecord
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session, send_message
        from sqlalchemy import select

        job, profile = await _seed(sqlite_session, gate=True)
        provider = _mock_provider()
        created = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted"),
            sqlite_session, provider,
        )

        resp = await send_message(created.session_id, "yes", sqlite_session, provider)

        upload = (await sqlite_session.execute(select(UploadRecord))).scalar_one()
        assert upload.gate_status == "resolved_merged"
        # The parked CV's data is now in the master profile.
        prof = await sqlite_session.get(type(profile), profile.id)
        skill_names = {s.get("name") for s in prof.profile_json.get("skills", [])}
        assert "Figma" in skill_names
        # Session advanced off the gate to the JD interview question.
        assert resp.complete is False

    @pytest.mark.asyncio
    async def test_answering_no_discards_without_merging(self, sqlite_session):
        from applire.models.uploads import UploadRecord
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session, send_message
        from sqlalchemy import select

        job, profile = await _seed(sqlite_session, gate=True)
        provider = _mock_provider()
        created = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted"),
            sqlite_session, provider,
        )

        await send_message(created.session_id, "no, discard it", sqlite_session, provider)

        upload = (await sqlite_session.execute(select(UploadRecord))).scalar_one()
        assert upload.gate_status == "resolved_discarded"
        prof = await sqlite_session.get(type(profile), profile.id)
        skill_names = {s.get("name") for s in prof.profile_json.get("skills", [])}
        assert "Figma" not in skill_names

    @pytest.mark.asyncio
    async def test_no_open_gate_means_no_injection(self, sqlite_session):
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session

        job, _ = await _seed(sqlite_session, gate=False)
        provider = _mock_provider()

        resp = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted"),
            sqlite_session, provider,
        )

        # Pure JD interview — no gate, so the JD cluster is first (LLM consulted).
        assert "Boris" not in resp.first_question
        assert resp.gaps_total == 1

    @pytest.mark.asyncio
    async def test_already_resolved_gate_is_not_reinjected(self, sqlite_session):
        from applire.models.uploads import UploadRecord
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session
        from sqlalchemy import select

        job, _ = await _seed(sqlite_session, gate=True)
        upload = (await sqlite_session.execute(select(UploadRecord))).scalar_one()
        upload.gate_status = "resolved_merged"
        await sqlite_session.commit()

        provider = _mock_provider()
        resp = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted"),
            sqlite_session, provider,
        )
        assert resp.gaps_total == 1
        assert "Boris" not in resp.first_question


# ===========================================================================
# Part 3 — flow routing: an open gate must not let a returning user skip the
# interview (US163: never tailor from an unconfirmed profile).
# ===========================================================================


class TestGateForcesInterviewRouting:
    def test_returning_user_skips_interview_without_a_gate(self):
        # ADR-016 amended 2026-07-13: the skip is GAP-driven — a returning user
        # goes straight to generation only when the analysis found nothing to
        # address (and no gate is parked).
        from applire.services.flow.orchestrator import _compute_actions

        actions = _compute_actions(
            "gap_analysis", "returning", has_open_gate=False, has_gaps=False
        )
        assert actions["next"] == "cv_generation"

    def test_open_gate_routes_returning_user_into_the_interview(self):
        from applire.services.flow.orchestrator import _compute_actions

        actions = _compute_actions("gap_analysis", "returning", has_open_gate=True)
        assert actions["next"] == "interview"
        # The skip-to-generation shortcut is withdrawn while a gate is open.
        assert actions.get("skip") != "cv_generation"

    def test_new_user_path_is_unchanged_by_the_gate_flag(self):
        from applire.services.flow.orchestrator import _compute_actions

        assert _compute_actions("gap_analysis", "new", has_open_gate=True)["next"] == "interview"
        assert _compute_actions("gap_analysis", "new", has_open_gate=False)["next"] == "interview"

    @pytest.mark.asyncio
    async def test_create_flow_offers_interview_when_a_gate_is_parked(self, sqlite_session):
        from applire.schemas.flow import CreateFlowRequest
        from applire.services.flow.orchestrator import advance_flow, create_flow
        from applire.schemas.flow import AdvanceFlowRequest

        # Returning user (complete profile) + a parked gate.
        job, _ = await _seed(sqlite_session, gate=True)
        created = await create_flow(
            CreateFlowRequest(job_id=job.id), uuid.uuid4(), sqlite_session
        )
        assert created.user_type == "returning"

        # Walk to gap_analysis; the offered next action must be the interview.
        from applire.services.flow.orchestrator import get_flow_state

        await advance_flow(
            created.flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()),
            sqlite_session,
        )
        flow_state = await get_flow_state(created.flow_id, sqlite_session)
        assert flow_state.available_actions["next"] == "interview"
