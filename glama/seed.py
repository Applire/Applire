# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seed a throwaway Applire database for the Glama introspection image.

Glama builds ``glama/Dockerfile``, starts it over stdio and asks the server for
its tool list. Applire's MCP server refuses to start without a valid agent
token checked against PostgreSQL (ADR-091 cl. 17, S-5), so the image carries its
own throwaway database and this script mints a token for it at every start.

It uses the same code paths a self-hoster uses: the ``create-admin`` claim
(``applire.admin.create_admin``) and the ``agent``-scope token creation of
Settings -> Tokens. There is no bypass mode: the server still validates the
token like on any instance.

``main()`` prints ONLY the raw token to stdout; the entrypoint captures it into
the environment of the MCP process. The admin's e-mail address and password are
random, never printed and discarded with the container.
"""

from __future__ import annotations

import asyncio
import secrets
import string

from applire.admin.create_admin import create_admin
from applire.auth.harness import STUB_USER_ID
from applire.auth.setup import ensure_instance_secret, ensure_stub_user
from applire.auth.tokens import audit_token_event, create_token

TOKEN_NAME = "glama-introspection"


def _random_password() -> str:
    """48 random characters (policy: 12-256, not equal to the e-mail)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(48))


async def seed(db) -> str:
    """Claim the instance and return a raw ``agent`` token. The caller opens the session.

    One-shot: ``create_admin`` refuses an already claimed instance with a
    ``ValueError`` ("This instance is already set up. ...").
    """
    await ensure_stub_user(db)
    await ensure_instance_secret(db)
    email = f"{secrets.token_hex(8)}@glama.invalid"
    await create_admin(db, email=email, password=_random_password())
    row, raw = await create_token(
        db, user_id=STUB_USER_ID, scope="agent", name=TOKEN_NAME
    )
    await audit_token_event(
        db,
        actor_id=STUB_USER_ID,
        action="token.created",
        owner_id=STUB_USER_ID,
        token_id=row.id,
        scope=row.scope,
    )
    await db.commit()
    return raw


async def _run() -> str:
    from applire.db.session import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            try:
                return await seed(db)
            except Exception:
                await db.rollback()
                raise
    finally:
        await engine.dispose()


def main() -> None:
    print(asyncio.run(_run()))


if __name__ == "__main__":
    main()
