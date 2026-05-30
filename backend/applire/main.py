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

from applire import __version__
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
from applire.routers import application, cover_letter, cv, cv_color, documents as documents_router, flow, health, job, jobs, profile, profile_enrich, profile_roles, session
from applire.routers import settings as settings_router
from applire.routers.admin import color_schemes as admin_color_schemes
from applire.services.thumbnails import ensure_thumbnails

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_STUB_EMAIL = "local@applire.community"

STATIC_DIR = resolve_static_dir()
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, email, created_at) VALUES (:id, :email, :created_at)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_STUB_USER_ID), "email": _STUB_EMAIL, "created_at": datetime.now(timezone.utc)},
        )
        await db.commit()
    await ensure_thumbnails(STATIC_DIR)
    yield


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
