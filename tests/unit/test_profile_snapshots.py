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

"""
US168 (E033 / ADR-042) — Master Profile snapshot + undo last merge.

Before every merge commit, the pre-merge ``profile_json`` is snapshotted into
``profile_snapshots`` (unconditional). ``undo_last_merge`` restores the most
recent snapshot — recovering from an accidental bad merge — clears the conflicts
that merge introduced, and warns if later edits would be discarded. Idempotent.
"""
import sys
import uuid
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from applire import constants  # noqa: E402
from applire.models.profile import MasterProfile, ProfileSnapshot  # noqa: E402
from applire.schemas.profile import (  # noqa: E402
    Conflict,
    EnrichmentRecord,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.profile.snapshots import (  # noqa: E402
    capture_pre_merge_snapshot,
    undo_last_merge,
)
from tests.support.profile_factory import make_master_profile, set_profile_json  # noqa: E402


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base  # noqa: F401
    # Register every model so create_all can resolve all cross-table FKs.
    import importlib
    import pkgutil

    import applire.models as _models_pkg

    for _m in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"applire.models.{_m.name}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _profile_json(*, head_enrichment_id: str, conflicts: int = 0, marker: str = "") -> dict:
    """A minimal profile JSON whose enrichment head + conflict count we control."""
    data = MasterProfileData(
        skills=[],
        metadata=ProfileMetadata(
            enrichment_history=[
                EnrichmentRecord(timestamp=__import__("datetime").datetime.now(), source="cv_upload", id=head_enrichment_id)
            ],
            pending_conflicts=[
                Conflict(section="work_experience", field="start_date", existing_value=str(i), incoming_value="x", source="cv")
                for i in range(conflicts)
            ],
        ),
    )
    blob = data.model_dump(mode="json")
    blob["_marker"] = marker  # round-trip witness
    return blob


async def _seed_profile(session, profile_json: dict) -> MasterProfile:
    record = make_master_profile(profile_json=profile_json)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def _snapshot_count(session, profile_id) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(ProfileSnapshot).where(
                ProfileSnapshot.profile_id == profile_id
            )
        )
    ).scalar_one()


# ─── capture ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_stores_the_pre_merge_json_keyed_to_enrichment(sqlite_session):
    profile = await _seed_profile(sqlite_session, _profile_json(head_enrichment_id="E0", marker="v1"))
    enrich_id = str(uuid.uuid4())

    await capture_pre_merge_snapshot(
        sqlite_session,
        profile_id=profile.id,
        profile_json=profile.profile_json,
        enrichment_record_id=enrich_id,
    )
    await sqlite_session.commit()

    snap = (await sqlite_session.execute(select(ProfileSnapshot))).scalar_one()
    assert snap.profile_id == profile.id
    assert str(snap.enrichment_record_id) == enrich_id
    assert snap.profile_json["_marker"] == "v1"


@pytest.mark.asyncio
async def test_capture_bounds_snapshots_per_profile(sqlite_session, monkeypatch):
    monkeypatch.setattr(constants, "SNAPSHOT_MAX_PER_PROFILE", 3)
    profile = await _seed_profile(sqlite_session, _profile_json(head_enrichment_id="E0"))

    for n in range(5):
        await capture_pre_merge_snapshot(
            sqlite_session,
            profile_id=profile.id,
            profile_json={"_marker": f"v{n}"},
            enrichment_record_id=str(uuid.uuid4()),
        )
        await sqlite_session.commit()

    assert await _snapshot_count(sqlite_session, profile.id) == 3
    # the three most-recent survive
    markers = {
        s.profile_json["_marker"]
        for s in (await sqlite_session.execute(select(ProfileSnapshot))).scalars()
    }
    assert markers == {"v2", "v3", "v4"}


# ─── undo: nothing to do ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_undo_with_no_profile_is_a_noop(sqlite_session):
    result = await undo_last_merge(sqlite_session)
    assert result.restored is False
    assert result.discarded_later_edits is False


@pytest.mark.asyncio
async def test_undo_with_no_snapshot_is_a_noop(sqlite_session):
    await _seed_profile(sqlite_session, _profile_json(head_enrichment_id="E0"))
    result = await undo_last_merge(sqlite_session)
    assert result.restored is False


# ─── undo: merge → undo round-trip ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_undo_restores_pre_merge_state_and_clears_introduced_conflicts(sqlite_session):
    # Pre-merge state: clean profile, no conflicts.
    pre = _profile_json(head_enrichment_id="E0", conflicts=0, marker="pre")
    profile = await _seed_profile(sqlite_session, pre)

    # The merge: snapshot the pre state, then overwrite with a post state that
    # introduced a conflict and whose head enrichment is the merge M (E1).
    merge_id = "E1"
    await capture_pre_merge_snapshot(
        sqlite_session, profile_id=profile.id, profile_json=pre, enrichment_record_id=merge_id
    )
    set_profile_json(profile, _profile_json(head_enrichment_id=merge_id, conflicts=1, marker="post"))
    await sqlite_session.commit()

    result = await undo_last_merge(sqlite_session)

    assert result.restored is True
    assert result.discarded_later_edits is False  # M was still the head
    await sqlite_session.refresh(profile)
    assert profile.profile_json["_marker"] == "pre"
    assert profile.profile_json["metadata"]["pending_conflicts"] == []


@pytest.mark.asyncio
async def test_undo_warns_when_later_edits_would_be_discarded(sqlite_session):
    pre = _profile_json(head_enrichment_id="E0", marker="pre")
    profile = await _seed_profile(sqlite_session, pre)
    merge_id = "E1"
    await capture_pre_merge_snapshot(
        sqlite_session, profile_id=profile.id, profile_json=pre, enrichment_record_id=merge_id
    )
    # After the merge, a LATER edit (E2) became the head — undoing discards it.
    set_profile_json(profile, _profile_json(head_enrichment_id="E2", marker="post+edit"))
    await sqlite_session.commit()

    result = await undo_last_merge(sqlite_session)

    assert result.restored is True
    assert result.discarded_later_edits is True


@pytest.mark.asyncio
async def test_undo_is_idempotent(sqlite_session):
    pre = _profile_json(head_enrichment_id="E0", marker="pre")
    profile = await _seed_profile(sqlite_session, pre)
    await capture_pre_merge_snapshot(
        sqlite_session, profile_id=profile.id, profile_json=pre, enrichment_record_id="E1"
    )
    set_profile_json(profile, _profile_json(head_enrichment_id="E1", marker="post"))
    await sqlite_session.commit()

    first = await undo_last_merge(sqlite_session)
    await sqlite_session.refresh(profile)
    json_after_first = dict(profile.profile_json)

    second = await undo_last_merge(sqlite_session)
    await sqlite_session.refresh(profile)

    assert first.restored is True
    assert second.restored is False  # nothing left to undo
    assert profile.profile_json == json_after_first  # state unchanged
    assert await _snapshot_count(sqlite_session, profile.id) == 0
