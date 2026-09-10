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

"""ADR-087 (settings registry / operator posture): the DB layer, the two
routers, and the startup-posture logging that sit on top of the registry.

Covers:
  * `applire.models.instance_state` / `applire.services.instance_state` —
    the key/value store's round-trip and its two ValueError guards;
  * `GET /health` — the frozen-fields contract (cl. 8) plus the additive
    `upgrade_notice` / `debug_log_on` / `topology` fields;
  * `POST /api/settings/upgrade-notice/dismiss` — the one thing that is
    allowed to advance `last_seen_version` (cl. 7);
  * `applire.main._log_startup_posture` — the two conditions it warns about.

DB tests build an in-memory SQLite session the same way
`tests/unit/test_settings_endpoint.py`'s `db` fixture does (that file tests
the sibling router in this same package). `/health`'s module-level
`_upgrade_notice` is process-global state, so every test that touches it
goes through the `clean_upgrade_notice` fixture below (autouse) rather than
relying on `set_upgrade_notice(None)` calls scattered through the test
bodies — a fixture with teardown is the only way to guarantee this file's
state does not leak into a test file that runs after it in the same
process.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """An in-memory SQLite session with every model's table created.

    Mirrors `tests/unit/test_settings_endpoint.py`'s `db` fixture: import the
    same explicit set of model modules that fixture does (`applire.models`'s
    own `__init__.py` does NOT import `cover_letter`, and `applications` has
    an FK to `generated_cover_letters` — `Base.metadata.create_all` fails on
    an unresolved FK if that module is not imported first) plus
    `instance_state` itself, the one this file actually exercises.
    """
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.instance_state  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def health_client():
    """A bare FastAPI app carrying only the health router — no DB, no auth,
    no lifespan (so no alembic subprocess call and no real Postgres)."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.routers.health import router

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def clean_upgrade_notice():
    """Reset `/health`'s module-level `_upgrade_notice` around every test in
    this file, so a test that publishes a notice cannot leak it into a test
    that runs after it (in this file, or in another file in the same run)."""
    from applire.routers.health import set_upgrade_notice

    set_upgrade_notice(None)
    yield
    set_upgrade_notice(None)


# ===========================================================================
# 10. instance_state round-trip
# ===========================================================================


@pytest.mark.asyncio
async def test_write_then_read_returns_the_written_value(db):
    from applire.services.instance_state import (
        KEY_LAST_SEEN_VERSION,
        read_state,
        write_state,
    )

    await write_state(db, KEY_LAST_SEEN_VERSION, "0.40.0")
    await db.commit()

    value = await read_state(db, KEY_LAST_SEEN_VERSION)
    assert value == "0.40.0"


@pytest.mark.asyncio
async def test_a_second_write_updates_the_row_rather_than_duplicating_it(db):
    from applire.models.instance_state import InstanceState
    from applire.services.instance_state import (
        KEY_LAST_SEEN_VERSION,
        read_state,
        write_state,
    )

    await write_state(db, KEY_LAST_SEEN_VERSION, "0.40.0")
    await db.commit()
    await write_state(db, KEY_LAST_SEEN_VERSION, "0.41.0")
    await db.commit()

    count = await db.execute(
        select(func.count())
        .select_from(InstanceState)
        .where(InstanceState.key == KEY_LAST_SEEN_VERSION)
    )
    assert count.scalar_one() == 1

    value = await read_state(db, KEY_LAST_SEEN_VERSION)
    assert value == "0.41.0"


@pytest.mark.asyncio
async def test_an_unknown_key_raises_valueerror_on_read(db):
    from applire.services.instance_state import read_state

    with pytest.raises(ValueError):
        await read_state(db, "not_a_declared_key")


@pytest.mark.asyncio
async def test_an_unknown_key_raises_valueerror_on_write(db):
    from applire.services.instance_state import write_state

    with pytest.raises(ValueError):
        await write_state(db, "not_a_declared_key", "some-value")


@pytest.mark.asyncio
async def test_a_key_never_written_reads_as_none(db):
    from applire.services.instance_state import KEY_LAST_BACKUP_AT, read_state

    value = await read_state(db, KEY_LAST_BACKUP_AT)
    assert value is None


# ===========================================================================
# 11. /health keeps its four original fields
# ===========================================================================


@pytest.mark.asyncio
async def test_health_four_original_fields_are_frozen(health_client):
    # ADR-087 cl. 8: `status`, `edition`, `version`, `llm_provider` are read
    # by the compose healthcheck (docker-compose.yml's backend service),
    # the pre-release install gate, and whatever uptime probe a self-hoster
    # points at the instance — they must keep their names, types and values
    # no matter what gets added around them.
    from applire._version import __version__
    from applire.config import HAS_CLOUD, settings

    resp = await health_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["edition"] == ("cloud" if HAS_CLOUD else "community")
    assert body["version"] == __version__
    assert body["llm_provider"] == settings.llm_provider
    assert isinstance(body["status"], str)
    assert isinstance(body["edition"], str)
    assert isinstance(body["version"], str)
    assert isinstance(body["llm_provider"], str)


@pytest.mark.asyncio
async def test_health_additive_fields_default_to_nothing_to_report(health_client):
    from applire.config import settings

    resp = await health_client.get("/health")
    body = resp.json()

    assert body["upgrade_notice"] is None
    assert body["debug_log_on"] == bool(settings.llm_debug_log)
    assert body["topology"] == settings.applire_topology


@pytest.mark.asyncio
async def test_health_upgrade_notice_reflects_whatever_was_published(health_client):
    from applire.routers.health import set_upgrade_notice

    notice = {"from": "0.30.0", "to": "0.41.0", "unset": [], "re_meant": []}
    set_upgrade_notice(notice)

    resp = await health_client.get("/health")
    assert resp.json()["upgrade_notice"] == notice


# ===========================================================================
# 12. the dismiss endpoint advances last-seen and clears the notice
# ===========================================================================


@pytest.mark.asyncio
async def test_dismiss_advances_last_seen_and_clears_the_health_notice(db):
    from applire._version import __version__
    from applire.routers.health import get_upgrade_notice, set_upgrade_notice
    from applire.routers.settings import api_dismiss_upgrade_notice
    from applire.services.instance_state import (
        KEY_LAST_SEEN_VERSION,
        KEY_UPGRADE_NOTICE_DISMISSED_FOR,
        read_state,
    )

    set_upgrade_notice(
        {"from": "0.30.0", "to": __version__, "unset": [], "re_meant": []}
    )

    result = await api_dismiss_upgrade_notice(db=db, _auth=MagicMock())

    assert result.last_seen_version == __version__
    assert result.upgrade_notice is None

    last_seen = await read_state(db, KEY_LAST_SEEN_VERSION)
    dismissed_for = await read_state(db, KEY_UPGRADE_NOTICE_DISMISSED_FOR)
    assert last_seen == __version__
    assert dismissed_for == __version__

    assert get_upgrade_notice() is None


# ===========================================================================
# 13. _log_startup_posture warns exactly when it should — four small tests
# ===========================================================================


@pytest.fixture
def main_module():
    """Import `applire.main` lazily (heavy router-import chain) and give the
    test both the module and `applire.config` — patching goes through the
    config module's `settings` object, which `applire.main` imported by
    reference (`from applire.config import ... settings`), so a
    `monkeypatch.setattr(cfg.settings, ...)` is visible to `applire.main`
    too without patching two separate objects."""
    import applire.config as cfg
    import applire.main as main

    return main, cfg


def test_startup_posture_warns_about_debug_log_when_it_is_on(main_module, monkeypatch, caplog):
    main, cfg = main_module
    monkeypatch.setattr(cfg.settings, "llm_debug_log", True)
    monkeypatch.setattr(cfg.settings, "llm_debug_log_dir", "logs/llm")
    monkeypatch.setattr(cfg.settings, "applire_topology", "production")

    with caplog.at_level(logging.WARNING, logger="applire"):
        main._log_startup_posture()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "logs/llm" in warnings[0].getMessage()


def test_startup_posture_says_nothing_when_debug_log_is_off(main_module, monkeypatch, caplog):
    main, cfg = main_module
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)
    monkeypatch.setattr(cfg.settings, "applire_topology", "production")

    with caplog.at_level(logging.WARNING, logger="applire"):
        main._log_startup_posture()

    assert caplog.records == []


def test_startup_posture_warns_about_dev_topology_naming_both_ports(main_module, monkeypatch, caplog):
    main, cfg = main_module
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)
    monkeypatch.setattr(cfg.settings, "applire_topology", "dev")

    with caplog.at_level(logging.WARNING, logger="applire"):
        main._log_startup_posture()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "8001" in message
    assert "5433" in message


def test_startup_posture_says_nothing_in_production_topology(main_module, monkeypatch, caplog):
    main, cfg = main_module
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)
    monkeypatch.setattr(cfg.settings, "applire_topology", "production")

    with caplog.at_level(logging.WARNING, logger="applire"):
        main._log_startup_posture()

    assert caplog.records == []
