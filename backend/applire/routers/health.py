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

"""GET /health — the one endpoint every probe reads.

**The first four fields are frozen** (ADR-087 clause 8). `status`, `edition`,
`version` and `llm_provider` are consumed by the compose healthcheck
(`docker-compose.yml`, the backend service's `healthcheck.test`), by the
pre-release install gate, and by whatever uptime probe a self-hoster points at
the instance. They keep their names, their types and their values.

Everything added since is additive and ignorable: a client that does not know a
key simply does not read it, and every new key has a default that means "nothing
to say" (`upgrade_notice: null`).
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from applire._version import __version__
from applire.config import HAS_CLOUD, settings
from applire.services.ops.aggregate import cached_summary

router = APIRouter()

#: Set by the lifespan after migrations (`main.py`). A module-level cache rather
#: than a per-request computation: the notice is a property of this process's
#: start, the comparison touches the database, and `/health` is polled every 30 s
#: by the compose healthcheck. `None` means "nothing to report" — which is also
#: the value before the lifespan has run, so a probe that arrives early gets the
#: honest "no notice", never a half-computed one.
_upgrade_notice: dict | None = None


def set_upgrade_notice(notice: dict | None) -> None:
    """Publish the startup comparison's result to `/health` (US310)."""
    global _upgrade_notice
    _upgrade_notice = notice


def get_upgrade_notice() -> dict | None:
    return _upgrade_notice


class HealthResponse(BaseModel):
    # --- frozen (ADR-087 cl. 8) -------------------------------------------
    status: str
    edition: str
    version: str
    llm_provider: str
    # --- additive ---------------------------------------------------------
    #: null when there is nothing to report: a fresh install, an unchanged
    #: version, or a jump that introduced nothing this environment misses.
    upgrade_notice: dict[str, Any] | None = None
    #: True while LLM_DEBUG_LOG is on. That log records CV PII, and it has no
    #: size or age cap by decision (ADR-087 cl. 9) — so it is reported instead.
    debug_log_on: bool = False
    #: "production" (docker-compose.yml alone) or "dev" (the override file is
    #: also applied — 3000/8001/5433 published, hot-reload backend).
    topology: str = "production"
    #: ADR-086 clause 3 — served from the ops layer's cache, NEVER computed here:
    #: this endpoint is the container healthcheck (docker-compose.yml).
    ops: dict[str, Any] | None = None


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        edition="cloud" if HAS_CLOUD else "community",
        version=__version__,
        llm_provider=settings.llm_provider,
        ops=cached_summary(),
        upgrade_notice=_upgrade_notice,
        debug_log_on=bool(settings.llm_debug_log),
        topology=settings.applire_topology,
    )
