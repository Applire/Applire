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

"""#615 (ADR-063 amended 2026-08-28, second entry / ADR-058 note) — one door-
level "does the serialised artefact carry the fact" test per import adapter.

The carried-predicate itself is proven against real captured/replayed data in
``test_615_import_witness.py``; this file proves the PLUMBING — that a
``MergeResult.not_applied`` computed inside ``reconcile_import`` actually
reaches each of the six doors' own response shape, unchanged, through
``_apply_merge`` / ``_import_from_text`` / ``_profile_summary``.

Every test patches ``applire.services.profile.reconcile_import`` (the name
bound in that module's namespace) to a stand-in returning a fully controlled
``MergeResult`` — the same technique ``test_commit_ops_pr2_door_writes.py``'s
``_merge_of`` helper uses — so each test isolates the ADAPTER's own mapping
logic instead of re-driving the whole extraction/reconcile pipeline six times.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import (
    FieldChange,
    ImportNotApplied,
    MasterProfileData,
    Skill,
)
from applire.services.profile.merge import MergeResult


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session — mirrors tests/unit/test_cv_upload.py's fixture."""
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                    UploadRecord.__table__,
                    User.__table__,
                    UserSettings.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _partial_merge_result() -> MergeResult:
    """A controlled `reconcile_import` stand-in: one skill landed, one did not."""
    merged = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Katrin Hoffmann"},
            "work_experience": [
                {"company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
                 "start_date": "2019-02"}
            ],
            "skills": [{"name": "Python", "category": "technical", "status": "confirmed"}],
        }
    )
    return MergeResult(
        merged_profile=merged,
        added=["Python"],
        changes=[FieldChange(section="skills", field="name", action="added", new_value="Python")],
        reconciliation={"skills": {"extracted": 2, "stored": 1, "delta": 1}},
        not_applied=[ImportNotApplied(section="skills", label="Kubernetes", reason="no_op_carried_entry")],
    )


async def _seed_first_profile(sqlite_session) -> None:
    """A minimal existing profile so the SECOND door call takes the merge
    branch (never the first-import creation branch)."""
    from applire.models.profile import MasterProfile, authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(
            profile_json={
                "personal_info": {"name": "Katrin Hoffmann"},
                "skills": [{"name": "Python", "category": "technical", "status": "confirmed"}],
                "metadata": {},
            }
        )
    sqlite_session.add(record)
    await sqlite_session.commit()


# ---------------------------------------------------------------------------
# 1. Sync upload — POST /api/profile/upload → CVUploadResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_upload_door_carries_merge_status_and_not_applied(sqlite_session, tmp_path):
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider

    await _seed_first_profile(sqlite_session)
    storage = LocalStorageProvider(str(tmp_path))
    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"

    with (
        patch("applire.services.cv_parser.extract_text", new=AsyncMock(return_value="Katrin Hoffmann")),
        patch("applire.services.profile.extract_with_fallback",
              new=AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"},
                                           "skills": [{"name": "Kubernetes"}]})),
        patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
        patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())),
    ):
        response = await upload_cv(
            file_bytes=b"cv-bytes",
            filename="cv.pdf",
            content_type="application/pdf",
            db=sqlite_session,
            provider=mock_provider,
            storage=storage,
            ocr_extractor=AsyncMock(),
        )

    assert response.merge_status == "partial"
    assert len(response.not_applied) == 1
    assert response.not_applied[0].section == "skills"
    assert response.not_applied[0].label == "Kubernetes"
    # Serialised artefact — the shape an API caller actually receives.
    dumped = response.model_dump(mode="json")
    assert dumped["merge_status"] == "partial"
    assert dumped["not_applied"][0]["reason"] == "no_op_carried_entry"


def test_old_enrichment_record_without_not_applied_loads_unchanged():
    """A receipt persisted before #615 has no `not_applied` key at all —
    EnrichmentRecord carries no `extra=\"forbid\"` (refuter A, C2/(ii)), so it
    must load with the new field defaulting to `[]`, not raise."""
    from applire.schemas.profile import EnrichmentRecord

    old_receipt = {
        "id": "rec-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "cv_upload",
        "changes": [],
        "reconciliation": {"work_experience": {"extracted": 1, "stored": 1, "delta": 0}},
        # no "not_applied" key at all — the pre-#615 shape
    }
    record = EnrichmentRecord.model_validate(old_receipt)
    assert record.not_applied == []
    assert record.reconciliation == {"work_experience": {"extracted": 1, "stored": 1, "delta": 0}}


# ---------------------------------------------------------------------------
# 2. The async job's result — GET /api/profile/import-jobs/{id}
# ---------------------------------------------------------------------------


def test_async_import_job_result_round_trip_preserves_the_fact():
    """import_jobs.py stores `result.model_dump(mode="json")` on the job row and
    the status endpoint rebuilds `CVUploadResponse.model_validate(job.result)` —
    no adapter of its own; this proves that round-trip is lossless for #615's
    two new fields."""
    from applire.schemas.profile import CVUploadResponse

    original = CVUploadResponse(
        profile_id=uuid.uuid4(),
        status="COMPLETE",
        completeness_score=0.9,
        expires_at=datetime.now(timezone.utc),
        merge_status="partial",
        not_applied=[ImportNotApplied(section="languages", label="Englisch", reason="no_op_carried_entry")],
    )
    stored = original.model_dump(mode="json")
    reloaded = CVUploadResponse.model_validate(stored)

    assert reloaded.merge_status == "partial"
    assert reloaded.not_applied == original.not_applied


# ---------------------------------------------------------------------------
# 3. Staged resolve — POST /api/profile/staged/{id}/resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staged_resolve_merge_door_carries_merge_status_and_not_applied(sqlite_session):
    from applire.models.uploads import UploadRecord
    from applire.services.profile import resolve_staged_extraction

    await _seed_first_profile(sqlite_session)
    staged = UploadRecord(
        original_filename="cv.pdf",
        content_hash="abc123",
        mime_type="application/pdf",
        file_path="unused",
        byte_size=10,
        gate_status="not_a_cv",
        staged_extraction={"personal_info": {"name": "Katrin Hoffmann"}, "skills": []},
    )
    sqlite_session.add(staged)
    await sqlite_session.commit()
    await sqlite_session.refresh(staged)

    with patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())):
        response = await resolve_staged_extraction(
            sqlite_session, staged.id, action="merge", provider=AsyncMock(),
        )

    assert response.merge_status == "partial"
    assert response.not_applied == [
        ImportNotApplied(section="skills", label="Kubernetes", reason="no_op_carried_entry")
    ]


@pytest.mark.asyncio
async def test_staged_resolve_discard_door_defaults_to_applied_empty(sqlite_session):
    """A discard resolves nothing — "applied, []" (the defaults) is the honest
    fact, not a special case that needs its own branch."""
    from applire.models.uploads import UploadRecord
    from applire.services.profile import resolve_staged_extraction

    staged = UploadRecord(
        original_filename="cv.pdf", content_hash="abc123", mime_type="application/pdf",
        file_path="unused", byte_size=10, gate_status="not_a_cv",
        staged_extraction={"personal_info": {"name": "X"}},
    )
    sqlite_session.add(staged)
    await sqlite_session.commit()
    await sqlite_session.refresh(staged)

    response = await resolve_staged_extraction(sqlite_session, staged.id, action="discard")

    assert response.merge_status == "applied"
    assert response.not_applied == []


# ---------------------------------------------------------------------------
# 4. POST /api/profile/import (text/paste) and 5. a LinkedIn route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_from_text_door_returns_profile_import_response(sqlite_session):
    from applire.schemas.profile import ProfileImportResponse
    from applire.services.profile import import_from_text

    await _seed_first_profile(sqlite_session)

    with (
        patch("applire.services.profile.extract_with_fallback",
              new=AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"},
                                           "skills": [{"name": "Kubernetes"}]})),
        patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
        patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())),
    ):
        response = await import_from_text("pasted CV text", sqlite_session, AsyncMock())

    assert isinstance(response, ProfileImportResponse)
    assert response.merge_status == "partial"
    assert response.not_applied[0].label == "Kubernetes"
    # #615 / refuter B MAJOR 1 — GET /api/profile and PATCH /{section} share
    # the PARENT class, which never gained this field (a separate builder,
    # `_to_import_response`, is the only thing that constructs the subclass).
    from applire.schemas.profile import MasterProfileResponse

    assert "merge_status" not in MasterProfileResponse.model_fields
    assert "not_applied" not in MasterProfileResponse.model_fields


@pytest.mark.asyncio
async def test_linkedin_import_route_returns_profile_import_response(sqlite_session):
    from applire.schemas.profile import ProfileImportResponse
    from applire.services.profile import import_from_linkedin

    await _seed_first_profile(sqlite_session)

    with (
        patch("applire.services.profile.extract_with_fallback",
              new=AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"},
                                           "skills": [{"name": "Kubernetes"}]})),
        patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
        patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())),
    ):
        response = await import_from_linkedin({"name": "Katrin Hoffmann"}, sqlite_session, AsyncMock())

    assert isinstance(response, ProfileImportResponse)
    assert response.merge_status == "partial"
    assert response.not_applied[0].section == "skills"


@pytest.mark.asyncio
async def test_first_import_via_text_door_is_applied_empty(sqlite_session):
    """No existing profile — nothing to reconcile against; #615's "applied, []"
    default for a first-profile creation, not a special case."""
    from applire.services.profile import import_from_text

    with (
        patch("applire.services.profile.extract_with_fallback",
              new=AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"}})),
        patch("applire.services.profile.review_and_refine", new=AsyncMock(side_effect=lambda **kw: kw["draft"])),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
    ):
        response = await import_from_text("first CV text", sqlite_session, AsyncMock())

    assert response.merge_status == "applied"
    assert response.not_applied == []


# ---------------------------------------------------------------------------
# 6. MCP import_cv — _profile_summary
# ---------------------------------------------------------------------------


def test_mcp_profile_summary_reports_merge_status_and_not_applied():
    from applire.mcp.server import _profile_summary
    from applire.schemas.profile import ProfileImportResponse, ProfileStats

    response = ProfileImportResponse(
        id=uuid.uuid4(),
        profile=MasterProfileData(),
        completeness=0.7,
        stats=ProfileStats(positions=1),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        merge_status="partial",
        not_applied=[ImportNotApplied(section="languages", label="Englisch", reason="no_op_carried_entry")],
    )
    summary = _profile_summary(response)

    # The five pre-existing fields stay — #615 only adds to this dict.
    assert set(summary) == {
        "profile_id", "positions", "skills_count", "completeness", "merge_conflicts",
        "merge_status", "not_applied",
    }
    assert summary["merge_status"] == "partial"
    assert summary["not_applied"] == [
        {"section": "languages", "label": "Englisch", "reason": "no_op_carried_entry"}
    ]
