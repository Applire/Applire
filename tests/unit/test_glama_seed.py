# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US371 route (b) — ``glama/seed.py`` claims a throwaway instance and mints the
agent token the Glama introspection image starts the MCP server with.

The image has no bypass: the server still resolves the token against the
database (S-5). This pins what the seed leaves behind: a token in the ``apl_``
format that ``resolve_agent_token`` accepts for the claimed admin (the stub
user's id, so the vault the stub owns stays reachable), an instance that is no
longer in setup-required state, and a claim that is one-shot — a second seed on
the same database is refused with ``ValueError`` ("This instance is already set
up") by ``create_admin`` and leaves no second token behind.
"""
import importlib.util
import re
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SEED_PATH = Path(__file__).resolve().parents[2] / "glama" / "seed.py"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("glama_seed", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.audit  # noqa: F401
    import applire.models.auth  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.instance_settings  # noqa: F401
    import applire.models.instance_state  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_returns_a_token_the_agent_door_accepts(db):
    from applire.auth.harness import STUB_USER_ID
    from applire.auth.setup import setup_required
    from applire.auth.tokens import resolve_agent_token

    assert await setup_required(db) is True  # fresh database: unclaimed

    raw = await _load_seed_module().seed(db)

    assert re.fullmatch(r"apl_[a-z0-9]{8}_[A-Za-z0-9_-]{43}", raw)
    user = await resolve_agent_token(db, raw)
    assert user.id == STUB_USER_ID  # the claim converts the stub in place
    assert user.role == "admin"
    assert user.email.endswith("@glama.invalid")
    assert await setup_required(db) is False


@pytest.mark.asyncio
async def test_second_seed_on_the_same_database_is_refused(db):
    """The claim is one-shot: ``create_admin`` raises ValueError on a claimed instance."""
    from applire.models.auth import PersonalToken

    seed_module = _load_seed_module()
    await seed_module.seed(db)

    with pytest.raises(ValueError, match="already set up"):
        await seed_module.seed(db)
    await db.rollback()

    count = (await db.execute(select(func.count()).select_from(PersonalToken))).scalar()
    assert count == 1  # no second token was minted


@pytest.mark.asyncio
async def test_two_fresh_databases_get_different_tokens():
    """Nothing fixed is baked into the image: every start draws new secrets."""
    from applire.db.session import Base
    import applire.models.audit  # noqa: F401
    import applire.models.auth  # noqa: F401
    import applire.models.instance_state  # noqa: F401
    import applire.models.user  # noqa: F401

    seed_module = _load_seed_module()
    tokens = []
    for _ in range(2):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            tokens.append(await seed_module.seed(session))
        await engine.dispose()
    assert tokens[0] != tokens[1]
