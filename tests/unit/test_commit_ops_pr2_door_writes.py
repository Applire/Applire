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

"""#480 PR 2 (design §7.6) — one door-level write test per migrated writer.

`commit_ops` **flushes and never commits**: the transaction stays with the
caller so `services/session.py` can write `session.state` and the vault as one
unit. The cost is named honestly in §7.6 — *a forgotten `db.commit()` is a
silent no-write* — and the binding mitigation is a test per migrated writer,
landed with the migration. PR 1 did that for the testimony and agent-claims
bridges (`test_commit_ops_door_writes.py`); PR 2 migrates four more writers:

* `_import_from_text` — the CV-paste / LinkedIn / MCP `import_cv` writer;
* `_apply_merge`      — the browser `/upload` writer;
* the enrich-router turn (`POST /api/profile/enrich/{id}/respond`);
* the `send_message` interview turn.

Every test drives the real writer against a **file-backed** database and
re-reads over a SEPARATE connection, so an uncommitted write is invisible —
which no in-session assertion could tell you.

The file also pins the two properties PR 2's `snapshot` parameter is FOR:
an import stays undoable, and an interview session — however long — snapshots
nothing, so it can never evict the import snapshot the undo exists for
(ADR-063 amendment (5) / #339).
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_SEED_PROFILE = {
    "personal_info": {"full_name": "Anna Bauer", "email": "anna@example.invalid"},
    "skills": [{"name": "Python", "category": "technical", "status": "confirmed"}],
    "work_experience": [
        {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
    ],
    "metadata": {},
}


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    """A file-backed database — so "did it survive the request?" is a real
    question and not an identity-map artefact."""
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


async def _seed_profile(factory, profile_json: dict | None = None) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=profile_json or dict(_SEED_PROFILE))
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


async def _snapshots(engine, profile_id: uuid.UUID) -> list:
    from applire.models.profile import ProfileSnapshot

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(ProfileSnapshot)
                    .where(ProfileSnapshot.profile_id == profile_id)
                    .order_by(ProfileSnapshot.created_at, ProfileSnapshot.id)
                )
            )
            .scalars()
            .all()
        )


def _merge_of(*skill_names: str):
    """A `reconcile_import` stand-in: the finished merged profile the bridge
    would have computed, plus its receipts — exactly what `ApplyImportMerge`
    carries."""
    from applire.schemas.profile import FieldChange, MasterProfileData
    from applire.services.profile.merge import MergeResult

    merged = MasterProfileData.model_validate(
        {
            **_SEED_PROFILE,
            "skills": [
                *_SEED_PROFILE["skills"],
                *(
                    {"name": n, "category": "technical", "status": "confirmed"}
                    for n in skill_names
                ),
            ],
        }
    )
    return MergeResult(
        merged_profile=merged,
        added=list(skill_names),
        changes=[
            FieldChange(section="skills", field="name", action="added", new_value=n)
            for n in skill_names
        ],
        reconciliation={"skills": {"extracted": len(skill_names), "stored": 1, "delta": 0}},
    )


# ── Door 3: the CV-paste / LinkedIn / MCP import writer (`_import_from_text`) ──


@pytest.mark.asyncio
async def test_import_door_write_survives_the_request(durable_db):
    from applire.services.profile import import_from_text

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    extracted = {"skills": [{"name": "Kafka", "category": "technical"}]}
    async with factory() as request_session:
        with (
            patch(
                "applire.services.profile.extract_with_fallback",
                new=AsyncMock(return_value=extracted),
            ),
            patch(
                "applire.services.profile.review_and_refine",
                new=AsyncMock(return_value=extracted),
            ),
            patch(
                "applire.services.profile.annotate_expected_fields", new=AsyncMock()
            ),
            patch(
                "applire.services.profile.enrich_skills",
                new=AsyncMock(side_effect=lambda p, _prov: p),
            ),
            patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")),
            patch(
                "applire.services.profile.reconcile_import",
                new=AsyncMock(return_value=_merge_of("Kafka")),
            ),
        ):
            await import_from_text("Kafka, three years.", request_session, AsyncMock())

    stored = await _read_back(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["Kafka", "Python"]
    # The committer's invariants are durable too, not just the merge.
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "cv_paste"
    # US161 — the merge statistics survive the move onto the committer's record.
    assert history[0]["reconciliation"] == {
        "skills": {"extracted": 1, "stored": 1, "delta": 0}
    }
    assert stored["metadata"]["completeness_score"] > 0


@pytest.mark.asyncio
async def test_import_door_write_is_still_undoable(durable_db):
    """The ADR-042 guarantee, now expressed as `snapshot=SnapshotClass.MERGE`
    instead of an inline call: the import is snapshotted BEFORE it lands, keyed
    to its own enrichment record, and `undo_last_merge` restores the exact
    pre-import vault."""
    from applire.services.profile import import_from_text
    from applire.services.profile.snapshots import undo_last_merge

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    before = await _read_back(engine, profile_id)

    extracted = {"skills": [{"name": "Kafka", "category": "technical"}]}
    async with factory() as request_session:
        with (
            patch(
                "applire.services.profile.extract_with_fallback",
                new=AsyncMock(return_value=extracted),
            ),
            patch(
                "applire.services.profile.review_and_refine",
                new=AsyncMock(return_value=extracted),
            ),
            patch(
                "applire.services.profile.annotate_expected_fields", new=AsyncMock()
            ),
            patch(
                "applire.services.profile.enrich_skills",
                new=AsyncMock(side_effect=lambda p, _prov: p),
            ),
            patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")),
            patch(
                "applire.services.profile.reconcile_import",
                new=AsyncMock(return_value=_merge_of("Kafka")),
            ),
        ):
            await import_from_text("Kafka, three years.", request_session, AsyncMock())

    snaps = await _snapshots(engine, profile_id)
    assert len(snaps) == 1
    assert snaps[0].profile_json == before
    # Keyed to the record the committer minted, so undo can tell whether that
    # merge is still the profile's head.
    after = await _read_back(engine, profile_id)
    assert snaps[0].enrichment_record_id == after["metadata"]["enrichment_history"][-1]["id"]

    async with factory() as session:
        result = await undo_last_merge(session)
        await session.commit()
    assert result.restored is True
    assert result.discarded_later_edits is False
    assert await _read_back(engine, profile_id) == before


# ── Door 4: the browser `/upload` writer (`_apply_merge`) ─────────────────────


@pytest.mark.asyncio
async def test_upload_door_write_survives_the_request(durable_db):
    from applire.providers.embedding.noop import NoopEmbeddingProvider
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile import _apply_merge

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        with (
            patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")),
            patch(
                "applire.services.profile.reconcile_import",
                new=AsyncMock(return_value=_merge_of("Terraform")),
            ),
        ):
            returned_id, completeness, conflicts, enrichment_id = await _apply_merge(
                request_session,
                MasterProfileData.model_validate(
                    {"skills": [{"name": "Terraform", "category": "technical"}]}
                ),
                source="cv_upload",
                emb_provider=NoopEmbeddingProvider(),
                provider=AsyncMock(),
            )

    assert returned_id == profile_id
    stored = await _read_back(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["Python", "Terraform"]
    # The id this door hands back still names the record it wrote AND the
    # snapshot it keyed — they just agree on the committer's id now.
    history = stored["metadata"]["enrichment_history"]
    assert str(enrichment_id) == history[-1]["id"]
    snaps = await _snapshots(engine, profile_id)
    assert [s.enrichment_record_id for s in snaps] == [str(enrichment_id)]
    assert completeness == stored["metadata"]["completeness_score"]
    assert conflicts == []


# ── Door 5: the enrich-router turn ────────────────────────────────────────────


def _enrich_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(unused)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


async def _start_enrich(session_factory, provider, monkeypatch_target):
    from applire.routers.profile_enrich import start_enrich_session
    from applire.schemas.enrich import EnrichStartRequest

    return await start_enrich_session(
        EnrichStartRequest(), session_factory, provider, None
    )


@pytest.fixture
def _stub_enrich_generators(monkeypatch):
    import applire.routers.profile_enrich as pe

    monkeypatch.setattr(
        pe,
        "gap_detector_mode_c",
        lambda profile_data, scope=None: ["team_size: Engineer @ Acme GmbH"],
    )

    async def _fake_question(state, profile_data, provider, lang="en"):
        return {"question": "How large was the team?"}

    monkeypatch.setattr(pe, "question_generator_with_profile", _fake_question)


@pytest.mark.asyncio
async def test_enrich_router_turn_write_survives_the_request(
    durable_db, _stub_enrich_generators, monkeypatch
):
    from applire.routers.profile_enrich import respond_to_enrich, start_enrich_session
    from applire.schemas.enrich import EnrichRespondRequest, EnrichStartRequest
    from applire.services.profile.reconcile.ops import ReconcileResult, UpsertSkill
    import applire.services.profile.reconcile.interview_bridge as ib

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    provider = _enrich_provider()

    async def _one_skill(before, new_info, source, provider, lang="en"):
        return ReconcileResult(
            ops=[UpsertSkill(name="Kafka", category="technical")], denials=[]
        )

    monkeypatch.setattr(ib, "reconcile", _one_skill)

    async with factory() as request_session:
        started = await start_enrich_session(
            EnrichStartRequest(), request_session, provider, None
        )
        await respond_to_enrich(
            started.session_id,
            EnrichRespondRequest(answer="Twelve engineers, and I ran Kafka."),
            request_session,
            provider,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["Kafka", "Python"]
    assert len(stored["metadata"]["enrichment_history"]) == 1


@pytest.mark.asyncio
async def test_enrich_router_denial_only_turn_receipt_survives_the_request(
    durable_db, _stub_enrich_generators, monkeypatch
):
    """#338 through the new path. A denial-only turn used to be gated out of
    the write by `addressed`; #338 widened the gate; `commit_ops` removes it.
    The receipt must still be there after the request, not just in memory."""
    from applire.routers.profile_enrich import respond_to_enrich, start_enrich_session
    from applire.schemas.enrich import EnrichRespondRequest, EnrichStartRequest
    from applire.services.profile.reconcile.ops import ReconcileResult
    import applire.services.profile.reconcile.interview_bridge as ib

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    provider = _enrich_provider()

    async def _denial_only(before, new_info, source, provider, lang="en"):
        return ReconcileResult(ops=[], denials=["Kubernetes"])

    monkeypatch.setattr(ib, "reconcile", _denial_only)

    async with factory() as request_session:
        started = await start_enrich_session(
            EnrichStartRequest(), request_session, provider, None
        )
        await respond_to_enrich(
            started.session_id,
            EnrichRespondRequest(answer="I have never touched Kubernetes."),
            request_session,
            provider,
            None,
        )

    stored = await _read_back(engine, profile_id)
    denied = stored["metadata"]["denied_concepts"]
    assert [d["concept"] for d in denied] == ["Kubernetes"]
    assert denied[0]["statement"] == "I have never touched Kubernetes."
    history = stored["metadata"]["enrichment_history"]
    assert history[-1]["source"] == "interview"
    assert any(c["field"] == "denied_concepts" for c in history[-1]["changes"])


# ── Door 6: the `send_message` interview turn ─────────────────────────────────


def _interview_session(job_id, profile_id):
    from applire.models.session import InterviewSession

    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_id,
        mode="targeted",
        status="active",
        state={
            "mode": "targeted",
            "job_id": str(job_id),
            "gap_analysis_id": None,
            "profile_id": str(profile_id),
            "critical_gaps": ["Kafka experience", "FastAPI experience"],
            "gap_categories": {"Kafka experience": "C", "FastAPI experience": "C"},
            "gap_clusters_by_id": {
                "Kafka experience": {
                    "id": "Kafka experience", "label": "Kafka experience",
                    "gaps": ["Kafka experience"], "jd_skills": [], "jd_context": "",
                },
                "FastAPI experience": {
                    "id": "FastAPI experience", "label": "FastAPI experience",
                    "gaps": ["FastAPI experience"], "jd_skills": [], "jd_context": "",
                },
            },
            "addressed_gaps": [],
            "current_gap_index": 0,
            "current_question": "Tell me about your Kafka experience.",
            "messages": [
                {"role": "assistant", "content": "Tell me about your Kafka experience."}
            ],
            "questions_asked": 1,
            "hard_ceiling": 12,
            "questions_per_gap": {},
            "skipped_gaps": [],
            "full_gaps": [],
        },
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )


def _make_job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer requiring Kafka and FastAPI.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "Kafka", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )


@pytest.mark.asyncio
async def test_send_message_turn_write_survives_the_request(durable_db, monkeypatch):
    from applire.services.profile.reconcile.ops import ReconcileResult, UpsertSkill
    from applire.services.session import send_message
    import applire.services.profile.reconcile.interview_bridge as ib

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as session:
        job = _make_job()
        session.add(job)
        await session.flush()
        record = _interview_session(job.id, profile_id)
        session.add(record)
        await session.commit()
        session_id = record.id

    async def _one_skill(before, new_info, source, provider, lang="en"):
        return ReconcileResult(
            ops=[UpsertSkill(name="Kafka", category="technical")], denials=[]
        )

    monkeypatch.setattr(ib, "reconcile", _one_skill)

    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(unused)")
    provider.aparse_json = AsyncMock(return_value={})
    async with factory() as request_session:
        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(
                return_value={"question": "Tell me about FastAPI.", "choices": None}
            ),
        ):
            result = await send_message(
                session_id,
                "I ran Kafka in production for three years.",
                request_session,
                provider,
            )

    assert result.complete is False
    stored = await _read_back(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["Kafka", "Python"]
    assert len(stored["metadata"]["enrichment_history"]) == 1


# ── The snapshot policy the parameter exists to express ──────────────────────


@pytest.mark.asyncio
async def test_a_long_interview_never_evicts_the_import_snapshot(durable_db, monkeypatch):
    """ADR-063 amendment (5), the whole reason snapshot coverage is a PARAMETER
    and not an invariant. `SNAPSHOT_MAX_PER_PROFILE` is pruned by recency, so
    if interview turns snapshotted too, a session longer than the cap would
    evict the import snapshot — degrading the guarantee exactly for the case it
    exists to protect. More turns than the cap, and the import stays undoable.
    """
    from applire.constants import SNAPSHOT_MAX_PER_PROFILE
    from applire.models.profile import MasterProfile
    from applire.providers.embedding.noop import NoopEmbeddingProvider
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile import _apply_merge
    from applire.services.profile.reconcile.interview_bridge import (
        reconcile_interview_turn,
    )
    from applire.services.profile.reconcile.ops import ReconcileResult, UpsertSkill
    from applire.services.profile.snapshots import undo_last_merge
    import applire.services.profile.reconcile.interview_bridge as ib

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    before_import = await _read_back(engine, profile_id)

    async with factory() as request_session:
        with (
            patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")),
            patch(
                "applire.services.profile.reconcile_import",
                new=AsyncMock(return_value=_merge_of("Terraform")),
            ),
        ):
            await _apply_merge(
                request_session,
                MasterProfileData.model_validate(
                    {"skills": [{"name": "Terraform", "category": "technical"}]}
                ),
                source="cv_upload",
                emb_provider=NoopEmbeddingProvider(),
                provider=AsyncMock(),
            )

    turns = SNAPSHOT_MAX_PER_PROFILE + 5
    counter = {"n": 0}

    async def _a_skill_per_turn(before, new_info, source, provider, lang="en"):
        counter["n"] += 1
        return ReconcileResult(
            ops=[UpsertSkill(name=f"Skill {counter['n']}", category="technical")],
            denials=[],
        )

    monkeypatch.setattr(ib, "reconcile", _a_skill_per_turn)

    async with factory() as session:
        record = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        for i in range(turns):
            await reconcile_interview_turn(
                session,
                profile_record=record,
                gap="skills",
                question="What else?",
                answer=f"Answer {i}",
                provider=AsyncMock(),
                session_id="s1",
            )
            await session.commit()

    snaps = await _snapshots(engine, profile_id)
    assert len(snaps) == 1, (
        "interview turns must snapshot NOTHING — otherwise a session longer "
        "than SNAPSHOT_MAX_PER_PROFILE evicts the import snapshot (#339)"
    )
    assert snaps[0].profile_json == before_import

    async with factory() as session:
        result = await undo_last_merge(session)
        await session.commit()
    assert result.restored is True
    restored = await _read_back(engine, profile_id)
    assert restored == before_import
