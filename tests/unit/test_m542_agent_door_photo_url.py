# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""M5.4.2 (1) at the AGENT DOOR — `render_document` answers a field-path
validation error for a caller-supplied `header.photo_url` (ruling L-3).

`LetterHeader.photo_url` is gone (M5.4.2 (1)), so the door's behaviour for that
key changes from *accept and silently set to None* to *reject, naming the field*.
That is what `schemas/cover_letter.py`'s own module docstring says `extra="forbid"`
is there for — *"an agent typo must surface as a field-path validation error,
never a silently dropped section"* — and it matches `PATCH /api/profile/{section}`,
which already refuses `photo_url` (`routers/signature.py:13-14`).

The security property is unchanged and better placed: a caller-supplied file path
cannot reach `storage.read` because the SCHEMA will not carry it, rather than
because an assignment overwrote it after validation.

Written in the Agent-PQ stdio tier's pattern but verified IN-PROCESS with the real
MCP tool function under `sqlite+aiosqlite` — the repo-root `tests/test_mcp_*.py`
files are never run from a worktree (their conftest drives `docker compose`).
`mcp/server.py` itself is another package's file and is only imported here.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# JSON-RPC "invalid params" — `applire/mcp/errors.py::invalid_input`, the code the
# door raises for a Pydantic error. The HTTP twin of this door answers 422.
INVALID_INPUT = -32602

PROFILE_JSON = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"de": "Backend-Entwicklerin."},
    "work_experience": [
        {
            "company": "Acme GmbH",
            "role": "Backend Engineer",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "achievements": ["Ein Team von 12 Personen geführt."],
        }
    ],
    "skills": [{"name": "Python"}],
}

LETTER_CONTENT = {
    "header": {"name": "Anna Bauer", "address": "Hauptstraße 42, 10115 Berlin"},
    "recipient": {"name": "Frau Schmidt", "company": "TechVision GmbH"},
    "body": {"paragraphs": ["Sehr geehrte Frau Schmidt,", "Hauptteil.", "Schluss."]},
    "signature": {"name": "Anna Bauer"},
}


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

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


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis

    from tests.support.profile_factory import make_master_profile

    job_id = uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id,
            raw_text_hash=f"m542-door-{uuid.uuid4().hex[:8]}",
            raw_text="Backend Engineer job",
            role_title="Backend Engineer",
            required_skills=["Python"],
            nice_to_have_skills=[],
            keywords=["Python"],
            seniority_level="senior",
            company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=uuid.uuid4(),
            profile_json=PROFILE_JSON,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return db, job_id


@pytest.mark.asyncio
async def test_render_document_rejects_a_letter_header_photo_url_by_field_path(seeded):
    """The door's answer names the field, so the caller's agent can fix its own
    payload without guessing — the whole reason `extra="forbid"` is on every
    `LetterData` section."""
    from applire.mcp.server import render_document

    session, job_id = seeded
    content = {
        **LETTER_CONTENT,
        "header": {
            **LETTER_CONTENT["header"],
            "photo_url": "/etc/passwd",  # hostile, and now unrepresentable
        },
    }

    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        with pytest.raises(McpError) as exc_info:
            await render_document(
                document_kind="cover_letter",
                content=content,
                job_id=str(job_id),
            )

    err = exc_info.value.error
    assert err.code == INVALID_INPUT, err
    assert "photo_url" in err.message, err.message
    assert "header" in err.message, err.message
    assert "extra_forbidden" in err.message or "not permitted" in err.message, err.message


@pytest.mark.asyncio
async def test_no_letter_row_is_persisted_when_the_door_rejects_the_payload(seeded):
    """The rejection happens at `LetterData.model_validate`, before anything is
    written — so a caller-supplied file path never reaches storage OR the DB."""
    from sqlalchemy import select

    from applire.mcp.server import render_document
    from applire.models.cover_letter import GeneratedCoverLetter

    session, job_id = seeded
    content = {
        **LETTER_CONTENT,
        "header": {**LETTER_CONTENT["header"], "photo_url": "uploads/photos/x.jpg"},
    }

    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        with pytest.raises(McpError):
            await render_document(
                document_kind="cover_letter", content=content, job_id=str(job_id)
            )

    rows = (await session.execute(select(GeneratedCoverLetter))).scalars().all()
    assert rows == [], "a rejected payload must not leave a letter row behind"


@pytest.mark.asyncio
async def test_the_same_payload_without_photo_url_is_accepted(seeded):
    """The rejection is scoped to the removed field and nothing else: the door
    still renders the caller's letter."""
    from applire.mcp.server import render_document

    session, job_id = seeded

    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch("applire.services.cover_letter.get_cover_letter_html",
              new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cover_letter_pdf.render_pdf",
              new=AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              return_value=("Anna Bauer", 1)),
    ):
        result = await render_document(
            document_kind="cover_letter",
            content=dict(LETTER_CONTENT),
            job_id=str(job_id),
        )

    assert result["document_kind"] == "cover_letter"
    assert result["status"] == "ready"
