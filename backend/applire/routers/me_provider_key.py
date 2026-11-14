# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/me/provider-key`` — a person's own AI provider key (ADR-096 cl. 2/3).

* ``GET`` (``require_user``): ``is_set``, family, model, ``updated_at``, whether a
  stored key is unreadable, the key mode and who pays for this person's work now.
  Never the key — no mask, no last four characters.
* ``PUT`` (``require_session_user``): ``{family, model, key, test?}``. With
  ``test: true`` one minimal call with the submitted key runs first; a failed
  test stores nothing. The body is validated raw by the service, never by a
  pydantic model: FastAPI's default 422 echoes the failing input, i.e. the key
  (the ADR-093 cl. 7 precedent). ``503 provider_key_secret_missing`` without
  ``PROVIDER_KEY_SECRET``.
* ``DELETE`` (``require_session_user``): 204, idempotent.

Session only for the writes: an ``api`` bearer (an agent) must not swap the key
it spends, the reason ADR-096 cl. 2 gives for having no MCP tool (ADR-054).
"""

from __future__ import annotations

from typing import Any

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_session_user, require_user
from applire.auth.links import ProviderKeySecretMissing
from applire.db.session import get_db
from applire.models.allowance import USER_KEY_FAMILIES
from applire.models.user import User
from applire.services import allowance, audit, provider_keys

router = APIRouter(prefix="/api/me", tags=["me"])


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message, **extra})


async def _payload(db: AsyncSession, me: User) -> dict[str, Any]:
    key = await provider_keys.describe(db, me.id)
    return {
        "key_mode": allowance.key_mode(),
        "is_set": key.is_set,
        "family": key.family,
        "model": key.model,
        "updated_at": key.updated_at.isoformat() if key.updated_at else None,
        "unreadable": key.unreadable,
        "instance_key_granted": me.instance_key_granted_at is not None,
        "payer": await allowance.who_pays(db, me),
        "families": list(USER_KEY_FAMILIES),
    }


@router.get("/provider-key")
async def get_provider_key(
    me: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _payload(db, me)


@router.put("/provider-key")
async def put_provider_key(
    request: Request,
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Raw body, the ADR-093 cl. 7 precedent (routers/admin/settings.py): a
    # malformed body must never come back inside a 422 that quotes it.
    try:
        body = json.loads(await request.body() or b"null")
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        raise _error(422, "invalid_body", "Send family, model and key.")
    if not provider_keys.secret_configured():
        raise _error(
            503, "provider_key_secret_missing",
            "This instance cannot store provider keys yet (PROVIDER_KEY_SECRET is not set or shorter than 32 bytes). Ask your admin.",
        )
    try:
        family, model, key = provider_keys.validate(body.get("family"), body.get("model"), body.get("key"))
    except provider_keys.ProviderKeyError as exc:
        raise _error(exc.status, exc.code, exc.message) from None
    test = body.get("test") is True
    if test:
        retry_after = provider_keys.reserve_test_call(me.id)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail={
                    "error_code": "provider_key_test_throttled",
                    "message": "Too many key tests in a short time. Wait a few minutes, or save without the test.",
                },
                headers={"Retry-After": str(retry_after)},
            )
        try:
            ok, reason = await provider_keys.test_key(family=family, model=model, key=key)
        finally:
            provider_keys.release_test_call(me.id)
        if not ok:
            raise _error(
                422, "provider_key_test_failed",
                "The provider did not accept a test call with this key and model. Nothing was stored.",
                reason=reason,
            )
    try:
        await provider_keys.store(db, user_id=me.id, family=family, model=model, key=key)
    except provider_keys.ProviderKeyConflict:
        await db.rollback()
        raise _error(
            409, "provider_key_conflict",
            "Your key was saved from another tab at the same moment. Reload and check which key is stored.",
        ) from None
    except ProviderKeySecretMissing:  # pragma: no cover — checked above; never plaintext
        raise _error(503, "provider_key_secret_missing", "PROVIDER_KEY_SECRET is not set.") from None
    await audit.record(
        db, actor_id=me.id, action="provider_key.stored", target_type="user", target_id=me.id,
        details={"family": family, "tested": test},
    )
    await db.commit()
    return await _payload(db, me)


@router.delete("/provider-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_key(
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if await provider_keys.remove(db, me.id):
        await audit.record(
            db, actor_id=me.id, action="provider_key.removed", target_type="user", target_id=me.id,
            details={},
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
