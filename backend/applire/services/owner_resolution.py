# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner resolution for package-4a service functions (Strawberry W2, ruling 3d-1).

The F5 keyword ``user_id: uuid.UUID | None = None`` stays on every public
service function. ``None`` falls back to the USER owner context
(``ownership.current_owner()``); an unscoped context or no context at all raises
``OwnerContextMissing`` — a per-user operation never runs without naming whose
rows it touches (ADR-092 cl. 8). Every fallback hit is counted per call site in
``OWNER_FALLBACK_STATS`` so W3 (3e) can see which callers still rely on it and
make the parameter required.
"""

from __future__ import annotations

import collections
import logging
import uuid

from applire import ownership

__all__ = ["OWNER_FALLBACK_STATS", "resolve_user_id"]

_log = logging.getLogger(__name__)

#: ``"<module>.<function>"`` → number of calls that relied on the context fallback.
OWNER_FALLBACK_STATS: "collections.Counter[str]" = collections.Counter()


def resolve_user_id(user_id: uuid.UUID | None, site: str) -> uuid.UUID:
    """``user_id`` if given, else the USER owner context; raise when neither exists."""
    if user_id is not None:
        return user_id
    ctx = ownership.current_owner()
    if ctx is None or ctx.is_unscoped or ctx.user_id is None:
        raise ownership.OwnerContextMissing(
            f"{site}: no user_id and no user owner context (ADR-092 cl. 8, ruling 3d-1)"
        )
    OWNER_FALLBACK_STATS[site] += 1
    _log.debug("owner fallback from context at %s", site)
    return ctx.user_id
