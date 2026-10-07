# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``POST /api/setup`` — claim the instance with the per-boot code (ADR-091 cl. 14).

Public by necessity, so: origin-checked, its own client throttle bucket (not
shared with login, so exhausting one cannot block the other), ``compare_digest``
on the code hash, the atomic claim in ``auth/setup.py``, refused with 409
``harness_active`` while the test harness is on (MD-9). Success signs the new
admin in (204 + session cookie; contract W0-3) and audits ``setup.claimed``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.services.audit import record as audit_record
from applire.auth.csrf import require_origin
from applire.auth.harness import STUB_USER_ID, forget_credential_cache
from applire.auth.passwords import check_password_policy, hash_password
from applire.auth.sessions import issue_session
from applire.auth.setup import claim_stub, setup_code_matches, setup_required
from applire.auth.throttle import ThrottleSaturated, setup_key, setup_throttle
from applire.config import settings
from applire.db.session import get_db
from applire.models.user import User
from applire.routers.auth import auth_error
from applire.schemas.auth import SetupRequest
from applire.services.instance_state import KEY_AUTH_SETUP_TOKEN_HASH, read_state

router = APIRouter(tags=["auth"])


@router.post(
    "/api/setup",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[Depends(require_origin)],
)
async def claim_instance(
    body: SetupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    if settings.auth_harness:
        raise auth_error(
            409, "harness_active", "The test harness is serving this instance; setup is off."
        )
    key = setup_key(request)
    async with setup_throttle.attempt(key):  # MD-35: one attempt per key at a time
        try:
            await setup_throttle.wait(key)
        except ThrottleSaturated:  # queue bound reached: refused unchecked
            raise auth_error(403, "invalid_setup_token",
                             "This setup code does not match. After a restart only the most "
                             "recently printed code works — check the log for the latest one.") from None
        if not await setup_required(db):
            raise auth_error(409, "setup_done", "This instance is already set up.")
        stored = await read_state(db, KEY_AUTH_SETUP_TOKEN_HASH)
        if not isinstance(stored, str) or not stored:
            # MD-40: the hash is gone because a concurrent claim already committed
            # (claim_stub deletes it) — the instance is set up, the code was not wrong.
            raise auth_error(409, "setup_done", "This instance is already set up.")
        if not setup_code_matches(body.setup_token, stored):
            setup_throttle.record_failure(key)
            raise auth_error(
                403,
                "invalid_setup_token",
                "This setup code does not match. After a restart only the most recently "
                "printed code works — check the log for the latest one.",
            )
        try:
            check_password_policy(body.password, body.email)
        except ValueError as exc:
            raise auth_error(422, "password_policy", str(exc)) from exc
        password_hash = await hash_password(body.password)
        try:
            claimed = await claim_stub(db, email=body.email, password_hash=password_hash)
        except IntegrityError:
            await db.rollback()
            raise auth_error(409, "email_taken", "An account with this email already exists.")
        if not claimed:
            await db.rollback()
            raise auth_error(409, "setup_done", "This instance is already set up.")
        setup_throttle.record_success(key)
        user = await db.get(User, STUB_USER_ID)
        await db.refresh(user)
        await audit_record(
            db,
            actor_id=user.id,
            action="setup.claimed",
            target_type="user",
            target_id=user.id,
            details={"via": "web"},
        )
        await issue_session(db, user, response)
        await db.commit()
        forget_credential_cache()
