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

"""Auth provider factory (ADR-008; ADR-091 cl. 2–3).

``AUTH_PROVIDER``:
  local — built-in accounts (email + password sessions, ``api`` bearer tokens;
          generic OIDC when ``OIDC_ISSUER`` is set). The default.
  none  — **re-meant** (MD-2): behaves exactly like ``local``; the lifespan logs a
          WARNING naming the ``.env`` line to delete. Login is always on (S-2).

``AUTH_HARNESS=true`` swaps in the fenced test harness (``auth/harness.py``)
whatever ``AUTH_PROVIDER`` says. Any other provider value raises.

``get_auth_provider`` stays THE override point (``dependency_overrides``) — the
Cloud edition registers its own providers there.
"""

from applire.config import settings
from applire.auth.base import AuthProvider

#: Values that select the built-in provider. ``none`` is the pre-0.43 value.
LOCAL_PROVIDER_VALUES = ("local", "none")

_providers: dict[str, AuthProvider] = {}


def provider_name() -> str:
    """The effective provider: ``harness`` or ``local`` (raises on an unknown value)."""
    value = (settings.auth_provider or "local").strip().lower()
    if value not in LOCAL_PROVIDER_VALUES:
        raise ValueError(
            f"Unknown AUTH_PROVIDER '{settings.auth_provider}'. "
            "Community Edition supports: local (the old value none means local too)."
        )
    return "harness" if settings.auth_harness else "local"


def auth_provider_is_re_meant() -> bool:
    """True when the operator's ``.env`` still says ``AUTH_PROVIDER=none`` (MD-2)."""
    return (settings.auth_provider or "").strip().lower() == "none"


async def get_auth_provider() -> AuthProvider:
    """Factory: the configured provider instance (one per kind, per process).

    ``async def`` (ADR-091 cl. 4): it sits in every route's dependant tree; a sync
    factory would run in the threadpool.
    """
    name = provider_name()
    provider = _providers.get(name)
    if provider is None:
        if name == "harness":
            from applire.auth.harness import HarnessAuthProvider

            provider = HarnessAuthProvider()
        else:
            from applire.auth.local import LocalAuthProvider

            provider = LocalAuthProvider()
        _providers[name] = provider
    return provider
