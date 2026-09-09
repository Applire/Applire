# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops endpoint (ADR-086 clause 2).

``GET /api/ops/health`` — the operator's instance facts, and the documented
attachment point for whatever uptime probe they already run.

Three properties are contractual and every one of them is a decision:

* **Additive-only JSON.** A field may be added in any release; renaming or
  removing one is a breaking change that needs an ``### Upgrade notes`` entry in
  the CHANGELOG (US310's convention). Somebody's Uptime Kuma is parsing this.
* **The verdict is in the HTTP status** — 200 for ``ok``/``degraded``, 503 for
  ``down`` — so an alerting probe never has to parse the body. ``degraded`` is
  deliberately 200: a stale retention run or a 12 %-full disk is not a reason to
  page anyone at 3 a.m., and a probe that cried wolf would be muted within a week.
* **Unauthenticated, and bounded by what it says.** Community ships
  ``AUTH_PROVIDER=none`` (ADR-022, ADR-053), so there is nothing to hide behind:
  versions, statuses, gauges and opaque ids are in; keys, paths, hostnames and
  the exact model id are out (ADR-086 clause 4, ``SF-OPS.5``). The endpoint is
  read-only and cannot trigger a provider call — the provider facts come from a
  cache the background refresher fills, so nobody can spend the operator's
  credit by curling this URL (``SF-OPS.6``).

Deliberately **not** an MCP tool (ADR-054, E060 ruling 6): an agent that wants
instance facts reads this over HTTP like any other probe.
"""

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import get_db
from applire.services.ops.aggregate import collect
from applire.services.ops.probes import DOWN

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/health")
async def ops_health(
    response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Aggregated instance health. 503 only when the verdict is ``down``."""
    report = await collect(db)
    if report.get("status") == DOWN:
        response.status_code = 503
    return report
