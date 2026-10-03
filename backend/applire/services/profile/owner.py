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

import inspect
import uuid

from applire.services.owner_resolution import OWNER_FALLBACK_STATS, resolve_user_id

__all__ = ["OWNER_FALLBACK_STATS", "resolve_owner"]

#: wrappers skipped when naming the call site for the shared fallback counter
_WRAPPERS = frozenset({"resolve_owner", "_get_latest", "get_profile_for_user", "_latest_profile",
                       "_get_profile", "_load_profile"})


def resolve_owner(user_id: uuid.UUID | None) -> uuid.UUID:
    """``user_id`` if given, else the user owner context; else refuse.

    Delegates to the shared ``services.owner_resolution.resolve_user_id`` (main
    ruling MD-21 / 3d-1) — one ``OWNER_FALLBACK_STATS`` counter for the build;
    the site is the first caller outside the vault read wrappers.
    """
    if user_id is not None and not isinstance(user_id, uuid.UUID):
        user_id = uuid.UUID(str(user_id))
    site = "?"
    if user_id is None:
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        while caller is not None and caller.f_code.co_name in _WRAPPERS:
            caller = caller.f_back
        if caller is not None:
            mod = caller.f_globals.get("__name__", "?").removeprefix("applire.")
            site = f"{mod}.{caller.f_code.co_name}"
    return resolve_user_id(user_id, site)
