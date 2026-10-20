# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 0083 — re-link the flows 0076 left detached (#789 L59; ADR-092
amended 2026-10-20).

Pre-0076 fixture: the v0.42 (Nougat) shape from ``tests.support.nougat_fixture``
plus ``seed_0076``'s flow-only posting, run through 0074 → 0075 → 0076 (the
upgrade that produced the symptom: a ``tracking`` card with no step), then 0083.
The PostgreSQL proof is the scratch-database run recorded in the WP-R report.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from applire.db.session import Base
from applire.models.application import STEP_TO_WORKFLOW_STATUS
from tests.support.nougat_fixture import M74, M75, STUB, FixtureDB, load_migration, run_migration, seed_nougat
from tests.support.posting_links import seed_0076

M76 = load_migration("0076")
M83 = load_migration("0083")


@pytest.fixture
def pre_0076_db():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migration(conn, M76.downgrade)
        run_migration(conn, M75.downgrade)
        run_migration(conn, M74.downgrade)
    yield engine
    engine.dispose()


def _upgrade_to_0076(engine) -> None:  # noqa: ANN001
    with engine.begin() as conn:
        for m in (M74, M75, M76):
            run_migration(conn, m.upgrade)


def _app_for(conn, job_id):  # noqa: ANN001
    rows = FixtureDB(conn).rows("applications", "id", "job_analysis_id", "flow_session_id", "workflow_status")
    return next(r for r in rows if r[1] == job_id)


def _flow(conn, flow_id):  # noqa: ANN001
    return next(r for r in FixtureDB(conn).rows("flow_sessions", "id", "application_id") if r[0] == flow_id)


def test_revision_chain():
    assert (M83.revision, M83.down_revision) == ("0083", "0082")


def test_the_step_map_is_a_frozen_copy_of_the_app_map():
    assert M83._STEP_TO_STATUS == {k: v.value for k, v in STEP_TO_WORKFLOW_STATUS.items()}


def test_the_0076_symptom_exists_and_0083_relinks_it(pre_0076_db):
    with pre_0076_db.begin() as conn:
        db = FixtureDB(conn)
        more = seed_0076(db, seed_nougat(db, with_stub=True))
    _upgrade_to_0076(pre_0076_db)
    with pre_0076_db.connect() as conn:
        app = _app_for(conn, more["j_flow"])
        assert app[2:] == (None, "none"), "precondition: 0076 left the flow detached"
    with pre_0076_db.begin() as conn:
        run_migration(conn, M83.upgrade)
    with pre_0076_db.connect() as conn:
        app = _app_for(conn, more["j_flow"])
        assert app[2] == more["flow_only"] and app[3] == "analyzing"  # jd_analysis -> analyzing
        assert _flow(conn, more["flow_only"])[1] == app[0]
        # postings without a flow keep their plain tracking card
        others = [r for r in FixtureDB(conn).rows("applications", "job_analysis_id", "flow_session_id")
                  if r[0] != more["j_flow"]]
        assert all(flow is None for _j, flow in others)


@pytest.mark.parametrize("step, status", sorted(M83._STEP_TO_STATUS.items()))
def test_the_workflow_status_follows_the_flow_step(pre_0076_db, step, status):
    with pre_0076_db.begin() as conn:
        db = FixtureDB(conn)
        more = seed_0076(db, seed_nougat(db, with_stub=True))
        conn.execute(sa.text("UPDATE flow_sessions SET current_step = :s"), {"s": step})
    _upgrade_to_0076(pre_0076_db)
    with pre_0076_db.begin() as conn:
        run_migration(conn, M83.upgrade)
        assert _app_for(conn, more["j_flow"])[3] == status


def test_cancelled_deleted_or_claimed_pairs_are_left_alone(pre_0076_db):
    with pre_0076_db.begin() as conn:
        db = FixtureDB(conn)
        more = seed_0076(db, seed_nougat(db, with_stub=True))
    _upgrade_to_0076(pre_0076_db)
    with pre_0076_db.begin() as conn:
        app_id = _app_for(conn, more["j_flow"])[0]
        conn.execute(sa.text("UPDATE applications SET user_status = 'cancelled'"))
        run_migration(conn, M83.upgrade)
        assert _app_for(conn, more["j_flow"])[2] is None, "a cancelled card is not re-linked"
        conn.execute(sa.text("UPDATE applications SET user_status = 'tracking'"))
        # the flow is claimed by another application -> not stolen
        other = uuid.uuid4()
        conn.execute(sa.text("UPDATE flow_sessions SET application_id = :o"), {"o": other.hex})
        run_migration(conn, M83.upgrade)
        assert _app_for(conn, more["j_flow"])[2] is None
        assert app_id is not None


def test_idempotent(pre_0076_db):
    with pre_0076_db.begin() as conn:
        db = FixtureDB(conn)
        more = seed_0076(db, seed_nougat(db, with_stub=True))
    _upgrade_to_0076(pre_0076_db)
    with pre_0076_db.begin() as conn:
        run_migration(conn, M83.upgrade)
        first = FixtureDB(conn).rows("applications", "id", "flow_session_id", "workflow_status")
        run_migration(conn, M83.upgrade)
        assert FixtureDB(conn).rows("applications", "id", "flow_session_id", "workflow_status") == first
        run_migration(conn, M83.downgrade)  # a no-op by design
        assert FixtureDB(conn).rows("applications", "id", "flow_session_id", "workflow_status") == first
    assert more["flow_only"] in {r[1] for r in first}
