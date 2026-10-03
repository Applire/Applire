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
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

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
from applire.routers import application, cover_letter, cv, cv_color, documents as documents_router, flow, health, job, jobs, ops, profile, profile_enrich, profile_roles, session, signature
from applire.routers import settings as settings_router
from applire.routers import review as review_router  # ADR-090
from applire.routers.admin import color_schemes as admin_color_schemes
from applire.routers import auth as auth_router
from applire.routers import setup as setup_router
from applire.auth.deps import require_user
from applire.services.thumbnails import ensure_thumbnails

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
    from applire.auth import auth_provider_is_re_meant, provider_name

    provider_name()  # an unknown AUTH_PROVIDER raises here, before serving (ADR-091 cl. 2)
    if auth_provider_is_re_meant():
        _applire_logger.warning(
            "AUTH_PROVIDER=none now means AUTH_PROVIDER=local: Applire has accounts and "
            "sign-in is always on. You can delete the line AUTH_PROVIDER=none from your "
            ".env — it changes nothing any more."
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
            if await _database_holds_data(db):
                # ADR-091 cl. 16: no key but data = an upgrade from before 0062
                # (instance_state did not exist yet), NOT a fresh install —
                # report everything since the first release.
                last_seen = "0.0.0"
            else:
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


async def _database_holds_data(db) -> bool:
    """The database holds a vault (ADR-091 cl. 16: ``master_profiles`` non-empty).

    A pre-0062 instance has no ``last_seen_version`` key; if it holds a profile
    it is an upgrade, not a fresh install. (Every profile belongs to a user, so
    "a user with a profile" is the same test.)
    """
    from applire.models.profile import MasterProfile
    from applire.ownership import unscoped

    with unscoped("startup-backfill"):
        count = (await db.execute(select(func.count()).select_from(MasterProfile))).scalar_one()
    return bool(count)


async def _enforce_harness_fences() -> None:
    """ADR-091 cl. 3: with AUTH_HARNESS on, a failed fence ends the process (exit 1)."""
    if not settings.auth_harness:
        return
    from applire.auth import _seams
    from applire.auth.harness import HarnessRefused, enforce_at_startup, log_refusal

    async with AsyncSessionLocal() as db:
        try:
            await enforce_at_startup(db)
        except HarnessRefused as exc:
            log_refusal(exc)
            logging.shutdown()
            os._exit(1)
        await _seams.audit(
            db, actor_id=None, action="harness.boot", target_type="instance",
            target_id=None, details={},
        )
        await db.commit()


async def _prepare_accounts() -> None:
    """Stub row, instance secret, and the setup code (ADR-091 cl. 10, 14, 15)."""
    from applire.auth.setup import (
        ensure_instance_secret,
        ensure_stub_user,
        prepare_boot,
        setup_block,
    )

    async with AsyncSessionLocal() as db:
        await ensure_stub_user(db)
        await ensure_instance_secret(db)
        code = None if settings.auth_harness else await prepare_boot(db)
        await db.commit()
    if code is not None:
        _applire_logger.warning(setup_block(code))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_startup_posture()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    await _enforce_harness_fences()
    await _publish_upgrade_notice()
    await _prepare_accounts()
    # ADR-077 clause 1 — one-time entry-id backfill through the committer
    # module. Idempotent (skips fully-migrated profiles), so it rides every
    # startup right after the schema migration, like the migration itself.
    from applire.ownership import unscoped
    from applire.services.profile.commit import backfill_entry_ids

    async with AsyncSessionLocal() as db:
        with unscoped("startup-backfill"):
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
    # D-6: the API docs are re-mounted below behind a login.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(_user=Depends(require_user)) -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
async def swagger_docs(_user=Depends(require_user)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} — docs")


@app.get("/redoc", include_in_schema=False)
async def redoc_docs(_user=Depends(require_user)):
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} — ReDoc")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health.router)
app.include_router(auth_router.router)
app.include_router(setup_router.router)
app.include_router(ops.router)
app.include_router(job.router)
app.include_router(jobs.router)
app.include_router(profile.router)
app.include_router(signature.router)
app.include_router(profile_enrich.router)
app.include_router(profile_roles.router)
app.include_router(session.router)
app.include_router(flow.router)
app.include_router(cv.router)
app.include_router(cover_letter.router)
app.include_router(review_router.router)
app.include_router(cv_color.router)
app.include_router(settings_router.router)
app.include_router(application.router)
app.include_router(documents_router.router)
app.include_router(admin_color_schemes.router)
