# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-export shim — the owner helpers live in ``services.owner_resolution`` (MD-24 (4)).

New code imports from :mod:`applire.services.owner_resolution`.
"""

from applire.services.owner_resolution import (
    OWNER_FALLBACK_STATS,
    job_for_user,
    owned_cv,
    resolve_owner,
)

__all__ = [
    "OWNER_FALLBACK_STATS",
    "resolve_owner",
    "owned_cv",
    "job_for_user",
]
