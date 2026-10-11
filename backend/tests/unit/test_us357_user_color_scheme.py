# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US357 / #770 — each person picks an app colour scheme the admin offers.

ADR-023 amended 2026-10-11; rulings CS-1 (default + offer list) and CS-2 (a
deleted or un-offered choice falls back to the default, silently). The seam
tests drive the real routes of ``applire.main.app`` — the picker list, the
settings write, ``/effective`` and the public ``/active`` — so every reader of
"offered" goes through the one predicate (SF-THEME.5) and two people never see
each other's palette (SF-THEME.1/.4).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from applire import ownership
from applire.auth.deps import require_admin, require_user
from applire.db.session import get_db
from applire.main import app
from applire.models.audit import AuditEvent
from applire.models.color_scheme import ColorScheme
from applire.models.user import User
from applire.services.color_schemes import derive_scheme

ALICE = uuid.UUID("00000000-0000-0000-0000-00000000a11c")
BOB = uuid.UUID("00000000-0000-0000-0000-000000000b0b")
ADMIN = uuid.UUID("00000000-0000-0000-0000-0000000ad111")


def _scheme(name: str, primary: str, *, active=False, builtin=False, offered=False):
    return ColorScheme(
        id=uuid.uuid4(),
        name=name,
        is_active=active,
        is_builtin=builtin,
        offered_to_users=offered,
        seed_primary=primary,
        seed_accent="#2a8f9d",
        seed_secondary="#c9a84c",
        surface_lightness=0.97,
        derived=derive_scheme(primary, "#2a8f9d", "#c9a84c", 0.97),
    )


@pytest_asyncio.fixture
async def world(async_db):
    """A default (offered), an offered extra, an unoffered draft; three people."""
    default = _scheme("EU Blue", "#1b4f72", active=True, builtin=True, offered=True)
    forest = _scheme("Forest", "#2e7d32", offered=True)
    draft = _scheme("Draft", "#8e24aa")
    for uid, email in ((ALICE, "alice@example.org"), (BOB, "bob@example.org"),
                       (ADMIN, "admin@example.org")):
        async_db.add(User(id=uid, email=email, role="admin" if uid == ADMIN else "user"))
    async_db.add_all([default, forest, draft])
    await async_db.commit()
    return {"default": default, "forest": forest, "draft": draft}


def _as(user_id: uuid.UUID):
    async def _dep():
        ownership.set_owner(user_id)
        return User(id=user_id, email=f"{user_id.hex[-4:]}@example.org")

    return _dep


@pytest_asyncio.fixture
async def call(async_db):
    """``await call(user_id, method, path, json=None)`` against the real app."""

    async def _db():
        yield async_db

    async def _call(user_id, method, path, json=None):
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[require_user] = _as(user_id)
        app.dependency_overrides[require_admin] = _as(user_id)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                return await c.request(method, path, json=json)
        finally:
            for dep in (get_db, require_user, require_admin):
                app.dependency_overrides.pop(dep, None)

    return _call


# --- the picker list ---------------------------------------------------------


@pytest.mark.asyncio
async def test_picker_lists_offered_schemes_and_the_default_only(world, call):
    r = await call(ALICE, "GET", "/api/color-schemes")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {s["name"] for s in body["schemes"]}
    assert names == {"EU Blue", "Forest"}  # the unoffered draft is absent
    assert body["default_id"] == str(world["default"].id)
    assert body["selected_id"] is None
    assert [s["is_default"] for s in body["schemes"] if s["name"] == "EU Blue"] == [True]


@pytest.mark.asyncio
async def test_default_is_listed_even_when_its_offer_flag_is_off(world, call, async_db):
    world["default"].offered_to_users = False
    await async_db.commit()
    r = await call(ALICE, "GET", "/api/color-schemes")
    assert "EU Blue" in {s["name"] for s in r.json()["schemes"]}


# --- choosing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_choice_persists_and_paints_effective(world, call):
    forest = str(world["forest"].id)
    r = await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": forest})
    assert r.status_code == 200, r.text
    assert r.json()["ui_color_scheme_id"] == forest

    assert (await call(ALICE, "GET", "/api/settings")).json()["ui_color_scheme_id"] == forest
    assert (await call(ALICE, "GET", "/api/color-schemes")).json()["selected_id"] == forest
    eff = (await call(ALICE, "GET", "/api/color-schemes/effective")).json()
    assert eff["id"] == forest
    assert eff["derived"]["--color-primary"] == "#2e7d32"


@pytest.mark.asyncio
async def test_explicit_null_follows_the_default_and_omission_leaves_it(world, call):
    forest = str(world["forest"].id)
    await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": forest})
    # An unrelated write must not touch the choice.
    r = await call(ALICE, "PATCH", "/api/settings", {"review_mode": "guided"})
    assert r.json()["ui_color_scheme_id"] == forest
    r = await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": None})
    assert r.json()["ui_color_scheme_id"] is None
    eff = (await call(ALICE, "GET", "/api/color-schemes/effective")).json()
    assert eff["id"] == str(world["default"].id)


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["draft", "unknown"])
async def test_an_unoffered_or_unknown_scheme_is_refused(world, call, which):
    target = str(world["draft"].id) if which == "draft" else str(uuid.uuid4())
    r = await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": target})
    assert r.status_code == 422
    assert (await call(ALICE, "GET", "/api/settings")).json()["ui_color_scheme_id"] is None


# --- isolation (SF-THEME.1 / .4) -----------------------------------------------


@pytest.mark.asyncio
async def test_one_persons_choice_never_paints_another(world, call):
    await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": str(world["forest"].id)})
    bob = (await call(BOB, "GET", "/api/color-schemes/effective")).json()
    assert bob["id"] == str(world["default"].id)
    assert (await call(BOB, "GET", "/api/color-schemes")).json()["selected_id"] is None


@pytest.mark.asyncio
async def test_public_active_answers_the_default_whoever_asks(world, call):
    await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": str(world["forest"].id)})
    r = await call(ALICE, "GET", "/api/admin/color-schemes/active")
    assert r.status_code == 200
    assert r.json()["id"] == str(world["default"].id)


# --- fallback (CS-2) -----------------------------------------------------------


@pytest.mark.asyncio
async def test_unoffering_a_chosen_scheme_falls_back_silently(world, call):
    forest = str(world["forest"].id)
    await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": forest})
    r = await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{forest}/offer", {"offered": False})
    assert r.status_code == 200, r.text
    assert r.json()["offered_to_users"] is False
    assert (await call(ALICE, "GET", "/api/color-schemes")).json()["selected_id"] is None
    assert (await call(ALICE, "GET", "/api/settings")).json()["ui_color_scheme_id"] is None
    eff = (await call(ALICE, "GET", "/api/color-schemes/effective")).json()
    assert eff["id"] == str(world["default"].id)


@pytest.mark.asyncio
async def test_deleting_a_chosen_scheme_falls_back_and_is_not_blocked(world, call):
    forest = str(world["forest"].id)
    await call(ALICE, "PATCH", "/api/settings", {"ui_color_scheme_id": forest})
    r = await call(ADMIN, "DELETE", f"/api/admin/color-schemes/{forest}")
    assert r.status_code == 204
    eff = (await call(ALICE, "GET", "/api/color-schemes/effective")).json()
    assert eff["id"] == str(world["default"].id)
    assert (await call(ALICE, "GET", "/api/settings")).json()["ui_color_scheme_id"] is None


# --- admin rules + audit -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_default_cannot_be_unoffered(world, call):
    default = str(world["default"].id)
    r = await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{default}/offer", {"offered": False})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_offer_unknown_scheme_is_404(world, call):
    r = await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{uuid.uuid4()}/offer", {"offered": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_activating_offers_and_both_changes_are_audited(world, call, async_db):
    draft = str(world["draft"].id)
    r = await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{draft}/activate")
    assert r.status_code == 200
    assert r.json()["offered_to_users"] is True
    assert "Draft" in {s["name"] for s in (await call(ALICE, "GET", "/api/color-schemes")).json()["schemes"]}

    forest = str(world["forest"].id)
    await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{forest}/offer", {"offered": False})
    # A no-op toggle writes nothing.
    await call(ADMIN, "PATCH", f"/api/admin/color-schemes/{forest}/offer", {"offered": False})

    rows = (await async_db.execute(
        select(AuditEvent).where(AuditEvent.action.like("appearance.%"))
    )).scalars().all()
    by_action = {}
    for row in rows:
        by_action.setdefault(row.action, []).append(row)
    assert len(by_action["appearance.default_changed"]) == 1
    changed = by_action["appearance.default_changed"][0]
    assert changed.actor_user_id == ADMIN
    assert changed.detail["target_id"] == draft
    assert changed.detail["from_scheme_id"] == str(world["default"].id)
    assert len(by_action["appearance.offer_changed"]) == 1
    assert by_action["appearance.offer_changed"][0].detail == {
        "offered": False, "target_type": "color_scheme", "target_id": forest,
    }


@pytest.mark.asyncio
async def test_new_schemes_are_not_offered_unless_asked(world, call):
    body = {"name": "Sand", "seed_primary": "#c2b280", "seed_accent": "#2a8f9d",
            "seed_secondary": "#c9a84c"}
    r = await call(ADMIN, "POST", "/api/admin/color-schemes", body)
    assert r.status_code == 201 and r.json()["offered_to_users"] is False
    r = await call(ADMIN, "POST", "/api/admin/color-schemes", {**body, "name": "Sand 2",
                                                                "offered_to_users": True})
    assert r.json()["offered_to_users"] is True
    names = {s["name"] for s in (await call(ALICE, "GET", "/api/color-schemes")).json()["schemes"]}
    assert "Sand 2" in names and "Sand" not in names
