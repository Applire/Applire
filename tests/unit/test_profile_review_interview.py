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

"""US165 (E033 / ADR-041) — standalone profile-review interview (no JD).

The Health-panel "Resolve" action must launch a guided interview that has no
``job_id``: it walks the user's open Tier-2 health issues and resolves them in
place. This file covers:

  - the pure conflict-cluster helpers in ``interview_graph`` (deterministic, no
    LLM — a pending ``Conflict`` becomes a two-choice "which value is correct?"
    question, mirroring the US163 gate pattern).
  - the session wiring: a no-``job_id`` profile-review session whose gaps are the
    open conflicts, resolved through the ADR-013 merge (``resolve_conflict`` →
    ``manual_edit`` EnrichmentRecord), resume-safe, recomputing health on
    completion so the integration assertion "resolve a conflict, health clears"
    holds.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ===========================================================================
# Part 1 — pure conflict-cluster helpers (no DB, no LLM)
# ===========================================================================


class TestConflictClusterHelpers:
    def test_is_conflict_cluster_recognises_the_prefix(self):
        from applire.services.interview_graph import is_conflict_cluster

        assert is_conflict_cluster("conflict:" + uuid.uuid4().hex) is True
        assert is_conflict_cluster("cluster-gcp") is False
        assert is_conflict_cluster("gate:" + str(uuid.uuid4())) is False

    def test_conflict_question_names_the_field_and_both_values(self):
        from applire.services.interview_graph import conflict_question

        q = conflict_question(
            section="personal_info",
            field="name",
            existing_value="Max Muster",
            incoming_value="Markus Brandt",
        )
        assert "name" in q["question"]
        assert "Max Muster" in q["question"]
        assert "Markus Brandt" in q["question"]
        # A correction offers exactly the two outcomes: keep / use-imported.
        assert len(q["choices"]) == 2

    def test_conflict_question_localises_to_german(self):
        from applire.services.interview_graph import conflict_question

        en = conflict_question("personal_info", "name", "A", "B", lang="en")
        de = conflict_question("personal_info", "name", "A", "B", lang="de")
        assert en["question"] != de["question"]

    def test_build_conflict_clusters_makes_one_pseudo_gap_per_conflict(self):
        from applire.services.interview_graph import build_conflict_clusters

        cid = uuid.uuid4().hex[:12]
        ids, categories, by_id = build_conflict_clusters(
            [{
                "conflict_id": cid,
                "section": "personal_info",
                "field": "name",
                "existing_value": "Max Muster",
                "incoming_value": "Markus Brandt",
            }],
            lang="en",
        )
        assert ids == [f"conflict:{cid}"]
        # Conflict items carry their own category, distinct from JD C/B clusters.
        assert categories[ids[0]] not in ("C", "B")
        entry = by_id[ids[0]]
        assert entry["conflict_id"] == cid
        assert entry["section"] == "personal_info"
        assert entry["field"] == "name"
        assert entry["question"]
        assert len(entry["choices"]) == 2

    def test_interpret_conflict_answer_maps_the_two_choices(self):
        from applire.services.interview_graph import (
            conflict_question,
            interpret_conflict_answer,
        )

        q = conflict_question("personal_info", "name", "Max Muster", "Markus Brandt")
        keep_choice, use_choice = q["choices"]
        assert interpret_conflict_answer(
            keep_choice, "Max Muster", "Markus Brandt"
        ) == "existing"
        assert interpret_conflict_answer(
            use_choice, "Max Muster", "Markus Brandt"
        ) == "incoming"

    def test_interpret_conflict_answer_handles_plain_keep_or_new(self):
        from applire.services.interview_graph import interpret_conflict_answer

        assert interpret_conflict_answer("keep the current one", "A", "B") == "existing"
        assert interpret_conflict_answer("behalten", "A", "B") == "existing"
        assert interpret_conflict_answer("use the new value", "A", "B") == "incoming"
        assert interpret_conflict_answer("übernehmen", "A", "B") == "incoming"

    def test_interpret_conflict_answer_matches_a_typed_value(self):
        from applire.services.interview_graph import interpret_conflict_answer

        # The user may just type the correct value verbatim.
        assert interpret_conflict_answer("Markus Brandt", "Max Muster", "Markus Brandt") == "incoming"
        assert interpret_conflict_answer("Max Muster", "Max Muster", "Markus Brandt") == "existing"

    def test_interpret_conflict_answer_is_unclear_on_ambiguity(self):
        from applire.services.interview_graph import interpret_conflict_answer

        assert interpret_conflict_answer("what do you mean?", "A", "B") == "unclear"
        assert interpret_conflict_answer("", "A", "B") == "unclear"


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


def _make_profile_with_conflicts(*conflicts):
    """A MasterProfile whose metadata carries unresolved pending conflicts."""
    from applire.models.profile import MasterProfile

    pending = [
        {
            "conflict_id": c["conflict_id"],
            "section": c["section"],
            "field": c["field"],
            "existing_value": c["existing_value"],
            "incoming_value": c["incoming_value"],
            "source": c.get("source", "cv:other.pdf"),
            "resolved": False,
        }
        for c in conflicts
    ]
    return MasterProfile(profile_json={
        "personal_info": {"name": "Max Muster", "email": "max@example.de"},
        "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "professional_summary": {"en": "Experienced engineer"},
        "metadata": {"pending_conflicts": pending, "enrichment_history": []},
    })


def _conflict(section="personal_info", field="name",
              existing="Max Muster", incoming="Markus Brandt"):
    return {
        "conflict_id": uuid.uuid4().hex[:12],
        "section": section,
        "field": field,
        "existing_value": existing,
        "incoming_value": incoming,
    }


def _mock_provider():
    """Asserts the conflict path never consults the LLM."""
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(should not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


def _ui_language(db, lang):
    from applire.models.user_settings import UserSettings
    from applire.services.color_detection import _CE_STUB_USER_ID
    db.add(UserSettings(user_id=_CE_STUB_USER_ID, ui_language=lang))


class TestProfileReviewSessionCreation:
    @pytest.mark.asyncio
    async def test_first_conflict_is_the_first_question_with_no_job(self, sqlite_session):
        from applire.services.session import create_profile_review_session

        prof = _make_profile_with_conflicts(
            _conflict(field="name", existing="Max Muster", incoming="Markus Brandt"),
            _conflict(section="personal_info", field="email",
                      existing="max@example.de", incoming="markus@example.de"),
        )
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        resp = await create_profile_review_session(sqlite_session, provider)

        assert "Max Muster" in resp.first_question
        assert "Markus Brandt" in resp.first_question
        assert resp.gaps_total == 2
        # Deterministic — the LLM is not consulted for conflict questions.
        assert provider.acomplete.await_count == 0
        assert provider.aparse_json.await_count == 0
        # The session has no job_id (standalone profile review).
        from applire.models.session import InterviewSession
        rec = (await sqlite_session.execute(select(InterviewSession))).scalar_one()
        assert rec.job_analysis_id is None
        assert rec.status == "active"

    @pytest.mark.asyncio
    async def test_no_open_issues_completes_immediately(self, sqlite_session):
        from applire.services.session import create_profile_review_session

        prof = _make_profile_with_conflicts()  # no conflicts
        sqlite_session.add(prof)
        await sqlite_session.commit()

        resp = await create_profile_review_session(sqlite_session, _mock_provider())

        assert resp.gaps_total == 0
        assert resp.gaps_remaining == 0

    @pytest.mark.asyncio
    async def test_second_create_resumes_the_active_no_job_session(self, sqlite_session):
        from applire.services.session import create_profile_review_session

        prof = _make_profile_with_conflicts(_conflict())
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        first = await create_profile_review_session(sqlite_session, provider)
        second = await create_profile_review_session(sqlite_session, provider)

        assert second.session_id == first.session_id
        assert second.resumed is True
        from applire.models.session import InterviewSession
        rows = (await sqlite_session.execute(select(InterviewSession))).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_a_mode_c_enrich_session_is_not_mistaken_for_resume(self, sqlite_session):
        """A Mode-C enrichment session is also job-less; it must not be picked up
        as the profile-review session to resume (regression)."""
        from datetime import timedelta
        from applire.models.session import InterviewSession
        from applire.services.session import create_profile_review_session

        prof = _make_profile_with_conflicts(_conflict())
        sqlite_session.add(prof)
        await sqlite_session.flush()
        # An in-flight Mode-C enrichment session (no job_id, mode 'profile_enrich').
        sqlite_session.add(InterviewSession(
            job_analysis_id=None,
            profile_id=prof.id,
            mode="profile_enrich",
            status="active",
            state={"mode": "profile_enrich", "critical_gaps": []},
            hard_ceiling=9,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
        await sqlite_session.commit()

        resp = await create_profile_review_session(sqlite_session, _mock_provider())

        # A fresh profile-review session is created, not the enrich one resumed.
        assert resp.resumed is False
        assert "Max Muster" in resp.first_question


class TestProfileReviewConflictResolution:
    @pytest.mark.asyncio
    async def test_keep_current_resolves_via_existing_and_advances(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import MasterProfileData
        from applire.services.session import create_profile_review_session, send_message

        c1 = _conflict(field="name", existing="Max Muster", incoming="Markus Brandt")
        c2 = _conflict(field="email", existing="max@example.de", incoming="markus@example.de")
        prof = _make_profile_with_conflicts(c1, c2)
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()
        created = await create_profile_review_session(sqlite_session, provider)

        resp = await send_message(created.session_id, "keep the current one", sqlite_session, provider)

        # First conflict resolved → name unchanged; pending list shrank to the second.
        refreshed = await sqlite_session.get(MasterProfile, prof.id)
        data = MasterProfileData.model_validate(refreshed.profile_json)
        assert data.personal_info.name == "Max Muster"
        pending = [c for c in data.metadata.pending_conflicts if not c.resolved]
        assert len(pending) == 1
        assert pending[0].field == "email"
        # A manual_edit enrichment record was written (ADR-013 trail).
        assert any(r.source == "manual_edit" for r in data.metadata.enrichment_history)
        # Advanced to the second conflict's question, not complete.
        assert resp.complete is False
        assert "markus@example.de" in resp.question

    @pytest.mark.asyncio
    async def test_use_new_writes_the_incoming_value(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import MasterProfileData
        from applire.services.session import create_profile_review_session, send_message

        c1 = _conflict(field="name", existing="Max Muster", incoming="Markus Brandt")
        prof = _make_profile_with_conflicts(c1)
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()
        created = await create_profile_review_session(sqlite_session, provider)

        resp = await send_message(created.session_id, "use the new value", sqlite_session, provider)

        refreshed = await sqlite_session.get(MasterProfile, prof.id)
        data = MasterProfileData.model_validate(refreshed.profile_json)
        assert data.personal_info.name == "Markus Brandt"
        assert resp.complete is True

    @pytest.mark.asyncio
    async def test_unclear_answer_reasks_without_resolving(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import MasterProfileData
        from applire.services.session import create_profile_review_session, send_message

        prof = _make_profile_with_conflicts(_conflict())
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()
        created = await create_profile_review_session(sqlite_session, provider)

        resp = await send_message(created.session_id, "hmm not sure", sqlite_session, provider)

        assert resp.complete is False
        # Still unresolved — the same conflict is re-asked.
        refreshed = await sqlite_session.get(MasterProfile, prof.id)
        data = MasterProfileData.model_validate(refreshed.profile_json)
        assert len([c for c in data.metadata.pending_conflicts if not c.resolved]) == 1

    @pytest.mark.asyncio
    async def test_resolving_all_conflicts_clears_health(self, sqlite_session):
        """The named US165 integration assertion: launch from the panel, resolve a
        conflict, and the health read no longer reports a conflict issue."""
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import MasterProfileData
        from applire.services.profile.health import assess_health
        from applire.services.session import create_profile_review_session, send_message

        prof = _make_profile_with_conflicts(_conflict())
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        # Before: health reports one conflict issue.
        before = assess_health(MasterProfileData.model_validate(prof.profile_json))
        assert any(i.thread == "conflict" for i in before.issues)

        created = await create_profile_review_session(sqlite_session, provider)
        resp = await send_message(created.session_id, "keep current", sqlite_session, provider)
        assert resp.complete is True

        refreshed = await sqlite_session.get(MasterProfile, prof.id)
        after = assess_health(MasterProfileData.model_validate(refreshed.profile_json))
        assert not any(i.thread == "conflict" for i in after.issues)

    @pytest.mark.asyncio
    async def test_get_state_works_for_a_no_job_session(self, sqlite_session):
        from applire.services.session import create_profile_review_session, get_session_state

        prof = _make_profile_with_conflicts(_conflict())
        sqlite_session.add(prof)
        await sqlite_session.commit()
        created = await create_profile_review_session(sqlite_session, _mock_provider())

        state = await get_session_state(created.session_id, sqlite_session)
        assert state.job_id is None
        assert state.status == "active"
        assert state.current_question
