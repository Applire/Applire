# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``create-admin`` — claim the instance from the server (ADR-091 cl. 14, S-14).

The command-line twin of the ``/setup`` page, for an operator who prefers the
shell (and for dev/PQ-backup/edge databases, which run without the harness).

* **Unclaimed instance:** converts the stub user in place — the same atomic
  claim as ``POST /api/setup`` (same ``users.id``, so an upgraded vault stays
  where it is). No setup code is needed: running this needs server access,
  which is exactly what the code proves.
* **Claimed instance:** refused (exit 1) — ruling 1a-1: after the claim,
  admins are made in one place, the audited admin UI; a locked-out admin
  recovers with ``python -m applire.admin reset-password`` (S-7).

The password is read from a prompt (twice) or, with ``--password-stdin``, from
the first line of standard input. It is never accepted as an argument (it would
land in the shell history and the process list).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from applire.services.audit import record as audit_record
from applire.auth.harness import STUB_USER_ID
from applire.auth.passwords import check_password_policy, hash_password
from applire.auth.setup import claim_stub, ensure_stub_user, setup_required


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
    """The work, on an open session. Returns ``"claimed"``; raises ValueError."""
    email = email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email:
        raise ValueError("That is not an email address.")
    check_password_policy(password, email)
    password_hash = await hash_password(password)
    await ensure_stub_user(db)
    if await setup_required(db):
        if not await claim_stub(db, email=email, password_hash=password_hash):
            raise ValueError("The instance was claimed meanwhile — run the command again.")
        await audit_record(
            db, actor_id=STUB_USER_ID, action="setup.claimed", target_type="user",
            target_id=STUB_USER_ID, details={"via": "cli"},
        )
        return "claimed"
    raise ValueError(
        "This instance is already set up. Invite people under Administration → People, "
        "or reset a password with `python -m applire.admin reset-password --email …`."
    )


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
    assert outcome == "claimed"
    print(f"Instance claimed: {args.email.strip()} is the administrator. Sign in at /login.")
    return 0
