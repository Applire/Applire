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

"""GET /health — liveness, and nothing else (ADR-091 cl. 19, ADR-086/087 amended 2026-10-03).

**Three fields, frozen** (ADR-087 cl. 8 as re-stated 2026-10-03): `status`,
`edition`, `version`. The endpoint is public (the route-auth allowlist, ADR-091
cl. 20) because the compose healthcheck (`docker-compose.yml`, the backend
service's `healthcheck.test`), the CI wait loops and a self-hoster's uptime probe
read it without a credential.

Everything else it used to carry — `llm_provider`, `upgrade_notice`,
`debug_log_on`, `topology`, `ops` — moved to `GET /api/ops/health`, which needs an
admin or a probe token (founder rulings S-16, RD-1). That move broke the
four-field freeze once; the CHANGELOG `### Upgrade notes` say so.

This module still OWNS the upgrade notice's process-level cache
(`set_upgrade_notice` / `get_upgrade_notice`): the lifespan publishes into it
(`main.py`) and the admin dismissal clears it (`routers/settings.py`). Only the
reader moved — `routers/ops.py` serves it.
"""

from fastapi import APIRouter

from applire._version import __version__
from applire.config import HAS_CLOUD
from applire.schemas.admin import LivenessResponse

router = APIRouter()

#: Set by the lifespan after migrations (`main.py`). A module-level cache rather
#: than a per-request computation: the notice is a property of this process's
#: start. `None` means "nothing to report" — which is also the value before the
#: lifespan has run. Served by `GET /api/ops/health` (admin or probe), never here.
_upgrade_notice: dict | None = None


def set_upgrade_notice(notice: dict | None) -> None:
    """Publish the startup comparison's result (US310); read by the ops route."""
    global _upgrade_notice
    _upgrade_notice = notice


def get_upgrade_notice() -> dict | None:
    return _upgrade_notice


@router.get("/health", response_model=LivenessResponse)
async def health() -> LivenessResponse:
    """Public liveness. Never touches the database (it is the container healthcheck)."""
    return LivenessResponse(
        status="ok",
        edition="cloud" if HAS_CLOUD else "community",
        version=__version__,
    )
