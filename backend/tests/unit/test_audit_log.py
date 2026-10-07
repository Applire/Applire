# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audit log is append-only and holds no personal data (ADR-091 cl. 26, RD-11)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text, update
from sqlalchemy.exc import DatabaseError

from applire.models.audit import AuditEvent, AuditLogIsAppendOnly
from applire.services import audit as audit_service
from applire.services.audit import ACTIONS, AuditDetailRejected, record

FORBIDDEN_KEY_FRAGMENTS = ("email", "mail_address", "password", "token_value", "secret",
                           "ip", "content", "profile", "text", "name")


@pytest.mark.asyncio
async def test_record_writes_one_row_with_user_target(async_db):
    actor, target = uuid.uuid4(), uuid.uuid4()
    await record(async_db, actor_id=actor, action="user.role_changed", target_type="user",
                 target_id=target, details={"from_role": "user", "to_role": "admin"})
    await async_db.commit()
    rows = (await async_db.execute(select(AuditEvent))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == actor and row.target_user_id == target
    assert row.detail == {"from_role": "user", "to_role": "admin"}
    assert row.at is not None


@pytest.mark.asyncio
async def test_non_user_target_goes_into_detail(async_db):
    tok = uuid.uuid4()
    await record(async_db, actor_id=None, action="token.revoked", target_type="token",
                 target_id=tok, details={"token_id": tok, "scope": "agent"})
    row = (await async_db.execute(select(AuditEvent))).scalar_one()
    assert row.target_user_id is None
    assert row.detail == {"token_id": str(tok), "scope": "agent",
                          "target_type": "token", "target_id": str(tok)}


def test_audit_table_has_no_ip_column():
    cols = set(AuditEvent.__table__.columns.keys())
    assert cols == {"id", "at", "actor_user_id", "action", "target_user_id", "detail"}
    assert not any("ip" == c or c.endswith("_ip") for c in cols)


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_no_action_allows_a_personal_data_key(action):
    for key in ACTIONS[action]:
        lowered = key.lower()
        for frag in FORBIDDEN_KEY_FRAGMENTS:
            assert frag not in lowered.split("_") and lowered != frag, (action, key)


@pytest.mark.asyncio
@pytest.mark.parametrize("details", [
    {"email": "a@b.org"},
    {"password": "x"},
    {"token": "apl_x"},
    {"ip": "10.0.0.1"},
])
async def test_forbidden_detail_keys_are_refused(async_db, details):
    with pytest.raises(AuditDetailRejected):
        await record(async_db, actor_id=None, action="user.disabled", target_type="user",
                     target_id=uuid.uuid4(), details=details)


@pytest.mark.asyncio
async def test_email_value_under_an_allowed_key_is_refused(async_db):
    with pytest.raises(AuditDetailRejected):
        await record(async_db, actor_id=None, action="reset_link.issued", target_type="user",
                     target_id=uuid.uuid4(), details={"via": "x@example.org"})


@pytest.mark.asyncio
async def test_unknown_action_is_refused(async_db):
    with pytest.raises(AuditDetailRejected):
        await record(async_db, actor_id=None, action="user.peeked", target_type=None,
                     target_id=None, details={})


@pytest.mark.asyncio
async def test_non_scalar_detail_is_refused(async_db):
    with pytest.raises(AuditDetailRejected):
        await record(async_db, actor_id=None, action="tokens.revoked_all", target_type="user",
                     target_id=uuid.uuid4(), details={"count": [1, 2]})


def test_record_signature_is_the_dictated_one():
    import inspect
    sig = inspect.signature(audit_service.record)
    assert list(sig.parameters) == ["db", "actor_id", "action", "target_type", "target_id", "details"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for n, p in sig.parameters.items() if n != "db")
    assert inspect.iscoroutinefunction(audit_service.record)


# --- append-only: three layers, each proven on its own --------------------------

async def _one_row(db) -> AuditEvent:
    await record(db, actor_id=None, action="user.disabled", target_type="user",
                 target_id=uuid.uuid4(), details={})
    await db.commit()
    return (await db.execute(select(AuditEvent))).scalar_one()


@pytest.mark.asyncio
async def test_orm_dirty_update_is_refused(async_db):
    row = await _one_row(async_db)
    row.action = "user.enabled"
    with pytest.raises(AuditLogIsAppendOnly):
        await async_db.flush()


@pytest.mark.asyncio
async def test_orm_bulk_update_is_refused(async_db):
    await _one_row(async_db)
    with pytest.raises(AuditLogIsAppendOnly):
        await async_db.execute(update(AuditEvent).values(action="user.enabled"))


@pytest.mark.asyncio
async def test_database_trigger_refuses_raw_update(async_db):
    """Bypasses the ORM entirely — only the trigger stands between."""
    await _one_row(async_db)
    with pytest.raises(DatabaseError, match="append-only"):
        await async_db.execute(text("UPDATE audit_events SET action = 'user.enabled'"))
    await async_db.rollback()
    row = (await async_db.execute(select(AuditEvent))).scalar_one()
    assert row.action == "user.disabled"


@pytest.mark.asyncio
async def test_delete_stays_possible_for_retention(async_db):
    await _one_row(async_db)
    await async_db.execute(text("DELETE FROM audit_events"))
    await async_db.commit()
    assert (await async_db.execute(select(AuditEvent))).first() is None


# --- migration 0073 builds the same table and trigger ---------------------------

def _load_migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0073_audit_events.py"
    spec = importlib.util.spec_from_file_location("mig_0073", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0073_creates_append_only_table_on_sqlite():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("audit_events")}
        assert cols == {"id", "at", "actor_user_id", "action", "target_user_id", "detail"}
        conn.execute(text(
            "INSERT INTO audit_events (id, at, action, detail) "
            "VALUES ('a', '2026-10-03', 'user.disabled', '{}')"))
    with engine.begin() as conn, pytest.raises(DatabaseError, match="append-only"):
        conn.execute(text("UPDATE audit_events SET action = 'x'"))
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.downgrade()
        assert "audit_events" not in sa.inspect(conn).get_table_names()


def test_migration_and_model_trigger_text_agree():
    from applire.models import audit as model
    mig = _load_migration()
    assert mig._PG_FUNCTION == model.PG_TRIGGER_FUNCTION
    assert mig._PG_TRIGGER == model.PG_TRIGGER
    assert mig._SQLITE_TRIGGER == model.SQLITE_TRIGGER
