# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#822 — migration 0087 repairs roles stored with a passed end date AND
``is_current = true``. A migration carries a copy of the app's predicate; this
file pins that the copy agrees with the app and that the step works on a real
database (live rows only, idempotent, the app loads what it wrote)."""
import importlib.util
import json
from datetime import date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from applire.schemas.profile import MasterProfileData, normalise_partial_date
from applire.services.profile.reconcile.apply import end_date_has_passed

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "backend" / "alembic" / "versions" / "0087_ended_roles_not_current.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("m0087", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_chains_onto_0086():
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0087", "0086")


_DATES = [
    None, "", "heute", "present", "2020", "2026", "2027", "2020-01", "2026-10", "2026-11",
    "2026-12", "2020/3", "03.2014", "03/2014", "2026-11-06", "2026-11-07", "2026-11-08",
    "6.11.2026", "06/11/2026", "2026-11-06T00:00:00", "2026-13", "31.02.2020", 2020,
]


@pytest.mark.parametrize("value", _DATES)
def test_the_migrations_copy_agrees_with_the_app(value):
    mod = _migration()
    today = date(2026, 11, 7)
    assert mod.normalise_partial_date(value) == normalise_partial_date(value)
    assert mod.end_date_has_passed(value, today) == end_date_has_passed(value, today)


def _vault(work: list[dict], **extra) -> dict:
    data = MasterProfileData(work_experience=work, **extra).model_dump(mode="json")
    return data


def test_the_migration_repairs_live_rows_on_a_real_database():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _migration()
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "master_profiles", meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("profile_json", sa.JSON, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    meta.create_all(engine)
    stale = [
        {"company": "Northwind Health", "role": "PM", "start_date": "2016-04",
         "end_date": "2020-01", "is_current": True},
        {"company": "Bluefin Care", "role": "Lead PM", "start_date": "2020-02",
         "end_date": "2024-02", "is_current": True},
        {"company": "Orbit Apps", "role": "Head of Product", "start_date": "2024-03",
         "end_date": None, "is_current": True},
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {"id": "live", "profile_json": _vault(stale), "deleted_at": None},
            {"id": "deleted", "profile_json": _vault(stale), "deleted_at": datetime(2026, 11, 1)},
        ])

    def run(step):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()
        with engine.connect() as conn:
            return {
                r.id: (json.loads(r.profile_json) if isinstance(r.profile_json, str) else r.profile_json)
                for r in conn.execute(sa.select(table))
            }

    after = run(mod.upgrade)
    assert [w["is_current"] for w in after["live"]["work_experience"]] == [False, False, True]
    assert [w["end_date"] for w in after["live"]["work_experience"]] == ["2020-01", "2024-02", None]
    assert [w["is_current"] for w in after["deleted"]["work_experience"]] == [True, True, True]
    MasterProfileData.model_validate(after["live"])  # the app loads what the migration wrote

    again = run(mod.upgrade)
    assert again["live"] == after["live"], "idempotent"

    down = run(mod.downgrade)
    assert down["live"] == after["live"], "downgrade restores nothing"


def test_repair_reaches_projects_and_volunteer_entries():
    mod = _migration()
    data = {
        "projects": [{"name": "P", "end_date": "2019-09", "is_current": True}],
        "volunteer_activities": [{"organization": "V", "end_date": "2018", "is_current": True}],
        "work_experience": "not a list",
    }
    assert mod.repair(data, today=date(2026, 11, 7)) == 2
    assert data["projects"][0]["is_current"] is False
    assert data["volunteer_activities"][0]["is_current"] is False
