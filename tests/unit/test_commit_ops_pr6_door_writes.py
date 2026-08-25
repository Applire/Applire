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

"""#480 PR 6 — the post-hire doors, and inventory row 6's closure.

**§7.6's door-level write test, one per migrated writer.** `commit_ops` flushes
and never commits, so a forgotten `db.commit()` in a migrated caller is a SILENT
no-write. `add_role_to_profile` is reached through two doors — the REST
`POST /api/profile/roles` route and the MCP `add_role` tool — and each is driven
here against a **file-backed** database, re-read over a separate connection.

**Row 6's `compl.` ❌.** The old writer computed `calculate_completeness()` for
its RESPONSE and never wrote it back, so the stored `metadata.completeness_score`
drifted every time someone changed jobs — the profile-health surface reporting a
score for a vault that no longer matched it. Invariant 4 recomputes universally,
so the fix arrives with the routing rather than as a patch. Same for the trail:
the writer kept its own hand-rolled `EnrichmentRecord`, which is now the
committer's (invariant 3), and there must be exactly ONE record per act.
"""
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_OPEN_ROLE = "11111111-1111-1111-1111-111111111111"


def _seed_profile_json() -> dict:
    return {
        "personal_info": {
            "full_name": "Daniel Kovač",
            "email": "daniel@example.invalid",
        },
        "skills": [
            {"name": "Terraform", "category": "technical", "status": "confirmed"}
        ],
        "work_experience": [
            {
                "id": _OPEN_ROLE,
                "company": "Rheinwerk GmbH",
                "role": "Automation Engineer",
                "start_date": "2018-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": ["Ran the build"],
                "achievements": ["Cut deploy time in half"],
                "team_size": 6,
                "industry_context": "Industrial automation",
            }
        ],
        "education": [
            {"institution": "TU Wien", "degree": "MSc", "field": "Informatik"}
        ],
        "metadata": {
            "completeness_score": 0.0,
            "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z",
            "last_updated": "2020-01-01T00:00:00Z",
            "enrichment_history": [],
        },
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


async def _seed(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=_seed_profile_json())
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id: uuid.UUID) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state.

    The writer's request session has already exited by the time this runs, so a
    missing `db.commit()` has been rolled back and this read sees the
    pre-request state. Mutation-verified for both doors.
    """
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        return dict(row.profile_json)


def _mcp_db(factory):
    @asynccontextmanager
    async def _cm():
        async with factory() as session:
            yield session

    return _cm


# ── Door 12: the REST post-hire route ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_add_role_door_write_survives_the_request(durable_db):
    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        response = await add_role(
            AddRoleRequest(
                title="Principal Engineer",
                company="Meridian Systems",
                start_date="2026-06-01",
                location="Wien",
                close_roles=[
                    CloseRoleEntry(role_id=_OPEN_ROLE, end_date="2026-05-31")
                ],
                source="manual",
            ),
            request_session,
            None,
        )

    assert response.new_role_id
    assert response.closed_role_ids == [_OPEN_ROLE]

    stored = await _read_back(engine, profile_id)
    entries = stored["work_experience"]
    # Insert-at-0 survives the routing — array order is what the CV is built from.
    assert entries[0]["id"] == response.new_role_id
    assert entries[0]["company"] == "Meridian Systems"
    assert entries[0]["is_current"] is True
    closed = next(w for w in entries if w["id"] == _OPEN_ROLE)
    assert closed["end_date"] == "2026-05-31"
    assert closed["is_current"] is False


@pytest.mark.asyncio
async def test_the_rest_door_closes_row_6s_missing_completeness_recompute(durable_db):
    """Row 6 carried a ❌ in the `compl.` column: the score was computed for the
    RESPONSE and never written back, so the stored value drifted on every job
    change. Invariant 4 makes it universal."""
    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        response = await add_role(
            AddRoleRequest(
                title="Principal Engineer",
                company="Meridian Systems",
                start_date="2026-06-01",
                close_roles=[],
                source="manual",
            ),
            request_session,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["completeness_score"] != 0.0
    assert stored["metadata"]["completeness_score"] == response.completeness_score


@pytest.mark.asyncio
async def test_the_act_leaves_exactly_one_trail_record(durable_db):
    """Invariant 3 moved the trail into the committer. Exactly one record for
    the whole act — both halves receipted inside it, not one record per half and
    not a hand-rolled duplicate alongside the committer's."""
    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        response = await add_role(
            AddRoleRequest(
                title="Principal Engineer",
                company="Meridian Systems",
                start_date="2026-06-01",
                close_roles=[
                    CloseRoleEntry(role_id=_OPEN_ROLE, end_date="2026-05-31")
                ],
                source="manual",
            ),
            request_session,
            None,
        )

    stored = await _read_back(engine, profile_id)
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual_role_add"
    keys = {c["rationale_key"] for c in history[0]["changes"]}
    assert keys == {"role_added", "role_closed"}
    fields = {c["field"] for c in history[0]["changes"]}
    assert f"[{response.new_role_id}]" in fields
    assert f"[{_OPEN_ROLE}].end_date" in fields
    # Invariant 5 — the clock moved with the write.
    assert stored["metadata"]["last_updated"] != "2020-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_the_rest_door_still_422s_an_unclosable_role(durable_db):
    """The refusals happen in the pure adapter, before anything reaches the
    write path — and the door still maps them the way it always did."""
    from fastapi import HTTPException

    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        with pytest.raises(HTTPException) as exc:
            await add_role(
                AddRoleRequest(
                    title="Principal Engineer",
                    company="Meridian Systems",
                    start_date="2026-06-01",
                    close_roles=[
                        CloseRoleEntry(role_id="does-not-exist", end_date="2026-05-31")
                    ],
                    source="manual",
                ),
                request_session,
                None,
            )

    assert exc.value.status_code == 422
    stored = await _read_back(engine, profile_id)
    assert len(stored["work_experience"]) == 1
    assert stored["metadata"]["enrichment_history"] == []


@pytest.mark.asyncio
async def test_the_rest_door_still_404s_without_a_profile(durable_db):
    from fastapi import HTTPException

    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest

    engine, factory = durable_db

    async with factory() as request_session:
        with pytest.raises(HTTPException) as exc:
            await add_role(
                AddRoleRequest(
                    title="Principal Engineer",
                    company="Meridian Systems",
                    start_date="2026-06-01",
                    close_roles=[],
                    source="manual",
                ),
                request_session,
                None,
            )

    assert exc.value.status_code == 404


# ── Door 13: the MCP `add_role` tool ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_add_role_door_write_survives_the_request(durable_db, monkeypatch):
    import applire.mcp.server as server

    engine, factory = durable_db
    profile_id = await _seed(factory)
    monkeypatch.setattr(server, "get_db", _mcp_db(factory))

    result = await server.add_role(
        title="Principal Engineer",
        company="Meridian Systems",
        start_date="2026-06-01",
        close_role_ids=[_OPEN_ROLE],
    )

    stored = await _read_back(engine, profile_id)
    entries = stored["work_experience"]
    assert entries[0]["id"] == result["new_role_id"]
    assert entries[0]["company"] == "Meridian Systems"
    closed = next(w for w in entries if w["id"] == _OPEN_ROLE)
    # The MCP door closes the prior role the day BEFORE the new start date.
    assert closed["end_date"] == "2026-05-31"
    assert closed["is_current"] is False
    assert len(stored["metadata"]["enrichment_history"]) == 1
    assert stored["metadata"]["completeness_score"] != 0.0


@pytest.mark.asyncio
async def test_both_doors_produce_the_same_vault_state(durable_db, monkeypatch):
    """ADR-058 clause 2 — the same act through the UI door and the agent door
    must leave the same vault. Both were already on one implementation; routing
    it through the committer is what makes the INVARIANTS identical too."""
    import applire.mcp.server as server
    from applire.routers.profile_roles import add_role
    from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry

    engine, factory = durable_db

    rest_profile = await _seed(factory)
    async with factory() as request_session:
        await add_role(
            AddRoleRequest(
                title="Principal Engineer",
                company="Meridian Systems",
                start_date="2026-06-01",
                close_roles=[
                    CloseRoleEntry(role_id=_OPEN_ROLE, end_date="2026-05-31")
                ],
                source="manual",
            ),
            request_session,
            None,
        )
    rest_state = await _read_back(engine, rest_profile)

    # A second, independent profile for the agent door.
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=_seed_profile_json())
        session.add(record)
        await session.commit()
        mcp_profile = record.id

    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    await server.add_role(
        title="Principal Engineer",
        company="Meridian Systems",
        start_date="2026-06-01",
        close_role_ids=[_OPEN_ROLE],
    )
    mcp_state = await _read_back(engine, mcp_profile)

    def _comparable(state: dict) -> dict:
        """Strip the identifiers and timestamps that differ by construction.

        ADR-077 gave the five previously id-less vault types minted ids —
        random per door invocation, like the work-entry ids already stripped
        here — so every entry ``id`` is normalized away recursively."""

        def _strip_ids(node):
            if isinstance(node, dict):
                return {k: _strip_ids(v) for k, v in node.items() if k != "id"}
            if isinstance(node, list):
                return [_strip_ids(v) for v in node]
            return node

        return _strip_ids({k: v for k, v in state.items() if k != "metadata"})

    assert _comparable(rest_state) == _comparable(mcp_state)
    assert (
        rest_state["metadata"]["completeness_score"]
        == mcp_state["metadata"]["completeness_score"]
    )
