# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The 1d routes satisfy the route-auth inventory rule (ADR-091 cl. 20) before
``main.py`` wires them: the two callback-flow GETs are the allowlisted public
ones, every other route depends on ``require_session_user`` (credential
management is session only, cl. 17), and every dependency is ``async def`` (cl. 4).
The real-app inventory (``tests/unit/test_route_auth_inventory.py``) covers them
once included."""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from applire.auth.deps import require_session_user
from applire.routers import auth_oidc, me_oidc, me_reauth

PUBLIC = {("GET", "/api/auth/oidc/start"), ("GET", "/api/auth/oidc/callback")}


def _calls(route: APIRoute) -> set:
    out = set()

    def walk(dep):
        for sub in dep.dependencies:
            out.add(sub.call)
            walk(sub)

    walk(route.dependant)
    return out


def test_1d_routes_are_public_only_where_allowlisted_else_session_only():
    seen = set()
    for router in (auth_oidc.router, me_oidc.router, me_reauth.router):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                key = (method, route.path)
                seen.add(key)
                if key in PUBLIC:
                    assert method == "GET"
                    continue
                assert require_session_user in _calls(route), key
    assert seen == PUBLIC | {
        ("POST", "/api/me/oidc/link"),
        ("DELETE", "/api/me/oidc"),
        ("POST", "/api/me/reauth/start"),
    }


def test_1d_dependencies_and_endpoints_are_async():
    for router in (auth_oidc.router, me_oidc.router, me_reauth.router):
        for route in router.routes:
            assert inspect.iscoroutinefunction(route.endpoint), route.path
            for call in _calls(route):
                if inspect.isfunction(call):
                    assert inspect.iscoroutinefunction(call) or inspect.isasyncgenfunction(call), (
                        route.path, call)
