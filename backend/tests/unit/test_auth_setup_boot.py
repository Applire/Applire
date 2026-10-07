# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-boot setup state (ADR-091 cl. 10, 14–16; MD-1; SF-IAM.1, SF-IAM.9)."""

import logging
import uuid

import pytest
from sqlalchemy import func, select

from applire.auth.harness import STUB_USER_ID
from applire.auth.setup import (
    claim_stub,
    ensure_instance_secret,
    ensure_stub_user,
    generate_setup_code,
    hash_setup_code,
    normalise_setup_code,
    prepare_boot,
    setup_block,
    setup_code_matches,
)
from applire.models.instance_state import (
    KEY_AUTH_CLAIM_NOTICE,
    KEY_AUTH_INSTANCE_SECRET,
    KEY_AUTH_SETUP_TOKEN_HASH,
)
from applire.models.user import User
from applire.services.instance_state import read_state


def test_code_shape_entropy_and_normalisation():
    code = generate_setup_code()
    groups = code.split("-")
    assert len(groups) == 6 and all(len(g) == 4 for g in groups)  # 24 base32 chars = 120 bits
    assert normalise_setup_code(code.lower().replace("-", " ")) == code.replace("-", "")
    assert setup_code_matches(code.lower(), hash_setup_code(code))
    assert not setup_code_matches(generate_setup_code(), hash_setup_code(code))
    assert not setup_code_matches(code, None)


def test_the_block_names_a_path_and_the_cli_never_a_base_url():
    block = setup_block("ABCD-EFGH")
    assert "SETUP REQUIRED — open /setup on the address where you normally open Applire" in block
    assert "ABCD-EFGH" in block
    assert "python -m applire.admin create-admin --email you@example.org" in block
    assert "A new code is printed at every start until setup is done." in block
    assert "localhost:8001" not in block


@pytest.mark.asyncio
async def test_stub_is_inserted_only_into_an_empty_users_table(async_db):
    await ensure_stub_user(async_db)
    await ensure_stub_user(async_db)
    await async_db.commit()
    assert (await async_db.execute(select(func.count()).select_from(User))).scalar_one() == 1
    stub = await async_db.get(User, STUB_USER_ID)
    assert stub.role == "user"  # no role set before the claim (D-11)

    await async_db.delete(stub)
    async_db.add(User(id=uuid.uuid4(), email="someone@example.org"))
    await async_db.commit()
    await ensure_stub_user(async_db)
    assert await async_db.get(User, STUB_USER_ID) is None


@pytest.mark.asyncio
async def test_every_boot_rotates_the_code_until_claimed(async_db):
    await ensure_stub_user(async_db)
    first = await prepare_boot(async_db)
    stored_first = await read_state(async_db, KEY_AUTH_SETUP_TOKEN_HASH)
    second = await prepare_boot(async_db)
    stored_second = await read_state(async_db, KEY_AUTH_SETUP_TOKEN_HASH)
    assert first != second
    assert not setup_code_matches(first, stored_second)  # the old code is dead (MD-1)
    assert setup_code_matches(second, stored_second) and stored_first != stored_second


@pytest.mark.asyncio
async def test_after_the_claim_the_next_boot_logs_once_and_mints_no_code(async_db, caplog):
    await ensure_stub_user(async_db)
    await prepare_boot(async_db)
    assert await claim_stub(async_db, email="op@example.org", password_hash="scrypt$x")
    await async_db.commit()
    assert await read_state(async_db, KEY_AUTH_SETUP_TOKEN_HASH) is None
    with caplog.at_level(logging.WARNING, logger="applire.auth.setup"):
        assert await prepare_boot(async_db) is None
        assert await prepare_boot(async_db) is None
    claimed = [r for r in caplog.records if "was claimed at" in r.getMessage()]
    assert len(claimed) == 1 and "op@example.org" in claimed[0].getMessage()
    assert await read_state(async_db, KEY_AUTH_CLAIM_NOTICE) is None


@pytest.mark.asyncio
async def test_the_claim_is_conditional_on_no_credential(async_db):
    await ensure_stub_user(async_db)
    await async_db.commit()
    assert await claim_stub(async_db, email="a@example.org", password_hash="scrypt$a") is True
    assert await claim_stub(async_db, email="b@example.org", password_hash="scrypt$b") is False
    await async_db.commit()
    stub = await async_db.get(User, STUB_USER_ID)
    await async_db.refresh(stub)
    assert stub.email == "a@example.org" and stub.password_hash == "scrypt$a"


@pytest.mark.asyncio
async def test_an_oidc_bound_stub_cannot_be_claimed(async_db):
    await ensure_stub_user(async_db)
    stub = await async_db.get(User, STUB_USER_ID)
    stub.oidc_issuer, stub.oidc_subject = "https://idp", "sub"
    await async_db.commit()
    assert await claim_stub(async_db, email="x@example.org", password_hash="scrypt$x") is False


@pytest.mark.asyncio
async def test_the_instance_secret_is_generated_once(async_db):
    assert await ensure_instance_secret(async_db) is True
    secret = await read_state(async_db, KEY_AUTH_INSTANCE_SECRET)
    assert isinstance(secret, str) and len(secret) >= 43
    assert await ensure_instance_secret(async_db) is False
    assert await read_state(async_db, KEY_AUTH_INSTANCE_SECRET) == secret


@pytest.mark.asyncio
async def test_pre_0062_upgrade_with_data_is_not_a_fresh_install(async_db, seed_profile):
    """ADR-091 cl. 16: absent last_seen_version + a vault ⇒ treat as 0.0.0."""
    from applire.main import _database_holds_data
    from applire.schemas.profile import MasterProfileData

    await ensure_stub_user(async_db)
    await async_db.commit()
    assert await _database_holds_data(async_db) is False  # a bare stub is no vault
    await seed_profile(MasterProfileData())
    assert await _database_holds_data(async_db) is True


@pytest.mark.asyncio
async def test_the_upgrade_notice_reports_auth_provider_none_as_re_meant_for_a_pre_0062_vault(
    async_db, seed_profile, monkeypatch
):
    """S-5a: a skip-version upgrader (no key, a vault) gets the notice, which
    lists AUTH_PROVIDER under re_meant with the rewritten line 1, and whose log
    hint no longer suggests an anonymous curl."""
    from contextlib import asynccontextmanager

    import applire.main as main_mod
    from applire.schemas.profile import MasterProfileData

    await seed_profile(MasterProfileData())

    @asynccontextmanager
    async def session_factory():
        yield async_db

    captured = {}
    monkeypatch.setattr(main_mod, "AsyncSessionLocal", session_factory)
    # Pin the running release: CI installs requirements only, so the package
    # metadata is absent there and __version__ reads "unknown" (no notice by design).
    monkeypatch.setattr(main_mod, "__version__", "0.43.0")
    monkeypatch.setattr(
        "applire.settings_registry.current_environment", lambda: {"AUTH_PROVIDER": "none"}
    )
    monkeypatch.setattr("applire.routers.health.set_upgrade_notice", lambda n: captured.setdefault("n", n))
    await main_mod._publish_upgrade_notice()
    notice = captured["n"]
    assert notice is not None and notice["from"] == "0.0.0"
    entry = next(e for e in notice["re_meant"] if e["env_var"] == "AUTH_PROVIDER")
    assert entry["description"].startswith("Sign-in provider. `local` = built-in accounts")
    from applire.settings_registry import format_upgrade_notice_log

    log = format_upgrade_notice_log(notice)
    assert "Authorization: Bearer" in log
