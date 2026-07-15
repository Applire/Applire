# Copyright (C) 2026 Tobias Rosenbaum
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
tests/unit/test_cv_list_read_model.py

E041/US232 — CV list read model gains `template` + `created_at` so the
documents zone can render "Version of <date> · <template>" per row.

Run:
    PYTHONPATH=backend python3 -m pytest tests/unit/test_cv_list_read_model.py -v
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Make the applire package importable
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.session
    import applire.models.application
    import applire.models.cover_letter
    import applire.models.flow
    import applire.models.uploads

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_cv(
    job_id: uuid.UUID,
    status: str = "ready",
    offset_seconds: int = 0,
    target_pages: int | None = None,
):
    from applire.models.cv import GeneratedCV
    return GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        tailored_data={},
        template="continental",
        status=status,
        created_at=datetime.now(timezone.utc) + timedelta(seconds=offset_seconds),
        target_pages=target_pages,
    )


@pytest.mark.asyncio
async def test_list_cvs_for_job_carries_template_and_created_at(db):
    from applire.services.cv import list_cvs_for_job

    job_id = uuid.uuid4()
    older = _make_cv(job_id, status="ready", offset_seconds=0)
    newer = _make_cv(job_id, status="generating", offset_seconds=10)
    db.add(older)
    db.add(newer)
    await db.commit()

    result = await list_cvs_for_job(job_id, db, "http://localhost:8001")

    assert len(result) == 2
    # newest first
    assert result[0].cv_id == newer.id
    assert result[1].cv_id == older.id

    for response_item, orm_row in ((result[0], newer), (result[1], older)):
        assert response_item.template == orm_row.template == "continental"
        assert response_item.created_at == orm_row.created_at


@pytest.mark.asyncio
async def test_cv_status_responses_carry_target_pages(db):
    # Whole-branch review Finding 2: the header re-tailor button reads
    # target_pages off the newest READY CV's status response when no
    # stale_cv is present. CVStatusResponse must round-trip the column
    # from both GET /api/cv/{id}/status and GET /api/jobs/{id}/cvs.
    from applire.services.cv import get_cv_status, list_cvs_for_job

    job_id = uuid.uuid4()
    cv = _make_cv(job_id, status="ready", target_pages=3)
    db.add(cv)
    await db.commit()

    status_result = await get_cv_status(cv.id, db, "http://localhost:8001")
    assert status_result.target_pages == 3

    list_result = await list_cvs_for_job(job_id, db, "http://localhost:8001")
    assert list_result[0].target_pages == 3


@pytest.mark.asyncio
async def test_cv_status_responses_carry_null_target_pages(db):
    # NULL (region-standard / no override) must round-trip as None too.
    from applire.services.cv import get_cv_status

    job_id = uuid.uuid4()
    cv = _make_cv(job_id, status="ready", target_pages=None)
    db.add(cv)
    await db.commit()

    status_result = await get_cv_status(cv.id, db, "http://localhost:8001")
    assert status_result.target_pages is None
