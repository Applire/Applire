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

"""US167 (E033 / ADR-041 amended) — pre-merge gate HOLDS the merge.

These service-level tests drive the upload→merge path: when the deterministic
gate fires (name divergence / not-a-CV), the additive merge must be HELD before
it commits (safe default = don't merge, ADR-013/037), the staged extraction
parked on the upload row (7-day TTL, ADR-005), and the user given a resolve
choice. A clean second CV must still merge with no added friction.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings  # US184: get_ui_language

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,  # US168: _apply_merge snapshots pre-merge
                    UploadRecord.__table__,
                    User.__table__,
                    # US184: import paths now call get_ui_language(db) → needs the
                    # user_settings table so the engine path runs end-to-end.
                    UserSettings.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _cv_dict(name: str, *, company: str = "Acme GmbH", with_content: bool = True) -> dict:
    data: dict = {"personal_info": {"name": name}}
    if with_content:
        data["work_experience"] = [
            {"company": company, "role": "Engineer", "start_date": "2020-01"}
        ]
        data["skills"] = [{"name": "Python", "category": "technical"}]
    return data


async def _upload(session, storage, name_or_dict, *, company="Acme GmbH", with_content=True, user_id=None):
    """Run upload_cv with a mocked provider returning the given extraction."""
    from applire.services.profile import upload_cv

    data = (
        name_or_dict
        if isinstance(name_or_dict, dict)
        else _cv_dict(name_or_dict, company=company, with_content=with_content)
    )
    provider = AsyncMock()
    provider.__class__.__name__ = "MockProvider"
    provider.aparse_json.return_value = data

    with patch(
        "applire.services.cv_parser.extract_text",
        new=AsyncMock(return_value="raw cv text"),
    ), patch(
        "applire.services.profile.review_and_refine",
        new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
    ), patch(
        "applire.services.profile.enrich_skills",
        new=AsyncMock(side_effect=lambda p, _: p),
    ):
        return await upload_cv(
            file_bytes=b"fake-pdf",
            filename="cv.pdf",
            content_type="application/pdf",
            db=session,
            provider=provider,
            storage=storage,
            ocr_extractor=AsyncMock(),
            user_id=user_id,
        )


@pytest_asyncio.fixture
def storage(tmp_path):
    from applire.storage.local import LocalStorageProvider

    return LocalStorageProvider(str(tmp_path))


async def _latest_profile(session):
    from applire.models.profile import MasterProfile

    rows = (await session.execute(select(MasterProfile))).scalars().all()
    return rows[-1] if rows else None


class TestGateHoldsMerge:
    @pytest.mark.asyncio
    async def test_name_divergence_holds_the_merge(self, sqlite_session, storage):
        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        before = await _latest_profile(sqlite_session)

        resp = await _upload(sqlite_session, storage, "Marcus Weber", company="SAP")

        assert resp.status == "GATED"
        assert resp.gate == "name_divergence"
        assert resp.account_name == "Anna Schmidt"
        assert resp.cv_name == "Marcus Weber"
        assert resp.staged_id is not None
        # Profile is untouched — Marcus's CV did NOT merge into Anna's profile.
        after = await _latest_profile(sqlite_session)
        assert after.profile_json == before.profile_json

    @pytest.mark.asyncio
    async def test_not_a_cv_holds_the_merge(self, sqlite_session, storage):
        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        before = await _latest_profile(sqlite_session)

        garbage = {"personal_info": {"name": "Acme Product Manual"}}
        resp = await _upload(sqlite_session, storage, garbage)

        assert resp.status == "GATED"
        assert resp.gate == "not_a_cv"
        assert resp.staged_id is not None
        after = await _latest_profile(sqlite_session)
        assert after.profile_json == before.profile_json

    @pytest.mark.asyncio
    async def test_clean_second_cv_still_merges(self, sqlite_session, storage):
        """A matching-name real CV merges with no gate (no added friction)."""
        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        resp = await _upload(sqlite_session, storage, "Anna Schmidt", company="Audi")

        assert resp.gate == "none"
        assert resp.status in ("DRAFT", "COMPLETE")


class TestParkingStagedExtraction:
    @pytest.mark.asyncio
    async def test_gated_upload_parks_staged_extraction(self, sqlite_session, storage):
        from applire.models.uploads import UploadRecord

        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        resp = await _upload(sqlite_session, storage, "Marcus Weber", company="SAP")

        rec = (
            await sqlite_session.execute(
                select(UploadRecord).where(UploadRecord.id == resp.staged_id)
            )
        ).scalar_one()
        assert rec.gate_status == "name_divergence"
        assert rec.staged_extraction["personal_info"]["name"] == "Marcus Weber"
        # 7-day upload TTL still applies to the parked item (ADR-005).
        assert 6 <= (rec.expires_at - rec.created_at).days <= 8


class TestResolveStagedExtraction:
    @pytest.mark.asyncio
    async def test_resolve_merge_applies_staged_extraction(self, sqlite_session, storage):
        from applire.services.profile import resolve_staged_extraction

        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        gated = await _upload(sqlite_session, storage, "Marcus Weber", company="SAP")

        result = await resolve_staged_extraction(
            sqlite_session, gated.staged_id, action="merge"
        )

        assert result.action == "merge"
        after = await _latest_profile(sqlite_session)
        companies = {w["company"] for w in after.profile_json["work_experience"]}
        # Additive merge: the staged CV's company is now present alongside BMW.
        assert "SAP" in companies
        assert "BMW" in companies

    @pytest.mark.asyncio
    async def test_resolve_discard_leaves_profile_untouched(self, sqlite_session, storage):
        from applire.models.uploads import UploadRecord
        from applire.services.profile import resolve_staged_extraction

        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        before = await _latest_profile(sqlite_session)
        gated = await _upload(sqlite_session, storage, "Marcus Weber", company="SAP")

        result = await resolve_staged_extraction(
            sqlite_session, gated.staged_id, action="discard"
        )

        assert result.action == "discard"
        after = await _latest_profile(sqlite_session)
        assert after.profile_json == before.profile_json
        rec = (
            await sqlite_session.execute(
                select(UploadRecord).where(UploadRecord.id == gated.staged_id)
            )
        ).scalar_one()
        assert rec.gate_status == "resolved_discarded"

    @pytest.mark.asyncio
    async def test_resolve_is_scoped_to_owner(self, sqlite_session, storage):
        """IDOR guard: a foreign user cannot resolve someone else's parked CV."""
        from applire.services.profile import (
            StagedExtractionNotFound,
            resolve_staged_extraction,
        )

        owner = uuid.uuid4()
        attacker = uuid.uuid4()
        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW", user_id=owner)
        gated = await _upload(
            sqlite_session, storage, "Marcus Weber", company="SAP", user_id=owner
        )

        with pytest.raises(StagedExtractionNotFound):
            await resolve_staged_extraction(
                sqlite_session, gated.staged_id, action="merge", user_id=attacker
            )

    @pytest.mark.asyncio
    async def test_resolve_unknown_id_raises(self, sqlite_session, storage):
        from applire.services.profile import (
            StagedExtractionNotFound,
            resolve_staged_extraction,
        )

        with pytest.raises(StagedExtractionNotFound):
            await resolve_staged_extraction(
                sqlite_session, uuid.uuid4(), action="merge"
            )

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_raises(self, sqlite_session, storage):
        from applire.services.profile import (
            StagedExtractionAlreadyResolved,
            resolve_staged_extraction,
        )

        await _upload(sqlite_session, storage, "Anna Schmidt", company="BMW")
        gated = await _upload(sqlite_session, storage, "Marcus Weber", company="SAP")
        await resolve_staged_extraction(sqlite_session, gated.staged_id, action="discard")

        with pytest.raises(StagedExtractionAlreadyResolved):
            await resolve_staged_extraction(
                sqlite_session, gated.staged_id, action="merge"
            )
