# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/me/allowance``, ``/api/me/plan``, ``/api/me/purchases`` — ADR-096
amended 2026-11-23 (phase 2; rulings K-3a…K-3f).

* ``GET /api/me/allowance`` (``require_user``): the policy, what is left this
  month (from the ledger, cl. 11), ``renews_on`` and **``purchasing_available``**
  — the one capability field: true only when a plan provider is registered and
  ``ALLOWANCE_POLICY=allowance``. A Community install always reads false, and
  the frontend renders no plan or purchase UI then (K-3e).
* ``PUT /api/me/plan`` and ``POST /api/me/purchases`` (``require_session_user``,
  like key writes: an agent does not spend the person's money; no MCP tool):
  delegate to the registered provider. Without one: ``402
  purchasing_unavailable`` (the Cloud-only convention).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_session_user, require_user
from applire.db.session import get_db
from applire.models.user import User
from applire.services import allowance, audit
from applire.services.allowance_seam import PlanError, plan_provider

router = APIRouter(prefix="/api/me", tags=["me"])

_MAX_ID = 64


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


def _provider_or_402():
    provider = plan_provider()
    if provider is None or allowance.policy() != "allowance":
        raise _error(402, "purchasing_unavailable", "Plans and purchases are not available on this instance.")
    return provider


def _short_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_ID:
        raise _error(422, "invalid_request", f"'{field}' must be a short string.")
    return value.strip()


@router.get("/allowance")
async def get_allowance(
    me: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await allowance.allowance_status(db, me)


@router.put("/plan")
async def put_plan(
    body: dict = Body(...),
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    provider = _provider_or_402()
    plan_id = _short_id(body.get("plan_id") if isinstance(body, dict) else None, "plan_id")
    subject = await allowance.ensure_subject(db, me)
    try:
        result = await provider.change_plan(db, user=me, subject=subject, plan_id=plan_id)
    except PlanError as exc:
        raise _error(exc.status, exc.code, exc.message) from None
    if result.get("change") != "unchanged":
        await audit.record(
            db, actor_id=me.id, action="plan.changed", target_type="user", target_id=me.id,
            details={
                "change": result.get("change"),
                "from_plan": result.get("from_plan"),
                "to_plan": result.get("to_plan"),
                "simulated": bool(result.get("simulated", True)),
            },
        )
    await db.commit()
    return {"change": result.get("change"), "allowance": await allowance.allowance_status(db, me)}


@router.post("/purchases", status_code=201)
async def post_purchase(
    body: dict = Body(...),
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    provider = _provider_or_402()
    data = body if isinstance(body, dict) else {}
    pack_id = _short_id(data.get("pack_id"), "pack_id")
    key = _short_id(data.get("idempotency_key"), "idempotency_key")
    subject = await allowance.ensure_subject(db, me)
    try:
        result = await provider.purchase(
            db, user=me, subject=subject, pack_id=pack_id, idempotency_key=key
        )
    except PlanError as exc:
        raise _error(exc.status, exc.code, exc.message) from None
    if result.get("created", True):
        await audit.record(
            db, actor_id=me.id, action="purchase.recorded", target_type="user", target_id=me.id,
            details={
                "purchase_id": str(result.get("purchase_id")),
                "pack_id": pack_id,
                "applications": int(result.get("applications", 0)),
                "simulated": bool(result.get("simulated", True)),
            },
        )
    await db.commit()
    return {"purchase": result, "allowance": await allowance.allowance_status(db, me)}
