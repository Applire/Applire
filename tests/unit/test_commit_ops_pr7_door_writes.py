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

"""#480 PR 7 — the metadata-writer family's doors, and their inventory rows.

**§7.6's door-level write test, one per migrated writer.** `commit_ops` flushes
and never commits, so a forgotten `db.commit()` in a migrated caller is a SILENT
no-write. Each writer here is driven against a **file-backed** database and
re-read over a separate connection, so "did it survive the request?" is a real
question rather than an identity-map artefact.

The three doors this file drives:

* the **N/A writer** (`POST /api/profile/enrich/{id}/na`), which edited
  `profile_json` as a raw dict — no trail, no completeness recompute, no denial
  floor, no round-trip guarantee. It keeps its own `db.commit()`;
* the **interview-confirmation applier** (`session._apply_interview_confirmation`),
  the family-list correction: its metadata half already routes through
  `ResolveConfirmation` (PR 5), and what was left was a `skills[]` upsert
  reaching `_apply_upsert_skill` directly with the dedupe-bypass flag. It now
  routes as an `UpsertSkill` through the committer, with the bypass supplied on
  the CALL PATH so the model can never reach it;
* the **committer's receipt classification** for the two ADR-064 bookkeeping
  acts — an escalation must land on `denials` and never on `changes`.

The probe and escalation DOORS themselves (`_ask_denial_probe`,
`send_message`'s probe-answer branch) are driven end-to-end against a database
by `tests/unit/test_session_service.py`; those tests already read the committed
row back and are the write-survival coverage for those two sites. The known
blind spot recorded on #518 applies unchanged: the agent-channel door cannot
detect a dropped service-level commit because `send_message` commits
downstream.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_NA_GAP = "budget_managed: Senior Engineer @ Logivia"
_SECOND_GAP = "team_size: Senior Engineer @ Logivia"


def _seed_profile_json() -> dict:
    return {
        "personal_info": {"name": "Sven Hartmann", "email": "sven@example.de"},
        "professional_summary": {"en": "Experienced engineer"},
        "skills": [{"name": "React", "category": "technical", "status": "confirmed"}],
        "work_experience": [
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "company": "Logivia",
                "role": "Senior Engineer",
                "start_date": "2020-01",
                "is_current": True,
            }
        ],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "metadata": {
            "completeness_score": 0.0,
            "last_updated": "2020-01-01T00:00:00Z",
            "enrichment_history": [],
        },
    }


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    """A file-backed database — so a dropped `db.commit()` is observable."""
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    url = f"sqlite+aiosqlite:///{tmp_path / 'vault.sqlite'}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def _seed(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=_seed_profile_json())
        session.add(record)
        await session.commit()
        return record.id


async def _seed_enrich_session(factory, profile_id: uuid.UUID) -> uuid.UUID:
    from datetime import datetime, timedelta, timezone

    from applire.models.session import InterviewSession

    state = {
        "mode": "profile_enrich",
        "job_id": None,
        "gap_analysis_id": None,
        "profile_id": str(profile_id),
        "critical_gaps": [_NA_GAP, _SECOND_GAP],
        "gap_categories": {},
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": "How large was the budget?",
        "messages": [],
        "questions_asked": 1,
        "hard_ceiling": 6,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [_NA_GAP, _SECOND_GAP],
        "na_gaps": [],
    }
    async with factory() as session:
        record = InterviewSession(
            job_analysis_id=None,
            profile_id=profile_id,
            mode="profile_enrich",
            status="active",
            state=state,
            questions_asked=1,
            hard_ceiling=6,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id: uuid.UUID) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state."""
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        return dict(row.profile_json)


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(should not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


# ── Door: the N/A writer (`POST /api/profile/enrich/{id}/na`) ────────────────


@pytest.mark.asyncio
async def test_the_na_door_write_survives_the_request(durable_db):
    """Inventory row: the raw-dict writer becomes a `SetProfileMeta` act. The
    door keeps its own `db.commit()` — the committer only flushes."""
    from applire.routers.profile_enrich import mark_gap_na

    engine, factory = durable_db
    profile_id = await _seed(factory)
    session_id = await _seed_enrich_session(factory, profile_id)

    async with factory() as request_session:
        with patch(
            "applire.routers.profile_enrich.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "How large was the team?"}),
        ):
            response = await mark_gap_na(
                session_id, request_session, _mock_provider(), None
            )

    assert response.done is False
    stored = await _read_back(engine, profile_id)
    assert stored["_meta"]["na_fields"] == [_NA_GAP]


@pytest.mark.asyncio
async def test_the_na_door_leaves_exactly_one_trail_record(durable_db):
    """The raw writer left NO trail at all — an N/A suppression changed what the
    profile-health surface reports and was invisible on "what changed & why".
    Invariant 3 makes the record unconditional and the committer its only
    author."""
    from applire.routers.profile_enrich import mark_gap_na

    engine, factory = durable_db
    profile_id = await _seed(factory)
    session_id = await _seed_enrich_session(factory, profile_id)

    async with factory() as request_session:
        with patch(
            "applire.routers.profile_enrich.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "How large was the team?"}),
        ):
            await mark_gap_na(session_id, request_session, _mock_provider(), None)

    stored = await _read_back(engine, profile_id)
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual_edit"
    assert [(c["section"], c["field"], c["new_value"]) for c in history[0]["changes"]] == [
        ("_meta", "na_fields", _NA_GAP)
    ]


@pytest.mark.asyncio
async def test_the_na_door_recomputes_completeness_and_moves_the_clock(durable_db):
    """The raw writer wrote neither — invariants 4 and 5 arrive with the
    routing."""
    from applire.routers.profile_enrich import mark_gap_na

    engine, factory = durable_db
    profile_id = await _seed(factory)
    session_id = await _seed_enrich_session(factory, profile_id)

    async with factory() as request_session:
        with patch(
            "applire.routers.profile_enrich.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "How large was the team?"}),
        ):
            await mark_gap_na(session_id, request_session, _mock_provider(), None)

    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["completeness_score"] != 0.0
    assert stored["metadata"]["last_updated"] != "2020-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_the_na_door_still_records_the_suppression_in_session_state(durable_db):
    """The act has two halves — the durable `_meta` suppression and the
    session's own `na_gaps` cursor — and they are still ONE transaction."""
    from applire.models.session import InterviewSession
    from applire.routers.profile_enrich import mark_gap_na

    engine, factory = durable_db
    profile_id = await _seed(factory)
    session_id = await _seed_enrich_session(factory, profile_id)

    async with factory() as request_session:
        with patch(
            "applire.routers.profile_enrich.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "How large was the team?"}),
        ):
            await mark_gap_na(session_id, request_session, _mock_provider(), None)

    async with factory() as reader:
        row = (
            await reader.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
        ).scalar_one()
        assert row.state["na_gaps"] == [_NA_GAP]


# ── Door: the interview-confirmation applier (the family-list correction) ────


def _skill_context(incoming: str = "React Native") -> dict:
    return {
        "incoming_skill": incoming,
        "related_skills": ["React"],
        "category": "technical",
        "proficiency": None,
        "evidence_refs": [],
    }


@pytest.mark.asyncio
async def test_the_confirmation_skill_write_survives_a_commit(durable_db):
    """The last unrouted write in `_apply_interview_confirmation`: a `skills[]`
    upsert that called `_apply_upsert_skill` directly and assigned
    `profile_json` by hand."""
    from applire.models.profile import MasterProfile
    from applire.services.session import _apply_interview_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        applied = await _apply_interview_confirmation(
            request_session,
            record,
            _skill_context(),
            "Add 'React Native' as a separate skill",
            session_id=str(uuid.uuid4()),
        )
        # Flush, not commit — the caller owns the transaction, as it always did.
        await request_session.commit()

    assert applied is True
    stored = await _read_back(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["React", "React Native"]


@pytest.mark.asyncio
async def test_the_confirmation_merge_decision_keeps_its_bypass_semantics(durable_db):
    """"Merge into the existing skill" folds the incoming into the match and
    keeps the more specific name — the guard that would otherwise re-ask the
    question the candidate just answered is waived."""
    from applire.models.profile import MasterProfile
    from applire.services.session import _apply_interview_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        await _apply_interview_confirmation(
            request_session,
            record,
            _skill_context(),
            "Merge into the existing skill",
            session_id=str(uuid.uuid4()),
        )
        await request_session.commit()

    stored = await _read_back(engine, profile_id)
    assert [s["name"] for s in stored["skills"]] == ["React Native"]
    # And no confirmation was re-parked — the loop #187 fixed stays closed.
    assert stored["metadata"].get("pending_confirmations", []) == []


@pytest.mark.asyncio
async def test_keeping_the_existing_skills_writes_nothing(durable_db):
    """"Keep the existing skills" discards the incoming. The candidate still
    ANSWERED, so the parked ask is cleared elsewhere (`ResolveConfirmation`),
    but the vault's skill list is untouched and no commit is owed."""
    from applire.models.profile import MasterProfile
    from applire.services.session import _apply_interview_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        applied = await _apply_interview_confirmation(
            request_session,
            record,
            _skill_context(),
            "Keep the existing skills",
            session_id=str(uuid.uuid4()),
        )
        await request_session.commit()

    assert applied is False
    stored = await _read_back(engine, profile_id)
    assert [s["name"] for s in stored["skills"]] == ["React"]
    assert stored["metadata"]["enrichment_history"] == []


@pytest.mark.asyncio
async def test_a_non_skill_confirmation_writes_nothing(durable_db):
    """Entity near-dupe confirmations carry no `incoming_skill`; advancing the
    interview is the whole resolution (#187's stated scope)."""
    from applire.models.profile import MasterProfile
    from applire.services.session import _apply_interview_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        applied = await _apply_interview_confirmation(
            request_session,
            record,
            {"overlapping_entities": ["Logivia GmbH"]},
            "They are the same employer",
            session_id=str(uuid.uuid4()),
        )
        await request_session.commit()

    assert applied is False
    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["enrichment_history"] == []


@pytest.mark.asyncio
async def test_the_confirmation_write_gains_the_trail_and_the_recompute(durable_db):
    """The inventory row's two ❌ columns: this writer left no enrichment
    record and never recomputed completeness."""
    from applire.models.profile import MasterProfile
    from applire.services.session import _apply_interview_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory)
    session_id = str(uuid.uuid4())

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        await _apply_interview_confirmation(
            request_session,
            record,
            _skill_context(),
            "Add 'React Native' as a separate skill",
            session_id=session_id,
        )
        await request_session.commit()

    stored = await _read_back(engine, profile_id)
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "interview"
    assert history[0]["source_session_id"] == session_id
    assert stored["metadata"]["completeness_score"] != 0.0


# ── The committer: receipt classification for the bookkeeping acts ───────────


def _denial_seed() -> dict:
    payload = _seed_profile_json()
    payload["metadata"]["denied_concepts"] = [
        {
            "concept": "GCP-Zertifizierung",
            "statement": "Eine GCP-Zertifizierung habe ich nicht.",
            "source": "interview",
            "date": "2020-01-01",
            "denial_level": "direct",
            "probe_asked": False,
        }
    ]
    return payload


async def _seed_with_denial(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=_denial_seed())
        session.add(record)
        await session.commit()
        return record.id


# ── Door: the transfer probe (`_ask_denial_probe`) ───────────────────────────


@pytest.mark.asyncio
async def test_the_probe_flag_survives_the_request_it_was_issued_in(durable_db):
    """ADR-064's whole point: `probe_asked` is written the instant the probe is
    ISSUED, in the SAME commit as the question, so an ABANDONED session cannot
    lose it and a later genuine denial of the same concept cannot re-trigger a
    probe the candidate already received.

    `commit_ops` only flushes, so this branch's own `db.commit()` is what makes
    that true — and it is the terminal statement of the request, which is why a
    dropped commit here is observable (unlike the #518 blind spot, where
    `send_message` commits downstream). Driven against a file-backed database
    and re-read on a separate connection.
    """
    from datetime import datetime, timedelta, timezone

    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.session import _ask_denial_probe

    engine, factory = durable_db
    profile_id = await _seed_with_denial(factory)

    state = {
        "mode": "interview",
        "profile_id": str(profile_id),
        "critical_gaps": ["GCP-Zertifizierung", "FastAPI experience"],
        "current_gap_index": 0,
        "gap_categories": {},
        "addressed_gaps": [],
        "skipped_gaps": [],
        "questions_per_gap": {},
        "messages": [],
        "questions_asked": 1,
        "hard_ceiling": 6,
    }

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        session_row = InterviewSession(
            job_analysis_id=None,
            profile_id=profile_id,
            mode="interview",
            status="active",
            state=state,
            questions_asked=1,
            hard_ceiling=6,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        request_session.add(session_row)
        await request_session.commit()

        turn = InterviewTurnResult(
            profile_dict=record.profile_json,
            changes=[],
            addressed=False,
            denial_recorded=True,
            denied_concepts=["GCP-Zertifizierung"],
        )
        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(
                return_value={
                    "question": "Any adjacent cloud platform experience?",
                    "choices": None,
                }
            ),
        ):
            response = await _ask_denial_probe(
                session_row,
                state,
                request_session,
                _mock_provider(),
                "GCP-Zertifizierung",
                0,
                "GCP-Zertifizierung",
                record.profile_json,
                turn,
                1,
                "en",
                record,
            )

    assert response.complete is False
    stored = await _read_back(engine, profile_id)
    entry = stored["metadata"]["denied_concepts"][0]
    assert entry["probe_asked"] is True
    # Bookkeeping, never testimony — the level and the candidate's words stand.
    assert entry["denial_level"] == "direct"
    assert entry["statement"] == "Eine GCP-Zertifizierung habe ich nicht."


@pytest.mark.asyncio
async def test_an_escalation_is_receipted_as_a_denial_never_as_a_change(durable_db):
    """Ruling 3. `CommitResult.changes` is the only list an `addressed` /
    ledger-upgrade gate may read; an escalation is the candidate ruling MORE
    out and must never appear there."""
    from applire.models.profile import MasterProfile
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import EscalateDenialLevel

    engine, factory = durable_db
    profile_id = await _seed_with_denial(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        result = await commit_ops(
            request_session,
            [EscalateDenialLevel(concept="GCP-Zertifizierung")],
            CommitProvenance(source="interview", intake="denial_probe_escalation"),
            record=record,
            grounding=None,
            snapshot=None,
        )
        await request_session.commit()

    assert result.changes == []
    assert len(result.denials) == 1
    # …and it still reaches the candidate's trail, which is `changes`' union.
    assert [c.field for c in result.enrichment_record.changes] == ["denied_concepts"]

    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["denied_concepts"][0]["denial_level"] == "partial"


@pytest.mark.asyncio
async def test_a_probe_flag_is_receipted_as_a_denial_never_as_a_change(durable_db):
    from applire.models.profile import MasterProfile
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import MarkProbeAsked

    engine, factory = durable_db
    profile_id = await _seed_with_denial(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        result = await commit_ops(
            request_session,
            [MarkProbeAsked(concept="GCP-Zertifizierung")],
            CommitProvenance(source="interview", intake="denial_probe"),
            record=record,
            grounding=None,
            snapshot=None,
        )
        await request_session.commit()

    assert result.changes == []
    assert len(result.denials) == 1

    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["denied_concepts"][0]["probe_asked"] is True
    assert stored["metadata"]["denied_concepts"][0]["denial_level"] == "direct"


@pytest.mark.asyncio
async def test_both_bookkeeping_acts_commit_ungrounded(durable_db):
    """Ruling 4 — bookkeeping is never testimony, so `grounding=None`. The
    committer's `record_denials` invariant path therefore records nothing of its
    own for these turns: the ONLY denial receipts are the ops' own."""
    from applire.models.profile import MasterProfile
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import (
        EscalateDenialLevel,
        MarkProbeAsked,
    )

    engine, factory = durable_db
    profile_id = await _seed_with_denial(factory)

    async with factory() as request_session:
        record = (
            await request_session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        result = await commit_ops(
            request_session,
            [
                MarkProbeAsked(concept="GCP-Zertifizierung"),
                EscalateDenialLevel(concept="GCP-Zertifizierung"),
            ],
            CommitProvenance(source="interview", intake="denial_probe"),
            record=record,
            grounding=None,
            snapshot=None,
        )
        await request_session.commit()

    assert len(result.denials) == 2
    stored = await _read_back(engine, profile_id)
    entry = stored["metadata"]["denied_concepts"][0]
    assert entry["probe_asked"] is True
    assert entry["denial_level"] == "partial"
    # One act, one record — the committer is the trail's only author.
    assert len(stored["metadata"]["enrichment_history"]) == 1
