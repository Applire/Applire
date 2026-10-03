# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``create-admin`` — claim the instance from the server (ADR-091 cl. 14, S-14).

The command-line twin of the ``/setup`` page, for an operator who prefers the
shell (and for dev/PQ-backup/edge databases, which run without the harness).

* **Unclaimed instance:** converts the stub user in place — the same atomic
  claim as ``POST /api/setup`` (same ``users.id``, so an upgraded vault stays
  where it is). No setup code is needed: running this needs server access,
  which is exactly what the code proves.
* **Claimed instance:** creates an additional, active admin account — the
  recovery path when no administrator can sign in any more. An existing email
  is refused (use ``reset-password`` for that person instead).

The password is read from a prompt (twice) or, with ``--password-stdin``, from
the first line of standard input. It is never accepted as an argument (it would
land in the shell history and the process list).
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from datetime import datetime, timezone

from applire.auth import _seams
from applire.auth.harness import STUB_USER_ID
from applire.auth.passwords import check_password_policy, hash_password
from applire.auth.setup import claim_stub, ensure_stub_user, setup_required
from applire.models.user import ROLE_ADMIN, User


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "create-admin",
        help="Claim this instance (or add an administrator) from the server.",
        description=__doc__.split("\n\n")[0],
    )
    p.add_argument("--email", required=True, help="Email address to sign in with.")
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of standard input.",
    )
    p.set_defaults(func=run)


def read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password (12–256 characters): ")
    second = getpass.getpass("Repeat the password: ")
    if first != second:
        raise ValueError("The two passwords do not match.")
    return first


async def create_admin(db, *, email: str, password: str) -> str:
    """The work, on an open session. Returns ``"claimed"`` or ``"created"``; raises ValueError."""
    from sqlalchemy import func, select

    email = email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email:
        raise ValueError("That is not an email address.")
    check_password_policy(password, email)
    password_hash = await hash_password(password)
    await ensure_stub_user(db)
    if await setup_required(db):
        if not await claim_stub(db, email=email, password_hash=password_hash):
            raise ValueError("The instance was claimed meanwhile — run the command again.")
        await _seams.audit(
            db, actor_id=STUB_USER_ID, action="setup.claimed", target_type="user",
            target_id=STUB_USER_ID, details={"via": "cli"},
        )
        return "claimed"
    taken = (
        await db.execute(select(User.id).where(func.lower(User.email) == func.lower(email)))
    ).first()
    if taken is not None:
        raise ValueError(
            "An account with this email already exists — use "
            "`python -m applire.admin reset-password --email …` for it."
        )
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(), email=email, password_hash=password_hash, role=ROLE_ADMIN,
        email_verified_at=now, created_at=now,
    )
    db.add(user)
    await db.flush()
    await _seams.audit(
        db, actor_id=None, action="user.created", target_type="user",
        target_id=user.id, details={"role": ROLE_ADMIN, "via": "cli"},
    )
    return "created"


async def run(args: argparse.Namespace) -> int:
    from applire.db.session import AsyncSessionLocal

    try:
        password = read_password(args)
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"create-admin: {exc}", file=sys.stderr)
        return 2
    async with AsyncSessionLocal() as db:
        try:
            outcome = await create_admin(db, email=args.email, password=password)
        except ValueError as exc:
            await db.rollback()
            print(f"create-admin: {exc}", file=sys.stderr)
            return 1
        await db.commit()
    if outcome == "claimed":
        print(f"Instance claimed: {args.email.strip()} is the administrator. Sign in at /login.")
    else:
        print(f"Administrator {args.email.strip()} created. Sign in at /login.")
    return 0
