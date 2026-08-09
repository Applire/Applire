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

"""#480 PR 5 — the conflict-resolution doors, and #512's closure.

Two things are pinned here.

**§7.6's door-level write test, one per migrated writer.** `commit_ops` flushes
and never commits, so a forgotten `db.commit()` in a migrated caller is a
SILENT no-write. `resolve_conflict` is reached through two doors — the REST
`POST /api/profile/conflicts/{id}/resolve` route and the agent channel's
`send_message` conflict-answer dispatch — and each is driven here against a
**file-backed** database, re-read over a separate connection.

**#512, the residual denial-release vector PR 4 could not close.** Until now
`resolve_conflict` assigned `profile_json` directly: no reconcile, no stance
guard, no denial floor, no committer. PR 4 narrowed the release corpus to
attested entity labels — which deliberately INCLUDES `work_experience[].role`,
`.company` and `.technologies[]` — so conflict-resolved text landing in those
fields became release-relevant while still travelling an unguarded path. What
closes the vector is not removing those fields from the corpus (the PO ruled
they stay: dropping them would over-floor genuine role-title affirmations) but
making a resolution an ordinary attested write: receipted, trailed, and
re-floor-guarded exactly like every other door.
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


_WORK_A = "11111111-1111-1111-1111-111111111111"
_CONFLICT = "aaaaaaaa-1111-2222-3333-444444444444"


def _seed_profile_json(**overrides) -> dict:
    """A vault holding a persisted denial of "Ansible" plus a role-title dispute
    whose incoming value re-states that very concept."""
    payload = {
        "personal_info": {
            "name": "Daniel Kovač",
            "email": "daniel@example.invalid",
        },
        "skills": [
            {"name": "Terraform", "category": "technical", "status": "confirmed"},
            {"name": "Ansible", "category": "technical", "status": "confirmed"},
        ],
        "work_experience": [
            {
                "id": _WORK_A,
                "company": "Rheinwerk GmbH",
                "role": "Automation Engineer",
                "start_date": "2018-01",
                "end_date": "2023-12",
                "responsibilities": ["Ran the build"],
            }
        ],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "metadata": {
            "completeness_score": 0.0,
            "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z",
            "last_updated": "2020-01-01T00:00:00Z",
            "denied_concepts": [
                {
                    "concept": "Ansible",
                    "statement": "I have never used Ansible.",
                    "source": "interview",
                    "date": "2026-08-01",
                    "denial_level": "direct",
                }
            ],
            "pending_conflicts": [
                {
                    "conflict_id": _CONFLICT,
                    "section": "work_experience",
                    "field": "role",
                    "entity_id": _WORK_A,
                    "existing_value": "Automation Engineer",
                    "incoming_value": "Ansible Automation Engineer",
                    "source": "cv_upload",
                    "resolved": False,
                }
            ],
            "enrichment_history": [],
        },
    }
    payload.update(overrides)
    return payload


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


async def _seed(factory, profile_json: dict | None = None) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(
                profile_json=profile_json
                if profile_json is not None
                else _seed_profile_json()
            )
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id: uuid.UUID) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state.

    The same helper the PR 1–3 door files carry, deliberately unchanged: the
    writer's request session has already exited by the time this runs, so a
    missing `db.commit()` has been rolled back and this read sees the
    pre-request state. Mutation-verified for both resolution doors — dropping
    either `db.commit()` turns the matching test red.
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


def _skill(stored: dict, name: str) -> dict:
    return next(s for s in stored["skills"] if s["name"] == name)


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(should not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


# ── Door 10: the REST conflict-resolution route ──────────────────────────────


@pytest.mark.asyncio
async def test_rest_resolve_door_write_survives_the_request(durable_db):
    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        response = await resolve_profile_conflict(
            _CONFLICT,
            ConflictResolutionRequest(resolution="incoming", value=None),
            request_session,
            None,
        )

    assert response.profile.work_experience[0].role == "Ansible Automation Engineer"
    stored = await _read_back(engine, profile_id)
    assert stored["work_experience"][0]["role"] == "Ansible Automation Engineer"
    assert stored["metadata"]["pending_conflicts"] == []
    # The committer's invariants are durable too, not just the field.
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual_edit"
    assert stored["metadata"]["completeness_score"] > 0
    assert stored["metadata"]["last_updated"] != "2020-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_rest_resolve_door_still_404s_on_an_unknown_conflict(durable_db):
    from fastapi import HTTPException

    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    engine, factory = durable_db
    await _seed(factory)

    async with factory() as request_session:
        with pytest.raises(HTTPException) as exc:
            await resolve_profile_conflict(
                str(uuid.uuid4()),
                ConflictResolutionRequest(resolution="incoming", value=None),
                request_session,
                None,
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_an_invalid_resolution_is_refused_before_anything_is_written(durable_db):
    """The refusal happens in the pure adapter, before anything reaches the
    write path — exactly as the section-edit intake refuses a bad section. The
    REST DTO already constrains `resolution` to the three literals, so this is
    the service contract the route's `ValueError` → 422 mapping rests on (and
    the one the agent door reaches without a DTO in front of it)."""
    from applire.services.profile import resolve_conflict

    engine, factory = durable_db
    profile_id = await _seed(factory)

    async with factory() as request_session:
        with pytest.raises(ValueError, match="Invalid resolution"):
            await resolve_conflict(_CONFLICT, "whatever", None, request_session)

    stored = await _read_back(engine, profile_id)
    assert stored["work_experience"][0]["role"] == "Automation Engineer"
    assert len(stored["metadata"]["pending_conflicts"]) == 1
    assert stored["metadata"]["enrichment_history"] == []


# ── Door 11: the agent channel's `send_message` conflict dispatch ─────────────


@pytest.mark.asyncio
async def test_agent_channel_resolve_write_survives_the_request(durable_db):
    """The same act through the interview door (`_handle_conflict_answer` →
    `_resolve_conflict_safely` → `resolve_conflict`). ADR-058 clause 2: one
    implementation, so the doors cannot drift."""
    from applire.services.session import create_profile_review_session, send_message

    engine, factory = durable_db
    profile_id = await _seed(factory)
    provider = _mock_provider()

    async with factory() as request_session:
        created = await create_profile_review_session(request_session, provider)
        await send_message(
            created.session_id, "use the new value", request_session, provider
        )

    stored = await _read_back(engine, profile_id)
    assert stored["work_experience"][0]["role"] == "Ansible Automation Engineer"
    assert stored["metadata"]["pending_conflicts"] == []
    assert stored["metadata"]["enrichment_history"][-1]["source"] == "manual_edit"


# ── Door 12: the confirmation-resolution intake ──────────────────────────────

_CONFIRMATION = "bbbbbbbb-1111-2222-3333-444444444444"


def _seed_with_confirmation() -> dict:
    payload = _seed_profile_json()
    payload["metadata"]["pending_conflicts"] = []
    payload["metadata"]["pending_confirmations"] = [
        {
            "confirmation_id": _CONFIRMATION,
            "question": "Is 'Owner' the same role as 'Founder'?",
            "options": ["Same role", "Two roles"],
            "context": {"existing": "Founder"},
            "source": "interview",
            "resolved": False,
        }
    ]
    return payload


@pytest.mark.asyncio
async def test_resolve_confirmation_door_write_survives_the_request(durable_db):
    """The clear has to be DURABLE or the ask comes back in the next session —
    which is the whole reason `commit_ops` could not park unconditionally
    before this PR."""
    from applire.services.profile import resolve_confirmation

    engine, factory = durable_db
    profile_id = await _seed(factory, _seed_with_confirmation())

    async with factory() as request_session:
        await resolve_confirmation(_CONFIRMATION, "Same role", request_session)

    stored = await _read_back(engine, profile_id)
    assert stored["metadata"]["pending_confirmations"] == []
    assert stored["metadata"]["enrichment_history"][-1]["source"] == "manual_edit"
    # The recompute this writer never did (design §6 row 5).
    assert stored["metadata"]["completeness_score"] > 0


# ── #512: the resolution is now an ordinary attested write ───────────────────


@pytest.mark.asyncio
async def test_resolving_a_conflict_runs_the_persisted_denial_refloor(durable_db):
    """THE mechanism #512 was about. `resolve_conflict` never met the write-seam
    floor before — its writes were caught only by the next ledger rebuild, if
    one ever ran. Routed, a resolution takes back a retracted skill like any
    other door."""
    engine, factory = durable_db
    profile_id = await _seed(factory)

    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    async with factory() as request_session:
        await resolve_profile_conflict(
            _CONFLICT,
            ConflictResolutionRequest(resolution="incoming", value=None),
            request_session,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert _skill(stored, "Ansible")["status"] == "denied"
    assert _skill(stored, "Terraform")["status"] == "confirmed"
    # …and the re-flooring is receipted, never counted as gap-addressing content.
    receipt = stored["metadata"]["enrichment_history"][-1]["changes"]
    assert any(
        c["section"] == "skills" and c["new_value"] == "denied" for c in receipt
    )


@pytest.mark.asyncio
async def test_the_denial_is_never_released_by_the_resolution_itself(durable_db):
    """The write-seam floor does not delete the denial, and the resolution does
    not consult an affirmation predicate to lift it. Whatever the release
    corpus later says about the new role text, the RECORD of the denial stands
    — ADR-059 §3.4: the only release path is the un-denial act (#506)."""
    engine, factory = durable_db
    profile_id = await _seed(factory)

    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    async with factory() as request_session:
        await resolve_profile_conflict(
            _CONFLICT,
            ConflictResolutionRequest(resolution="incoming", value=None),
            request_session,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert [d["concept"] for d in stored["metadata"]["denied_concepts"]] == ["Ansible"]


@pytest.mark.asyncio
async def test_a_resolution_and_a_manual_edit_leave_the_same_guarded_state(durable_db):
    """#512's actual closure, stated as parity: text entering the release
    corpus (`work_experience[].role`) through a conflict resolution is now
    guarded exactly as the same text entering it through the routed manual
    section edit. No door writes role/company/technologies unguarded any more."""
    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest
    from applire.services.profile import patch_profile_section

    engine, factory = durable_db
    resolved_id = await _seed(factory)

    async with factory() as request_session:
        await resolve_profile_conflict(
            _CONFLICT,
            ConflictResolutionRequest(resolution="incoming", value=None),
            request_session,
            None,
        )
    via_resolution = await _read_back(engine, resolved_id)

    # A second, independent vault written through the manual-edit door.
    from applire.models.profile import MasterProfile

    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == resolved_id)
            )
        ).scalar_one()
        row.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    edited_id = await _seed(factory)

    async with factory() as request_session:
        await patch_profile_section(
            "work_experience",
            [
                {
                    "id": _WORK_A,
                    "company": "Rheinwerk GmbH",
                    "role": "Ansible Automation Engineer",
                    "start_date": "2018-01",
                    "end_date": "2023-12",
                    "responsibilities": ["Ran the build"],
                }
            ],
            request_session,
        )
    via_edit = await _read_back(engine, edited_id)

    def _guard_state(stored: dict) -> dict:
        return {
            "role": stored["work_experience"][0]["role"],
            "skills": {s["name"]: s["status"] for s in stored["skills"]},
            "denials": [d["concept"] for d in stored["metadata"]["denied_concepts"]],
            "trailed": bool(stored["metadata"]["enrichment_history"]),
        }

    assert _guard_state(via_resolution) == _guard_state(via_edit)
    assert _guard_state(via_resolution)["skills"]["Ansible"] == "denied"
