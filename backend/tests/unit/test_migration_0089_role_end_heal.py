# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ruling MD7-12 — migration 0089 heals stored role-end disputes at upgrade.

UX audit round 4 left vaults with ``is_current`` / ``end_date`` disputes that only
state a role's end ("false (ended 2024-02)" vs "true (current position, no end
date)"). Build 7's committer settles that shape on every write
(``settle_role_end_conflicts``); 0089 does it once for every stored vault. A
migration carries a copy of the app's rules; this file pins that the copy and
the app agree (primitives AND the whole heal), and that the step works on a real
database: live rows only, a genuine dispute stays, idempotent, counts not content.
"""
import copy
import importlib.util
import json
import logging
from datetime import date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from applire.schemas.profile import (
    Conflict,
    MasterProfileData,
    ProfileMetadata,
    ProjectEntry,
    WorkEntry,
    normalise_partial_date,
)
from applire.services.profile.reconcile import role_end
from applire.services.profile.reconcile.apply import close_ended_roles, settle_role_end_conflicts

TODAY = date(2026, 11, 7)

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "backend" / "alembic" / "versions" / "0089_role_end_conflict_heal.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("m0089", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_chains_onto_0088():
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0089", "0088")


def test_the_word_sets_and_sections_are_the_apps():
    mod = _migration()
    for name in ("BOOL_CONFLICT_FIELDS", "DATE_CONFLICT_FIELDS", "DATED_ENTRY_SECTIONS",
                 "_TRUE", "_FALSE", "_NO_DATE"):
        assert getattr(mod, name) == getattr(role_end, name), name
    assert mod._LEAD.pattern == role_end._LEAD.pattern


# ── primitive parity ─────────────────────────────────────────────────────────────

_VALUES = [
    None, "", "  ", True, False, 1, 2020, "true", "True (current position, no end date)",
    "false (ended 2024-02)", "yes", "nein", "aktuell", "beendet", "ended 2024", "maybe",
    "none (current position)", "present", "heute", "ongoing", "null", "2024-02", "2024",
    "02.2024", "02/2024", "2024/2", "2024-02-15", "15.02.2024", "2024-02-15T00:00:00",
    "2024-02 (end of contract)", "Feb 2024", "2024-13", "31.02.2020", "sometime last year",
    "2026-11", "2026-12", "2026-11-06", "2026-11-07", "2026",
]


@pytest.mark.parametrize("value", _VALUES)
@pytest.mark.parametrize("field", ["is_current", "end_date", "start_date", "company"])
def test_coerce_agrees_with_the_app(field, value):
    mod = _migration()
    got, want = mod.coerce_conflict_value(field, value), role_end.coerce_conflict_value(field, value)
    if want is role_end.UNREADABLE:
        assert got is mod.UNREADABLE
    else:
        assert got == want and type(got) is type(want)


@pytest.mark.parametrize("value", _VALUES)
def test_normalise_partial_date_agrees_with_the_app(value):
    assert _migration().normalise_partial_date(value) == normalise_partial_date(value)


_END_DATES = [None, "", "2024-02", "2026-11", "2026-12", "2026", "2025", "2026-11-06", "2026-11-07", "bogus"]
_INCOMING = [None, True, False, "2024-02", "2026-11", "2027-01", "2025", "2026-11-06", "2026-11-08"]


@pytest.mark.parametrize("end", _END_DATES)
@pytest.mark.parametrize("incoming", _INCOMING)
@pytest.mark.parametrize("field", ["is_current", "end_date", "company"])
def test_the_verdict_agrees_with_the_app(field, end, incoming):
    mod = _migration()
    assert mod.role_end_verdict(field, end, incoming, TODAY) == role_end.role_end_verdict(field, end, incoming, TODAY)


# ── whole-heal parity: the dict heal == settle_role_end_conflicts + close_ended_roles ──


def _role(company: str, **kw) -> WorkEntry:
    payload = {"company": company, "role": "Nurse", "start_date": "2020-02", "is_current": True}
    payload.update(kw)
    return WorkEntry(**payload)


def _conflict(entity_id: str, field: str, existing, incoming, *, section="work_experience", **kw) -> Conflict:
    return Conflict(section=section, field=field, entity_id=entity_id,
                    existing_value=existing, incoming_value=incoming, source="cv_upload", **kw)


def _emma_pair(entity_id: str, ended: str = "2024-02") -> list[Conflict]:
    """The stored round-4 shape (prose values, is_current first)."""
    return [
        _conflict(entity_id, "is_current", "true (current position, no end date)", f"false (ended {ended})"),
        _conflict(entity_id, "end_date", "none (current position)", ended),
    ]


def _vault(work: list[WorkEntry], conflicts: list[Conflict], projects=()) -> MasterProfileData:
    return MasterProfileData(work_experience=work, projects=list(projects),
                             metadata=ProfileMetadata(pending_conflicts=conflicts))


def _scenarios() -> dict[str, MasterProfileData]:
    out: dict[str, MasterProfileData] = {}

    emma = _role("Mediora Health")
    cerova = _role("Cerova Clinic", start_date="2024-03")
    out["emma pair"] = _vault([emma, cerova], _emma_pair(emma.id))

    e = _role("Mediora Health")
    out["emma pair, end_date first"] = _vault([e], list(reversed(_emma_pair(e.id))))

    two = _role("Mediora Health", end_date="2023-05", is_current=False)
    out["two past dates stay"] = _vault([two], [_conflict(two.id, "end_date", "2023-05", "2024-02")])

    back = _role("Mediora Health", end_date="2024-02", is_current=False)
    out["later source says current stays"] = _vault([back], [_conflict(back.id, "is_current", False, "true (current)")])

    fut = _role("Mediora Health")
    out["future end stays"] = _vault([fut], [_conflict(fut.id, "end_date", "none", "2027-03")])

    unread = _role("Mediora Health")
    out["unreadable stays"] = _vault([unread], [_conflict(unread.id, "end_date", "none", "sometime last year")])

    res = _role("Mediora Health")
    out["resolved untouched"] = _vault([res], [_conflict(res.id, "end_date", "none", "2024-02", resolved=True)])

    wrong = _role("Mediora Health")
    out["section mismatch stays"] = _vault(
        [wrong], [_conflict(wrong.id, "end_date", "none", "2024-02", section="projects")])

    ghost = _role("Mediora Health")
    out["unknown entity stays"] = _vault([ghost], [_conflict("no-such-id", "end_date", "none", "2024-02")])

    only_flag = _role("Mediora Health", end_date="2024-02")
    out["is_current alone on an ended role"] = _vault(
        [only_flag], [_conflict(only_flag.id, "is_current", True, False)])

    typed = _role("Mediora Health")
    out["typed stored pair"] = _vault([typed], [
        _conflict(typed.id, "is_current", True, False),
        _conflict(typed.id, "end_date", None, "2024-02"),
    ])

    other = _role("Mediora Health")
    out["other field stays"] = _vault([other], [_conflict(other.id, "company", "Mediora", "Mediora Health GmbH")])

    proj = ProjectEntry(name="Ward rollout", is_current=True)
    out["project entry"] = _vault([], [_conflict(proj.id, "end_date", "present", "03.2023", section="projects")],
                                  projects=[proj])

    stale = _role("Mediora Health", end_date="2022-08")  # no dispute, 0087-shape leftover
    out["stale current with no dispute"] = _vault([stale], [])

    mixed = _role("Mediora Health")
    real = _role("Cerova Clinic", end_date="2021-01", is_current=False)
    out["mixed vault"] = _vault([mixed, real], [
        *_emma_pair(mixed.id, "2023-09"),
        _conflict(real.id, "end_date", "2021-01", "2021-06"),
    ])
    return out


_SCENARIOS = _scenarios()


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_the_heal_agrees_with_the_committer(name):
    mod = _migration()
    profile = _SCENARIOS[name].model_copy(deep=True)
    data = copy.deepcopy(profile.model_dump(mode="json"))

    settle_role_end_conflicts(profile, [], [], today=TODAY)
    close_ended_roles(profile, [], today=TODAY)
    mod.heal(data, today=TODAY)

    assert data == profile.model_dump(mode="json")


def test_the_emma_pair_heals_to_the_fact():
    mod = _migration()
    data = _SCENARIOS["emma pair"].model_dump(mode="json")
    assert mod.heal(data, today=TODAY) == (2, 1)
    first, second = data["work_experience"]
    assert (first["end_date"], first["is_current"]) == ("2024-02", False)
    assert second["is_current"] is True  # a parallel current role stays open
    assert data["metadata"]["pending_conflicts"] == []


@pytest.mark.parametrize("name", [
    "two past dates stay", "later source says current stays", "future end stays", "unreadable stays",
    "resolved untouched", "section mismatch stays", "unknown entity stays", "other field stays",
])
def test_a_genuine_or_unreadable_dispute_stays(name):
    mod = _migration()
    data = _SCENARIOS[name].model_dump(mode="json")
    before = copy.deepcopy(data)
    assert mod.heal(data, today=TODAY) == (0, 0)
    assert data == before


def test_heal_tolerates_shapes_it_does_not_own():
    mod = _migration()
    assert mod.heal(None, today=TODAY) == (0, 0)
    assert mod.heal([], today=TODAY) == (0, 0)
    data = {"work_experience": "not a list", "metadata": {"pending_conflicts": "nope"}}
    assert mod.heal(data, today=TODAY) == (0, 0)
    data = {"work_experience": [{"end_date": "2020", "is_current": True}], "metadata": None}
    assert mod.heal(data, today=TODAY) == (0, 1)  # an id-less entry still closes (0087 repair)


# ── a real database ──────────────────────────────────────────────────────────────


def test_the_migration_heals_live_rows_on_a_real_database(caplog):
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
    emma = _SCENARIOS["emma pair"].model_dump(mode="json")
    genuine = _SCENARIOS["two past dates stay"].model_dump(mode="json")
    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {"id": "emma", "profile_json": emma, "deleted_at": None},
            {"id": "genuine", "profile_json": genuine, "deleted_at": None},
            {"id": "deleted", "profile_json": emma, "deleted_at": datetime(2026, 11, 1)},
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

    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        after = run(mod.upgrade)
    healed = after["emma"]
    assert (healed["work_experience"][0]["end_date"], healed["work_experience"][0]["is_current"]) == ("2024-02", False)
    assert healed["work_experience"][1]["is_current"] is True
    assert healed["metadata"]["pending_conflicts"] == []
    assert after["genuine"] == genuine, "a genuine two-past-dates dispute stays"
    assert after["deleted"] == emma, "a deleted vault is not touched"
    MasterProfileData.model_validate(healed)  # the app loads what the migration wrote

    log_text = " ".join(r.getMessage() for r in caplog.records if r.getMessage().startswith("0089"))
    assert "1 vault(s): 2 role-end dispute(s) dropped, 1 role(s) closed" in log_text
    assert "Mediora" not in log_text and "2024-02" not in log_text, "counts, never content"

    again = run(mod.upgrade)
    assert again == after, "a second run changes nothing"

    down = run(mod.downgrade)
    assert down == after, "downgrade restores nothing"
