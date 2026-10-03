# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one place package 1b calls into 1a/1c/1d code (Strawberry W1 seam).

Names agreed through the lead developer (NEEDS-EDIT 1b-3, ruling 2026-10-03).
Since the W1 integration every name below IS the partner's implementation (no
fallback, no second copy):

* 1a ``applire.auth.passwords``: ``hash_password``, ``check_password_policy``,
  ``verify_password``.
* 1a ``applire.auth.sessions``: ``issue_session``, ``revoke_user_sessions``,
  ``clear_session_cookie``.
* 1c ``applire.auth.tokens``: ``revoke_all_for_user`` (as ``revoke_all_tokens``;
  agent + api tokens, bumps ``users.link_epoch``).
* 1a ``applire.auth.roles``: ``assert_not_last_admin`` — the one last-admin
  predicate (ADR-091 cl. 7; MD-18: an active admin holds a credential).
* 1a ``applire.auth.csrf``: ``require_origin`` (cl. 12).
* 1d ``applire.auth.reauth``: ``consume_grant`` — **still resolved at call time and
  fail-closed** until 1d ships in W3 (a password-less account then gets
  ``reauth_required``).
"""

from __future__ import annotations

import importlib
import uuid

from fastapi import Request

from applire.auth.csrf import require_origin
from applire.auth.passwords import check_password_policy, hash_password, verify_password
from applire.auth.roles import assert_not_last_admin
from applire.auth.sessions import clear_session_cookie, issue_session, revoke_user_sessions
from applire.auth.tokens import revoke_all_for_user as revoke_all_tokens
from applire.models.user import User

__all__ = [
    "assert_not_last_admin",
    "check_password_policy",
    "clear_session_cookie",
    "consume_reauth_grant",
    "hash_password",
    "issue_session",
    "require_origin",
    "require_origin_dependency",
    "revoke_all_tokens",
    "revoke_user_sessions",
    "verify_password",
]


def require_origin_dependency():
    """1a's ``require_origin`` (kept as a function for the routers' import line)."""
    return require_origin


# --- re-authentication (1d, W3) ------------------------------------------------

async def consume_reauth_grant(
    db, *, request: Request, user: User, action: str, target_id: uuid.UUID
) -> bool:
    """Consume a verified fresh-OIDC grant; ``False`` while 1d has not shipped
    (a password-less account then gets ``reauth_required`` — fail closed)."""
    try:
        mod = importlib.import_module("applire.auth.reauth")
    except ImportError:
        return False
    fn = getattr(mod, "consume_grant", None)
    if fn is None:
        return False
    return bool(await fn(db, request=request, user=user, action=action, target_id=target_id))
