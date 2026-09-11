# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#58 / #581 (Nougat build 2, adversarial pass adv-agent-letter).

New attack-surface coverage the shipped unit suites
(`tests/unit/test_mcp_profile_health.py`, `backend/tests/unit/test_676_flow_advance_artifact_not_found.py`)
did not exercise:

* `resolve_held_merge` / `undo_last_merge` given a well-formed UUID that
  belongs to a DIFFERENT table — never a table each function's own query
  scans — rather than an id that matches nothing at all.
* `undo_last_merge`'s `discarded_later_edits` warning after a MANUAL field
  edit (`update_profile` / `patch_profile_section`), not just after a second
  merge. `commit_ops` mints an `enrichment_history` entry unconditionally
  (`services/profile/commit.py:675`) for every writer including
  `patch_profile_section`, so the head id changes on a manual edit too — this
  pins that the claim in the tool's own description ("Tell the user when
  discarded_later_edits is true") is actually true for THIS shape, not only
  for the merge-vs-merge shape the shipped tests cover.
* the MCP door of #581's fix (`advance_flow`) given an id from the WRONG
  table for the step, mirroring the REST-door regression added alongside
  this file to `backend/tests/unit/test_676_flow_advance_artifact_not_found.py`.

All three are REFUTED hypotheses — the existing controls hold under each of
these probes — pinned here so they stay true.
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

from tests.support.profile_factory import make_master_profile


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _patched(session):
    return (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch("applire.mcp.server.get_provider", return_value=MagicMock()),
    )


def _profile_json(name: str = "Stefan Brandt", enrichment_id: str | None = None) -> dict:
    from applire.schemas.profile import MasterProfileData

    raw = {
        "personal_info": {"name": name, "email": "kontakt@applire.de"},
        "work_experience": [], "skills": [], "education": [],
    }
    data = MasterProfileData.model_validate(raw).model_dump(mode="json")
    if enrichment_id is not None:
        meta = data.get("metadata") or {}
        meta["enrichment_history"] = [
            {"id": enrichment_id, "source": "cv_upload", "changes": [],
             "timestamp": "2026-09-01T00:00:00Z"}
        ]
        data["metadata"] = meta
    return data


async def _seed_user(db) -> uuid.UUID:
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="kontakt@applire.de")
    db.add(user)
    await db.flush()
    return user.id


# ---------------------------------------------------------------------------
# resolve_held_merge / undo_last_merge — an id from the WRONG table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_held_merge_with_a_user_id_is_not_found_not_a_crash(db):
    """staged_id looks exactly like a valid UUID and IS a real row's id — just
    in `users`, not `upload_records`. `resolve_staged_extraction`'s query
    scans `UploadRecord` alone, so this must degrade to the same
    `StagedExtractionNotFound` path as a wholly unknown id, never raise
    unhandled or silently touch the wrong table."""
    from applire.mcp.server import resolve_held_merge

    uid = await _seed_user(db)
    await db.commit()

    p_db, p_prov = _patched(db)
    with p_db, p_prov, pytest.raises(McpError) as exc:
        await resolve_held_merge(str(uid), "discard")
    assert "get_profile_health" in str(exc.value)


# ---------------------------------------------------------------------------
# undo_last_merge — discarded_later_edits after a MANUAL edit, not a merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_after_a_manual_field_edit_warns_and_the_edit_is_really_lost(db):
    """The existing suite only proves the flag after a SECOND MERGE changed the
    enrichment head. A manual `update_profile` write goes through the same
    `commit_ops` committer (`patch_profile_section` -> `commit_ops`), which
    mints an unconditional enrichment_history entry — so the flag must ALSO
    fire here, and the restore must ACTUALLY discard the manual edit (the
    profile_json is a wholesale overwrite from the snapshot), not merely flag
    a mismatched id while the edit secretly survives."""
    from applire.mcp.server import undo_last_merge
    from applire.models.profile import ProfileSnapshot
    from applire.services.profile import patch_profile_section

    merge_id = str(uuid.uuid4())
    await _seed_user(db)
    profile = make_master_profile(
        profile_json=_profile_json("Stefan Brandt", enrichment_id=merge_id)
    )
    db.add(profile)
    await db.flush()
    pre_merge = _profile_json("OLD NAME BEFORE THE MERGE")
    db.add(
        ProfileSnapshot(
            id=uuid.uuid4(), profile_id=profile.id, enrichment_record_id=merge_id,
            profile_json=pre_merge, created_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    # A MANUAL edit after the merge — not another merge.
    await patch_profile_section(
        "personal_info", {"name": "Stefan Brandt EDITED", "email": "kontakt@applire.de"}, db,
    )
    await db.refresh(profile)
    assert profile.profile_json["personal_info"]["name"] == "Stefan Brandt EDITED"

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await undo_last_merge()

    assert out == {"restored": True, "discarded_later_edits": True}, (
        "a manual edit after the merge must warn exactly like a second merge would"
    )
    await db.refresh(profile)
    # The claim behind the warning must be TRUE, not just the flag: the manual
    # edit is genuinely gone, reverted to the pre-merge snapshot.
    assert profile.profile_json["personal_info"]["name"] == "OLD NAME BEFORE THE MERGE"


# ---------------------------------------------------------------------------
# advance_flow (MCP door) — an id from the WRONG table for the step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_advance_flow_with_wrong_table_id_is_invalid_input_not_a_crash(db):
    """Mirrors the REST-door regression added to
    `backend/tests/unit/test_676_flow_advance_artifact_not_found.py`: a real
    GeneratedCV id, passed as the artifact_id for the `gap_analysis` step
    (which resolves against GapAnalysis), must reach the caller as a typed
    invalid-input error on the MCP door too — never an unhandled
    IntegrityError from db.commit()."""
    from applire.mcp.server import advance_flow, start_flow
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis

    await _seed_user(db)
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=f"h-{uuid.uuid4()}", raw_text="JD",
        role_title="Software Engineer", seniority_level="mid",
        language_requirement="English",
    )
    profile = make_master_profile(profile_json=_profile_json())
    db.add_all([job, profile])
    await db.flush()
    cv = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={})
    db.add(cv)
    await db.commit()
    await db.refresh(cv)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        flow = await start_flow(job_id=str(job.id))
        flow_id = flow["flow_id"]
        with pytest.raises(McpError) as exc:
            await advance_flow(flow_id, "gap_analysis", str(cv.id))
    msg = str(exc.value)
    assert str(cv.id) in msg
    assert "gap_analysis" in msg
