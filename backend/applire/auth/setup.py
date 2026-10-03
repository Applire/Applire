# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The first-run claim (ADR-091 cl. 14–16; S-3, S-14, MD-1; SF-IAM.1, SF-IAM.9).

*Setup required* ⇔ no user holds a credential. While it holds, **every boot**
mints a fresh 120-bit code (base32, six groups of four), stores only its sha256
in ``instance_state.auth.setup_token_hash`` and prints it in a WARNING block —
so only someone who can read the server's log can claim the instance (and an
upgraded instance's existing vault, which the stub user owns).

The claim converts the stub row **in place** (same ``users.id``, so no data
moves — S-3) with one atomic statement::

    UPDATE users SET email=…, password_hash=…, role='admin', email_verified_at=now()
     WHERE id=:stub AND password_hash IS NULL AND oidc_subject IS NULL RETURNING id

Zero rows → 409 ``setup_done``: of two concurrent claims exactly one wins. The
web route (``routers/setup.py``) and the CLI (``python -m applire.admin
create-admin``) share this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.harness import STUB_EMAIL, STUB_USER_ID, any_credential
from applire.models.user import ROLE_ADMIN, User
from applire.services.instance_state import (
    KEY_AUTH_CLAIM_NOTICE,
    KEY_AUTH_INSTANCE_SECRET,
    KEY_AUTH_SETUP_TOKEN_HASH,
    delete_state,
    read_state,
    write_state,
)

logger = logging.getLogger("applire.auth.setup")

SETUP_CODE_BITS = 120
INSTANCE_SECRET_BYTES = 32


def generate_setup_code() -> str:
    """120 random bits as base32 in groups of four: ``XXXX-XXXX-XXXX-XXXX-XXXX-XXXX``."""
    raw = base64.b32encode(secrets.token_bytes(SETUP_CODE_BITS // 8)).decode("ascii")
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def normalise_setup_code(value: str) -> str:
    """Case and ``-``/whitespace are ignored (the code is read off a log by a human)."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def hash_setup_code(code: str) -> str:
    return hashlib.sha256(normalise_setup_code(code).encode("ascii", "ignore")).hexdigest()


def setup_code_matches(presented: str, stored_hash: object) -> bool:
    """``compare_digest`` of the presented code's hash against the stored one."""
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    return hmac.compare_digest(hash_setup_code(presented), stored_hash)


def setup_block(code: str) -> str:
    """The WARNING block (ADR-091 cl. 14, verbatim; COPY.md 'Backend log, setup block')."""
    return (
        "\n==================================================================\n"
        f"SETUP REQUIRED — open /setup on the address where you normally open Applire "
        f"(for example http://<your-server>/setup) and enter: {code} — or run: "
        "docker compose exec backend python -m applire.admin create-admin "
        "--email you@example.org. A new code is printed at every start until setup "
        "is done.\n"
        "=================================================================="
    )


async def setup_required(db: AsyncSession) -> bool:
    return not await any_credential(db, use_cache=False)


async def ensure_stub_user(db: AsyncSession) -> None:
    """Insert the stub row only when ``users`` is empty (cl. 15). The caller commits."""
    has_user = (await db.execute(select(User.id).limit(1))).first() is not None
    if has_user:
        return
    await db.execute(
        insert(User).values(
            id=STUB_USER_ID,
            email=STUB_EMAIL,
            created_at=datetime.now(timezone.utc),
            photo_consent=False,
        )
    )


async def ensure_instance_secret(db: AsyncSession) -> bool:
    """Generate ``auth.instance_secret`` on first start (D-8). True if it was created."""
    if await read_state(db, KEY_AUTH_INSTANCE_SECRET):
        return False
    secret = base64.urlsafe_b64encode(secrets.token_bytes(INSTANCE_SECRET_BYTES)).decode("ascii")
    await write_state(db, KEY_AUTH_INSTANCE_SECRET, secret)
    return True


async def prepare_boot(db: AsyncSession) -> str | None:
    """Per-boot setup state. Returns the fresh code while setup is required, else None.

    Unclaimed: a NEW code every boot (MD-1) — the previous one stops working.
    Claimed: removes any stale code hash and logs the claim notice once.
    The caller commits.
    """
    if await setup_required(db):
        code = generate_setup_code()
        await write_state(db, KEY_AUTH_SETUP_TOKEN_HASH, hash_setup_code(code))
        return code
    await delete_state(db, KEY_AUTH_SETUP_TOKEN_HASH)
    notice = await read_state(db, KEY_AUTH_CLAIM_NOTICE)
    if isinstance(notice, dict):
        logger.warning(
            "This Applire instance was claimed at %s by %s.",
            notice.get("at", "?"),
            notice.get("email", "?"),
        )
        await delete_state(db, KEY_AUTH_CLAIM_NOTICE)
    return None


async def claim_stub(db: AsyncSession, *, email: str, password_hash: str) -> bool:
    """The atomic claim. True if this call converted the stub; False if 0 rows matched.

    Also removes the setup-code hash and records the one-time claim notice.
    The caller audits and commits.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(User)
        .where(
            User.id == STUB_USER_ID,
            User.password_hash.is_(None),
            User.oidc_subject.is_(None),
        )
        .values(
            email=email,
            password_hash=password_hash,
            role=ROLE_ADMIN,
            email_verified_at=now,
        )
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    if result.first() is None:
        return False
    await delete_state(db, KEY_AUTH_SETUP_TOKEN_HASH)
    await write_state(db, KEY_AUTH_CLAIM_NOTICE, {"at": now.isoformat(), "email": email})
    return True
