# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every route is authenticated or deliberately public (ADR-091 cl. 20; D-3; US319; SF-IAM.8).

Walks every ``APIRoute``'s dependant tree for one of the five dependencies in
``applire.auth.deps``. A route without one must be on the closed allowlist
below (cl. 20), and every allowlisted *unsafe* route must carry
``applire.auth.csrf.require_origin`` (cl. 12 — login CSRF, setup, redeem).

The checker is itself mutation-tested on synthetic apps: an unguarded route, a
public POST without the origin dependency, and a decorative dependency that is
not one of the five are each reported.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount

from applire.auth.csrf import require_origin
from applire.auth.deps import AUTH_DEPENDENCIES, require_admin, require_user

#: ADR-091 cl. 20 — closed. Adding a line here is an ADR amendment.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/api/auth/state"),
        ("POST", "/api/setup"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/oidc/start"),
        ("GET", "/api/auth/oidc/callback"),
        ("POST", "/api/auth/forgot"),
        ("POST", "/api/auth/links/inspect"),
        ("POST", "/api/auth/links/redeem"),
        ("GET", "/api/admin/color-schemes/active"),
    }
)
PUBLIC_MOUNTS = frozenset({"/static"})
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _calls(route: APIRoute) -> set:
    out = set()

    def walk(dep):
        for sub in dep.dependencies:
            out.add(sub.call)
            walk(sub)

    walk(route.dependant)
    return out


def audit(app: FastAPI) -> list[str]:
    """Human-readable violations; empty = compliant."""
    problems = []
    auth = set(AUTH_DEPENDENCIES)
    for route in app.routes:
        if isinstance(route, Mount):
            if route.path not in PUBLIC_MOUNTS:
                problems.append(f"mount {route.path} is not allowlisted")
            continue
        if not isinstance(route, APIRoute):
            problems.append(f"non-API route {getattr(route, 'path', route)!r} is not allowlisted")
            continue
        calls = _calls(route)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            key = (method, route.path)
            if calls & auth:
                continue
            if key not in PUBLIC_ROUTES:
                problems.append(f"{method} {route.path} has no auth dependency")
            elif method in UNSAFE and require_origin not in calls:
                problems.append(f"{method} {route.path} is public and unsafe without require_origin")
    return problems


def test_the_real_app_has_no_unauthenticated_route():
    from applire.main import app

    assert audit(app) == []


def test_the_real_app_has_routes_and_mounts_the_docs_behind_login():
    from applire.main import app

    paths = {r.path: r for r in app.routes if isinstance(r, APIRoute)}
    assert len(paths) > 90
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert require_user in _calls(paths[p]), p
    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None


def test_public_routes_present_on_this_branch_are_exactly_allowlisted():
    from applire.main import app

    auth = set(AUTH_DEPENDENCIES)
    public = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and not (_calls(r) & auth)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    assert public <= PUBLIC_ROUTES
    assert {("GET", "/health"), ("GET", "/api/auth/state"), ("POST", "/api/auth/login"),
            ("POST", "/api/setup"), ("GET", "/api/admin/color-schemes/active")} <= public


# --- mutation tests of the checker itself ----------------------------------


def _bare() -> FastAPI:
    return FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def test_checker_reports_fastapis_default_anonymous_docs():
    """D-6: the stock /docs, /redoc, /openapi.json are anonymous — the checker sees them."""
    problems = audit(FastAPI())
    assert any("/openapi.json" in p for p in problems) and any("/docs" in p for p in problems)


def test_checker_reports_an_unguarded_route():
    app = _bare()

    @app.get("/api/leaky")
    async def leaky():
        return {}

    assert audit(app) == ["GET /api/leaky has no auth dependency"]


def test_checker_reports_a_decorative_non_auth_dependency():
    app = _bare()

    async def looks_like_auth():
        return None

    @app.delete("/api/thing")
    async def thing(_auth=Depends(looks_like_auth)):
        return {}

    assert audit(app) == ["DELETE /api/thing has no auth dependency"]


def test_checker_reports_a_public_post_without_the_origin_check():
    app = _bare()

    @app.post("/api/auth/login")
    async def login():
        return {}

    assert audit(app) == ["POST /api/auth/login is public and unsafe without require_origin"]


def test_checker_accepts_a_nested_auth_dependency_and_router_level_dependencies():
    app = _bare()

    async def wrapper(u=Depends(require_admin)):
        return u

    @app.get("/api/nested")
    async def nested(_u=Depends(wrapper)):
        return {}

    @app.post("/api/auth/login", dependencies=[Depends(require_origin)])
    async def login():
        return {}

    assert audit(app) == []


def test_checker_reports_an_unknown_mount():
    from starlette.staticfiles import StaticFiles

    app = _bare()
    app.mount("/files", StaticFiles(directory="."), name="files")
    assert audit(app) == ["mount /files is not allowlisted"]
