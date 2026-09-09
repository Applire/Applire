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

import logging
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from applire._version import __version__
from applire.config import resolve_static_dir, settings

# Attach a StreamHandler directly to the applire logger so records don't rely on the
# root logger's handler chain (uvicorn's dictConfig only registers handlers for its
# own loggers — propagated records would otherwise be silently dropped).
_applire_logger = logging.getLogger("applire")
_applire_logger.setLevel(settings.log_level.upper())
if not _applire_logger.handlers:
    _applire_handler = logging.StreamHandler()
    _applire_handler.setFormatter(
        logging.Formatter("%(levelname)s [%(name)s] %(message)s")
    )
    _applire_logger.addHandler(_applire_handler)
from applire.db.session import AsyncSessionLocal
from applire.routers import application, cover_letter, cv, cv_color, documents as documents_router, flow, health, job, jobs, ops, profile, profile_enrich, profile_roles, session
from applire.routers import settings as settings_router
from applire.routers.admin import color_schemes as admin_color_schemes
from applire.services.thumbnails import ensure_thumbnails

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_STUB_EMAIL = "local@applire.community"

STATIC_DIR = resolve_static_dir()
STATIC_DIR.mkdir(parents=True, exist_ok=True)


def _log_startup_posture() -> None:
    """Say out loud what this instance's configuration means (ADR-087 cl. 9).

    Both of these are conditions an operator sets deliberately and then forgets,
    and both were previously visible nowhere: the debug log records CV PII
    (JF-O-4.1) and the dev topology publishes an unauthenticated API on :8001 and
    Postgres on :5433 with the default credentials (JF-O-1.2).
    """
    if settings.llm_debug_log:
        _applire_logger.warning(
            "LLM_DEBUG_LOG is ON. Every prompt and completion — including CV and "
            "interview PII — is written to %s/<date>.jsonl and kept until you "
            "delete it. There is no size or age cap by design. Turn this off in "
            "production (LLM_DEBUG_LOG=false) and remove the files.",
            settings.llm_debug_log_dir,
        )
    if settings.applire_topology.strip().lower() == "dev":
        _applire_logger.warning(
            "APPLIRE_TOPOLOGY=dev — the development compose override is applied. "
            "This publishes the API on :8001 without authentication and PostgreSQL "
            "on :5433 with the compose default credentials, and runs the backend "
            "with hot reload. For a real install use: "
            "docker compose -f docker-compose.yml up -d"
        )


async def _publish_upgrade_notice() -> None:
    """Compare last-seen against running version and report the difference (US310).

    Runs AFTER `alembic upgrade head`, so `instance_state` is guaranteed to exist.
    `last_seen_version` is written here only for a FRESH install (absent key); on
    every other path it is advanced by the dismissal alone (ADR-087 cl. 7) — a
    message about a silent change must not itself be visible for one boot only.
    """
    from applire.routers.health import set_upgrade_notice
    from applire.services.instance_state import (
        KEY_LAST_SEEN_VERSION,
        KEY_UPGRADE_NOTICE_DISMISSED_FOR,
        read_state,
        write_state,
    )
    from applire.settings_registry import (
        compute_upgrade_notice,
        current_environment,
        format_upgrade_notice_log,
    )

    async with AsyncSessionLocal() as db:
        last_seen = await read_state(db, KEY_LAST_SEEN_VERSION)
        if not isinstance(last_seen, str) or not last_seen:
            # Fresh install: record what ran and say nothing. Reporting every
            # setting introduced since 0.31.0 to someone installing today would
            # be noise, and there is no upgrade to describe.
            await write_state(db, KEY_LAST_SEEN_VERSION, __version__)
            await db.commit()
            set_upgrade_notice(None)
            return

        dismissed_for = await read_state(db, KEY_UPGRADE_NOTICE_DISMISSED_FOR)

    notice = compute_upgrade_notice(
        last_seen=last_seen,
        running=__version__,
        environ=current_environment(),
    )
    if notice is not None and dismissed_for == __version__:
        notice = None
    if notice is not None:
        _applire_logger.warning(format_upgrade_notice_log(notice))
    set_upgrade_notice(notice)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_startup_posture()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    await _publish_upgrade_notice()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, email, created_at) VALUES (:id, :email, :created_at)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_STUB_USER_ID), "email": _STUB_EMAIL, "created_at": datetime.now(timezone.utc)},
        )
        await db.commit()
    # ADR-077 clause 1 — one-time entry-id backfill through the committer
    # module. Idempotent (skips fully-migrated profiles), so it rides every
    # startup right after the schema migration, like the migration itself.
    from applire.services.profile.commit import backfill_entry_ids

    async with AsyncSessionLocal() as db:
        await backfill_entry_ids(db)
        await db.commit()
    await ensure_thumbnails(STATIC_DIR)
    # ADR-086 clause 9 — the ops verdict (and the WARNING that follows a change) is
    # computed on a timer, because after hand-over the operator is not watching.
    from applire.services.ops.aggregate import start_ops_refresh, stop_ops_refresh
    start_ops_refresh()
    yield
    await stop_ops_refresh()


app = FastAPI(
    title="Applire API",
    description="AI-powered DACH CV tailoring — Community Edition",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health.router)
app.include_router(ops.router)
app.include_router(job.router)
app.include_router(jobs.router)
app.include_router(profile.router)
app.include_router(profile_enrich.router)
app.include_router(profile_roles.router)
app.include_router(session.router)
app.include_router(flow.router)
app.include_router(cv.router)
app.include_router(cover_letter.router)
app.include_router(cv_color.router)
app.include_router(settings_router.router)
app.include_router(application.router)
app.include_router(documents_router.router)
app.include_router(admin_color_schemes.router)
