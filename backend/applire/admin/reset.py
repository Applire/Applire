# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m applire.admin reset-password --email …`` — recover an account from
the server (ADR-091 cl. 23, S-7; Operator Branch L "the only admin forgot the
password").

Whoever can run this has server access and could read the database anyway, so the
operator types the new password here (prompted twice, never echoed; or one line
on stdin with ``--password-stdin`` for scripts). The command signs the person out
everywhere, kills their open invite/reset links and writes an audit row
(``password.reset``, ``via=cli``, no actor). It does not re-enable a disabled
account or change a role.

Dispatched by ``applire/admin/__main__.py`` (package 1a) through
``SUBCOMMAND_MODULES``: :func:`register` adds the subparser and sets
``func=run`` — ``run(args) -> int`` is the process exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["register", "reset_password", "run"]


class ResetRefused(Exception):
    pass


async def reset_password(db: AsyncSession, email: str, password: str) -> str:
    """Set ``password`` for ``email``; return the account status line. Commits."""
    from applire.services import audit
    from applire.services.admin import identity_seams as seams
    from applire.services.admin import users as accounts
    from applire.services.admin.links import revoke_open_links

    user = await accounts.find_by_email(db, email)
    if user is None:
        raise ResetRefused("no account with this email address")
    try:
        seams.check_password_policy(password, user.email)
    except ValueError as exc:
        raise ResetRefused("use 12 to 256 characters, and not the email address") from exc
    user.password_hash = await seams.hash_password(password)
    await seams.revoke_user_sessions(db, user.id)
    await revoke_open_links(db, user.id)
    await audit.record(db, actor_id=None, action="password.reset", target_type="user",
                       target_id=user.id, details={"via": "cli"})
    await db.commit()
    if user.disabled_at is not None:
        return "password set — note: this account is DISABLED; an admin must enable it"
    return f"password set — role: {user.role}; signed out everywhere"


def _read_password(args: argparse.Namespace, prompt: Callable[[str], str] = getpass.getpass) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = prompt("New password: ")
    if prompt("Repeat it: ") != first:
        raise ResetRefused("the two entries differ")
    return first


async def _run_async(email: str, password: str) -> str:
    from applire.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await reset_password(db, email, password)


def run(args: argparse.Namespace) -> int:
    try:
        password = _read_password(args)
        line = asyncio.run(_run_async(args.email, password))
    except ResetRefused as exc:
        print(f"reset-password refused: {exc}", file=sys.stderr)
        return 1
    print(line)
    return 0


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "reset-password",
        help="set a new password for an account (e.g. the only admin)",
        description="Set a new password for an account. Signs the person out everywhere.",
    )
    parser.add_argument("--email", required=True, help="the account's email address")
    parser.add_argument("--password-stdin", action="store_true",
                        help="read the new password as one line from stdin instead of prompting")
    parser.set_defaults(func=run)
