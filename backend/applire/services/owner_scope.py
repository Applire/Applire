# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Re-export shim — the owner helpers live in ``services.owner_resolution`` (MD-24 (4)).

New code imports from :mod:`applire.services.owner_resolution`.
"""

from applire.services.owner_resolution import owned_row, resolve_user_id

__all__ = ["owned_row", "resolve_user_id"]
