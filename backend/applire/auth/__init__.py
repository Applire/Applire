# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

from applire.config import settings
from applire.auth.base import AuthProvider


async def get_auth_provider() -> AuthProvider:
    """Factory: instantiate the configured auth provider (ADR 008).

    ``async def`` (Strawberry W0, ADR-091 cl. 4): it sits in the dependant tree of
    every route through ``applire.auth.deps``, and a sync dependency would run in
    the threadpool. It stays THE override point (``dependency_overrides``).

    Controlled by the AUTH_PROVIDER environment variable:
      none  — NoAuthProvider; fixed single-user stub, zero enforcement (default)

    Cloud Edition backends (zitadel, oidc, apikey) are not part of this
    distribution and must be registered here by the Cloud layer.
    """
    provider = settings.auth_provider.lower()

    if provider == "none":
        from applire.auth.no_auth import NoAuthProvider
        return NoAuthProvider()

    raise ValueError(
        f"Unknown AUTH_PROVIDER '{settings.auth_provider}'. "
        "Community Edition supports: none. "
        "Cloud Edition backends (zitadel, oidc, apikey) are not included."
    )
