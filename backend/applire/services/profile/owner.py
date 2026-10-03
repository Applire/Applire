# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whose vault — the owner resolution of the profile side (ADR-092 cl. 2, 14; US333).

Every vault read and write in ``services/profile/**``, ``photo``, ``signature``,
``fact_pins`` and ``matching`` is keyed on a user. The doors pass ``user_id``
explicitly (REST: the resolved ``current_user``; MCP: the token's user; background
tasks: the user they were handed). A caller that passes nothing gets the **user**
owner context of its execution context (set by the auth dependency, the MCP
identity or ``owner_context``) — never "the newest row of anyone", which is what
the pre-Strawberry ``_get_latest`` read. With neither, the read refuses
(:class:`~applire.ownership.OwnerContextMissing`) instead of guessing: a vault read
without an owner is exactly the cross-user read ADR-092 exists to rule out.
"""

from __future__ import annotations

import collections
import inspect
import uuid

from applire import ownership

__all__ = ["OWNER_FALLBACK_STATS", "resolve_owner"]

#: W2 convention (main ruling 3d-1): every hit where a caller passed no
#: ``user_id`` and the owner came from the execution context, keyed by the
#: calling function — reported per package; 3e (W3) makes the parameter strict.
OWNER_FALLBACK_STATS: "collections.Counter[str]" = collections.Counter()


def resolve_owner(user_id: uuid.UUID | None) -> uuid.UUID:
    """``user_id`` if given, else the user owner context; else refuse."""
    if user_id is not None:
        if not isinstance(user_id, uuid.UUID):
            user_id = uuid.UUID(str(user_id))
        return user_id
    ctx = ownership.current_owner()
    if ctx is not None and ctx.user_id is not None:
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        # skip the thin wrappers so the counter names the real caller
        while caller is not None and caller.f_code.co_name in ("_get_latest", "get_profile_for_user", "_owner"):
            caller = caller.f_back
        if caller is not None:
            mod = caller.f_globals.get("__name__", "?").removeprefix("applire.")
            OWNER_FALLBACK_STATS[f"{mod}.{caller.f_code.co_name}"] += 1
        return ctx.user_id
    raise ownership.OwnerContextMissing(
        "a vault read/write named no owner: pass user_id or run under "
        "owner_context(user_id) (ADR-092 cl. 2/14)"
    )
