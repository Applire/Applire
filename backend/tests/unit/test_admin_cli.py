# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m applire.admin`` (ADR-091 cl. 14, 23; US321) — the subcommand
registry seam and ``create-admin``."""

import sys
import types
import uuid

import pytest
from sqlalchemy import select

from applire.admin.__main__ import SUBCOMMAND_MODULES, build_parser
from applire.admin.create_admin import create_admin
from applire.auth.harness import STUB_USER_ID
from applire.auth.passwords import verify_password
from applire.auth.setup import ensure_stub_user, prepare_boot
from applire.models.instance_state import KEY_AUTH_SETUP_TOKEN_HASH
from applire.models.user import User
from applire.services.instance_state import read_state

PASSWORD = "correct horse battery staple"


def test_the_registry_names_both_modules_and_skips_a_missing_one():
    assert SUBCOMMAND_MODULES == ("applire.admin.create_admin", "applire.admin.reset")
    parser = build_parser(("applire.admin.create_admin", "applire.admin.not_there"))
    args = parser.parse_args(["create-admin", "--email", "a@example.org"])
    assert args.command == "create-admin" and args.email == "a@example.org"


def test_a_registered_module_is_dispatched(monkeypatch):
    mod = types.ModuleType("applire_fake_reset")

    def register(sub):
        p = sub.add_parser("reset-password")
        p.add_argument("--email")
        p.set_defaults(func="sentinel")

    mod.register = register
    monkeypatch.setitem(sys.modules, "applire_fake_reset", mod)
    args = build_parser(("applire_fake_reset",)).parse_args(["reset-password", "--email", "x"])
    assert args.func == "sentinel"


def test_a_broken_import_inside_a_module_is_not_swallowed(monkeypatch):
    mod_name = "applire_broken_sub"

    def finder(name, *a, **k):
        if name == mod_name:
            raise ImportError("dependency missing", name="some_dependency")
        return real(name, *a, **k)

    import importlib

    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", finder)
    with pytest.raises(ImportError):
        build_parser((mod_name,))


@pytest.mark.asyncio
async def test_create_admin_claims_an_unclaimed_instance_in_place(async_db):
    await ensure_stub_user(async_db)
    await prepare_boot(async_db)
    await async_db.commit()
    assert await create_admin(async_db, email="op@example.org", password=PASSWORD) == "claimed"
    await async_db.commit()
    stub = await async_db.get(User, STUB_USER_ID)
    await async_db.refresh(stub)
    assert stub.email == "op@example.org" and stub.role == "admin"
    assert await verify_password(PASSWORD, stub.password_hash)
    assert await read_state(async_db, KEY_AUTH_SETUP_TOKEN_HASH) is None


@pytest.mark.asyncio
async def test_create_admin_on_a_claimed_instance_adds_an_admin(async_db):
    await ensure_stub_user(async_db)
    await create_admin(async_db, email="op@example.org", password=PASSWORD)
    await async_db.commit()
    assert await create_admin(async_db, email="second@example.org", password=PASSWORD) == "created"
    await async_db.commit()
    rows = (await async_db.execute(select(User).where(User.role == "admin"))).scalars().all()
    assert {u.email for u in rows} == {"op@example.org", "second@example.org"}
    with pytest.raises(ValueError, match="reset-password"):
        await create_admin(async_db, email="SECOND@example.org", password=PASSWORD)


@pytest.mark.asyncio
async def test_create_admin_applies_the_password_policy(async_db):
    with pytest.raises(ValueError):
        await create_admin(async_db, email="op@example.org", password="short")
    with pytest.raises(ValueError):
        await create_admin(async_db, email="not-an-email", password=PASSWORD)
