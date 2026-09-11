# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#58 / ADR-054 amended 2026-09-11 — the E033 Branch-H surfaces on the agent door.

`get_profile_health` (US160 read + the US167 open gates in one call),
`undo_last_merge` (US168 / ADR-042) and `resolve_held_merge` (US167, relay-only).

These run the tool FUNCTIONS in-process against a real `sqlite+aiosqlite`
session, which is what the repo-root stdio tier cannot do cheaply (its conftest
recreates a compose stack). The stdio tier pins REGISTRATION and RETURN SHAPE;
this file pins behaviour.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


_ACCOUNT = "Stefan Brandt"
_STRANGER = "Maria Klein"


def _profile_json(name: str = _ACCOUNT, enrichment_id: str | None = None) -> dict:
    """A REALISTIC persisted vault row.

    Round-tripped through ``MasterProfileData`` on purpose: every vault write
    path stores a model dump, so a hand-written raw dict is a shape production
    never has — and `field_gaps` reads keys whose presence the model normalises
    (`professional_summary` becomes a truthy object of ``None``s). A parity
    assertion made against a hand-written dict measures the fixture, not the
    seam.
    """
    from applire.schemas.profile import MasterProfileData

    raw = {
        "personal_info": {"name": name, "email": "kontakt@applire.de"},
        "work_experience": [
            {
                "company": "Beta GmbH",
                "role": "Product Lead",
                "start_date": "2020-01",
                "end_date": "2023-06",
                "achievements": [],
            }
        ],
        "skills": [{"name": "SAP PP", "level": "advanced"}],
        "education": [],
    }
    data = MasterProfileData.model_validate(raw).model_dump(mode="json")
    if enrichment_id is not None:
        meta = data.get("metadata") or {}
        meta["enrichment_history"] = [
            {"id": enrichment_id, "source": "cv_upload", "changes": []}
        ]
        data["metadata"] = meta
    return data


async def _seed_user(db) -> uuid.UUID:
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="kontakt@applire.de")
    db.add(user)
    await db.flush()
    return user.id


async def _seed_profile(db, name: str = _ACCOUNT, enrichment_id: str | None = None):
    profile = make_master_profile(profile_json=_profile_json(name, enrichment_id))
    db.add(profile)
    await db.flush()
    return profile


async def _seed_held_upload(db, user_id, gate: str = "name_divergence", cv_name: str = _STRANGER):
    from applire.models.uploads import UploadRecord

    rec = UploadRecord(
        id=uuid.uuid4(),
        user_id=user_id,
        original_filename="fremder-lebenslauf.pdf",
        content_hash="sha256:deadbeef",
        mime_type="application/pdf",
        file_path="/tmp/does-not-matter.pdf",
        byte_size=1234,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        gate_status=gate,
        staged_extraction={
            "personal_info": {"name": cv_name},
            "work_experience": [],
            "skills": [],
            "education": [],
        },
    )
    db.add(rec)
    await db.flush()
    return rec


# ---------------------------------------------------------------------------
# get_profile_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_health_returns_issues_completeness_and_held_merges(db):
    from applire.mcp.server import get_profile_health

    uid = await _seed_user(db)
    await _seed_profile(db)
    rec = await _seed_held_upload(db, uid)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await get_profile_health()

    assert set(out) >= {"issues", "completeness", "held_merges"}
    assert isinstance(out["issues"], list)
    assert "score" in out["completeness"] and "field_gaps" in out["completeness"]
    assert len(out["held_merges"]) == 1
    held = out["held_merges"][0]
    assert held["staged_id"] == str(rec.id)
    assert held["gate"] == "name_divergence"
    # Both names, so the agent can put a PRECISE question to the human instead
    # of guessing — the whole point of the relay (ADR-041 amended).
    assert held["account_name"] == _ACCOUNT
    assert held["cv_name"] == _STRANGER
    assert held["original_filename"] == "fremder-lebenslauf.pdf"
    assert held["created_at"]


@pytest.mark.asyncio
async def test_get_profile_health_field_gaps_is_exactly_the_mode_c_agenda(db):
    """ADR-058 clause 5: the agent door publishes the AGENDA, not a question
    loop. That is only honest if the agenda is the same list Mode C walks —
    `gap_detector_mode_c` delegates to `completeness.field_gaps` (US179), and
    this pins the two together at the agent door's own return value."""
    from applire.mcp.server import get_profile_health
    from applire.services.interview_graph import gap_detector_mode_c

    await _seed_user(db)
    profile = await _seed_profile(db)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await get_profile_health()

    assert out["completeness"]["field_gaps"] == gap_detector_mode_c(profile.profile_json)
    assert out["completeness"]["field_gaps"], "fixture must actually have gaps"


@pytest.mark.asyncio
async def test_get_profile_health_lists_no_held_merge_once_it_is_resolved(db):
    from applire.mcp.server import get_profile_health

    uid = await _seed_user(db)
    await _seed_profile(db)
    rec = await _seed_held_upload(db, uid)
    rec.gate_status = "resolved_discarded"
    await db.flush()

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await get_profile_health()

    assert out["held_merges"] == []


@pytest.mark.asyncio
async def test_get_profile_health_survives_a_fresh_install(db):
    """No user row, no profile. The health read must answer, not raise — an
    agent's first call on a new instance is exactly this state."""
    from applire.mcp.server import get_profile_health

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await get_profile_health()

    assert out["completeness"]["score"] == 0.0
    assert out["issues"] == []
    assert out["held_merges"] == []


@pytest.mark.asyncio
async def test_held_merge_summary_reads_the_same_name_as_the_rest_door(db):
    """ADR-066 door parity, at the one place the two doors format independently.

    The REST upload-history door digs the parked CV's name out of
    `staged_extraction["personal_info"]["name"]` (`routers/profile.py`); the MCP
    adapter must read the SAME field, or the two doors show the human two
    different names for one document.
    """
    from applire.mcp.server import _held_merge_summary

    uid = await _seed_user(db)
    rec = await _seed_held_upload(db, uid, cv_name="Ganz Anders")

    rest_side = (
        (rec.staged_extraction or {}).get("personal_info", {}).get("name")
        if rec.staged_extraction
        else None
    )
    assert _held_merge_summary(rec, _ACCOUNT)["cv_name"] == rest_side == "Ganz Anders"


@pytest.mark.asyncio
async def test_held_merge_summary_does_not_invent_a_name(db):
    """A `not_a_cv` gate parks a document with no reliable name. The adapter
    reports `None`, never a placeholder — a fabricated name in an identity
    question is the worst possible failure of this surface."""
    from applire.mcp.server import _held_merge_summary

    uid = await _seed_user(db)
    rec = await _seed_held_upload(db, uid, gate="not_a_cv", cv_name="")
    rec.staged_extraction = {"personal_info": {}, "work_experience": [], "skills": []}

    summary = _held_merge_summary(rec, None)
    assert summary["cv_name"] is None
    assert summary["account_name"] is None
    assert summary["gate"] == "not_a_cv"


# ---------------------------------------------------------------------------
# undo_last_merge
# ---------------------------------------------------------------------------


async def _seed_snapshot(db, profile, enrichment_id: str, skills=None):
    from applire.models.profile import ProfileSnapshot

    pre_merge = _profile_json()
    pre_merge["skills"] = skills if skills is not None else []
    db.add(
        ProfileSnapshot(
            id=uuid.uuid4(),
            profile_id=profile.id,
            enrichment_record_id=enrichment_id,
            profile_json=pre_merge,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_undo_last_merge_restores_then_is_idempotent(db):
    """The clean case: the profile head is still the merge this snapshot
    preceded, so nothing made after it is lost."""
    from applire.mcp.server import undo_last_merge

    merge_id = str(uuid.uuid4())
    await _seed_user(db)
    profile = await _seed_profile(db, enrichment_id=merge_id)
    await _seed_snapshot(db, profile, merge_id)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        first = await undo_last_merge()
        second = await undo_last_merge()

    assert first == {"restored": True, "discarded_later_edits": False}
    # Single-level by design: the chain is consumed, so a repeat is a no-op and
    # says so instead of peeling back another merge.
    assert second == {"restored": False, "discarded_later_edits": False}
    await db.refresh(profile)
    assert profile.profile_json["skills"] == []


@pytest.mark.asyncio
async def test_undo_last_merge_warns_when_later_edits_are_discarded(db):
    """The agent MUST be able to tell the human that their post-merge edits went
    with the undo — the flag is the only signal, and swallowing it turns a
    recoverable mistake into a silent data loss."""
    from applire.mcp.server import undo_last_merge

    await _seed_user(db)
    # Profile head = a LATER enrichment than the one the snapshot precedes.
    profile = await _seed_profile(db, enrichment_id=str(uuid.uuid4()))
    await _seed_snapshot(db, profile, str(uuid.uuid4()))

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await undo_last_merge()

    assert out == {"restored": True, "discarded_later_edits": True}


@pytest.mark.asyncio
async def test_undo_last_merge_with_nothing_to_undo_is_not_an_error(db):
    from applire.mcp.server import undo_last_merge

    await _seed_user(db)
    await _seed_profile(db)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await undo_last_merge()

    assert out == {"restored": False, "discarded_later_edits": False}


# ---------------------------------------------------------------------------
# resolve_held_merge — the relay boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_held_merge_discard_leaves_the_vault_untouched(db):
    from applire.mcp.server import resolve_held_merge

    uid = await _seed_user(db)
    profile = await _seed_profile(db)
    rec = await _seed_held_upload(db, uid)
    before = dict(profile.profile_json)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        out = await resolve_held_merge(str(rec.id), "discard")

    assert out["staged_id"] == str(rec.id)
    assert out["action"] == "discard"
    await db.refresh(profile)
    assert profile.profile_json["personal_info"] == before["personal_info"]
    await db.refresh(rec)
    assert rec.gate_status == "resolved_discarded"


@pytest.mark.asyncio
async def test_resolve_held_merge_rejects_any_decision_but_merge_or_discard(db):
    """The agent RELAYS a human decision. There is no default, no 'auto', and
    no free-text — a value the tool does not understand must be refused before
    anything touches the vault (#58: identity stays the user's call)."""
    from applire.mcp.server import resolve_held_merge

    uid = await _seed_user(db)
    await _seed_profile(db)
    rec = await _seed_held_upload(db, uid)

    p_db, p_prov = _patched(db)
    for bad in ("", "auto", "yes", "MERGE", "probably a maiden name"):
        with p_db, p_prov, pytest.raises(McpError) as exc:
            await resolve_held_merge(str(rec.id), bad)
        assert "merge" in str(exc.value) and "discard" in str(exc.value)
    await db.refresh(rec)
    assert rec.gate_status == "name_divergence", "a refused decision must not resolve"


@pytest.mark.asyncio
async def test_resolve_held_merge_unknown_id_names_the_tool_that_lists_them(db):
    """#603's rule: an error an agent reads must name the TOOL that fixes it,
    never a REST path the agent cannot call."""
    from applire.mcp.server import resolve_held_merge

    await _seed_user(db)
    await _seed_profile(db)

    p_db, p_prov = _patched(db)
    with p_db, p_prov, pytest.raises(McpError) as exc:
        await resolve_held_merge(str(uuid.uuid4()), "discard")
    assert "get_profile_health" in str(exc.value)


@pytest.mark.asyncio
async def test_resolve_held_merge_refuses_a_second_resolve(db):
    from applire.mcp.server import resolve_held_merge

    uid = await _seed_user(db)
    await _seed_profile(db)
    rec = await _seed_held_upload(db, uid)

    p_db, p_prov = _patched(db)
    with p_db, p_prov:
        await resolve_held_merge(str(rec.id), "discard")
    with p_db, p_prov, pytest.raises(McpError) as exc:
        await resolve_held_merge(str(rec.id), "discard")
    assert "already resolved" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_resolve_held_merge_rejects_a_malformed_staged_id(db):
    from applire.mcp.server import resolve_held_merge

    await _seed_user(db)
    p_db, p_prov = _patched(db)
    with p_db, p_prov, pytest.raises(McpError) as exc:
        await resolve_held_merge("not-a-uuid", "discard")
    assert "staged_id" in str(exc.value)
