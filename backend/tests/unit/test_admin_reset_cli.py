# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m applire.admin reset-password`` (ADR-091 cl. 23; Operator Branch L)."""

from __future__ import annotations

import argparse
import asyncio
import io

import pytest
from sqlalchemy import select

from applire.admin import reset
from applire.models.audit import AuditEvent
from applire.models.auth import AuthSession
from applire.models.user import User
from applire.services.admin import identity_seams as seams
from applire.services.admin import links
from tests.support.owners_1b import add_user

PW = "recovered passphrase 2026"


def _parser():
    p = argparse.ArgumentParser(prog="applire.admin")
    reset.register(p.add_subparsers(dest="command"))
    return p


def test_register_follows_the_cli_contract():
    args = _parser().parse_args(["reset-password", "--email", "a@example.org", "--password-stdin"])
    assert args.func is reset.run and args.email == "a@example.org" and args.password_stdin


@pytest.mark.asyncio
async def test_reset_password_sets_hash_signs_out_kills_links_and_audits(async_db):
    user = await add_user(async_db, email="Admin@Example.org", role="admin")
    async_db.add(AuthSession(user_id=user.id, token_hash="e" * 64))
    await async_db.commit()
    _, raw = await links.issue_link(async_db, user, "reset")
    await async_db.commit()
    line = await reset.reset_password(async_db, "admin@example.org", PW)
    assert "role: admin" in line
    row = await async_db.get(User, user.id, populate_existing=True)
    assert await seams.verify_password(PW, row.password_hash)
    assert (await async_db.execute(select(AuthSession.revoked_at))).scalar_one() is not None
    assert (await links.inspect_link(async_db, raw)).state == "used"
    (ev,) = (await async_db.execute(select(AuditEvent))).scalars().all()
    assert ev.action == "password.reset" and ev.detail == {"via": "cli"} and ev.actor_user_id is None


@pytest.mark.asyncio
async def test_disabled_account_gets_a_warning_line(async_db):
    await add_user(async_db, email="d@example.org", state="disabled")
    assert "DISABLED" in await reset.reset_password(async_db, "d@example.org", PW)


@pytest.mark.asyncio
@pytest.mark.parametrize("email,pw", [("nobody@example.org", PW), ("p@example.org", "short"),
                                      ("p@example.org", "p@example.org")])
async def test_refusals(async_db, email, pw):
    await add_user(async_db, email="p@example.org")
    with pytest.raises(reset.ResetRefused):
        await reset.reset_password(async_db, email, pw)


def test_run_exit_codes(monkeypatch, capsys):
    async def ok(email, password):
        assert password == PW
        return "password set"

    monkeypatch.setattr(reset, "_run_async", ok)
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    args = _parser().parse_args(["reset-password", "--email", "a@example.org", "--password-stdin"])
    assert asyncio.run(reset.run(args)) == 0 and "password set" in capsys.readouterr().out

    async def refused(email, password):
        raise reset.ResetRefused("no account with this email address")

    monkeypatch.setattr(reset, "_run_async", refused)
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert asyncio.run(reset.run(args)) == 1 and "refused" in capsys.readouterr().err


def test_prompt_mismatch_is_refused():
    args = argparse.Namespace(password_stdin=False)
    answers = iter(["one passphrase here", "another passphrase"])
    with pytest.raises(reset.ResetRefused):
        reset._read_password(args, prompt=lambda _: next(answers))
