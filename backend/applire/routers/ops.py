# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops endpoint (ADR-086 clause 2, amended 2026-10-03; ADR-091 cl. 19).

``GET /api/ops/health`` — the operator's instance facts, and the documented
attachment point for whatever uptime probe they already run.

Four properties are contractual and every one of them is a decision:

* **Admin or probe token only** (S-16, RD-1). An admin session (or an admin's
  ``api`` bearer) or a ``probe``-scope bearer an admin created
  (``/api/admin/probe-tokens``); everything else is 401/403
  (``auth/deps_links.admin_or_probe``). The fields that left the public
  ``/health`` — ``upgrade_notice``, ``debug_log_on``, ``topology``, ``ops`` — are
  served here.
* **Additive-only JSON.** A field may be added in any release; renaming or
  removing one is a breaking change that needs an ``### Upgrade notes`` entry in
  the CHANGELOG (US310's convention). Somebody's Uptime Kuma is parsing this.
* **The verdict is in the HTTP status** — 200 for ``ok``/``degraded``, 503 for
  ``down`` — so an alerting probe never has to parse the body. ``degraded`` is
  deliberately 200: a stale retention run or a 12 %-full disk is not a reason to
  page anyone at 3 a.m.
* **Bounded by what it says, even to an admin** (ADR-086 clause 4, ``SF-OPS.5``):
  versions, statuses, gauges and opaque ids are in; keys, paths, hostnames, the
  instance secret and the setup-token hash are out. The endpoint is read-only and
  cannot trigger a provider call — the provider facts come from a cache the
  background refresher fills (``SF-OPS.6``).

The report runs under ``unscoped("ops-aggregate")`` (ADR-092 cl. 7): it reads
instance tables only, and a probe-token request has no owner to act as.

Deliberately **not** an MCP tool (ADR-054, E060 ruling 6).
"""

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

# Integration note (1c-4): ``auth/deps.py`` re-exports this object; until then the
# route imports it from its home module so both branches stay green alone.
from applire.auth.deps_links import admin_or_probe
from applire.config import settings
from applire.db.session import get_db
from applire.models.user import User
from applire.ownership import unscoped
from applire.routers.health import get_upgrade_notice
from applire.services.ops.aggregate import cached_summary, collect
from applire.services.ops.probes import DOWN

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/health")
async def ops_health(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _auth: User | None = Depends(admin_or_probe),
) -> dict[str, Any]:
    """Aggregated instance health. 503 only when the verdict is ``down``."""
    with unscoped("ops-aggregate"):
        report = await collect(db)
    if report.get("status") == DOWN:
        response.status_code = 503
    # Moved off the public /health (RD-1). Additive keys on the report.
    return {
        **report,
        "upgrade_notice": get_upgrade_notice(),
        "debug_log_on": bool(settings.llm_debug_log),
        "topology": settings.applire_topology,
        "ops": cached_summary(),
    }
