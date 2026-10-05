# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real-auth lane — races through nginx on the production topology (ADR-091).

Runs ONLY in the real-auth lane (``APPLIRE_E2E_AUTH=1``); on the harness lanes
(`tests/integration/` in the Integration & E2E job, pq.yml) the module skips at
collection — those stacks serve the NoAuth harness, where no claim, invite or
role exists to race. Run instructions: ``tests/test_auth_e2e.py``.

Three races, each fired with a thread barrier so the requests leave together:

* **Concurrent claim** — ``RACERS`` setups with the valid code and different
  emails on an unclaimed instance: exactly one wins, the rest are 409
  ``setup_done``, and exactly one account holds a credential.
* **Concurrent invite redeem** — ``RACERS`` redemptions of one invite link with
  different passwords: exactly one wins (204), the rest 409 ``link_used``, and
  only the winner's password signs in.
* **Last-admin demote / disable** — two admins remove each other's admin role
  (and, second round, disable each other) at the same moment: exactly one
  request succeeds, the other is 409 ``last_admin``, and one active admin is left.

The claim race needs an UNCLAIMED stack: it is decided in ``lane.claim()`` the
first time any real-auth test asks for the admin. In CI that is the first test of
the 5b step, on a stack nobody has claimed yet.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_LANE_PATH = Path(__file__).resolve().parent.parent / "e2e-auth" / "lane.py"
if "applire_e2e_auth_lane" in sys.modules:
    lane = sys.modules["applire_e2e_auth_lane"]
else:
    _spec = importlib.util.spec_from_file_location("applire_e2e_auth_lane", _LANE_PATH)
    lane = importlib.util.module_from_spec(_spec)
    sys.modules["applire_e2e_auth_lane"] = lane
    _spec.loader.exec_module(lane)

if not lane.REAL_AUTH_LANE:
    pytest.skip(
        "real-auth lane only (APPLIRE_E2E_AUTH=1 against docker-compose.ci-auth.yml)",
        allow_module_level=True,
    )

ROUNDS = 3  # each race is fired this many times (fresh subjects per round)


def _together(fns):
    """Run the callables at the same instant (barrier) and return their results in order."""
    barrier = threading.Barrier(len(fns))

    def go(fn):
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(len(fns)) as pool:
        return list(pool.map(go, fns))


# ---------------------------------------------------------------------------
# Concurrent claim
# ---------------------------------------------------------------------------


def test_concurrent_claim_has_exactly_one_winner():
    c = lane.claim()
    if not c.raced:
        msg = (
            "the claim race needs an unclaimed stack (`down -v` first); "
            f"this stack was already claimed — {c.note}"
        )
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(msg)
    assert c.statuses.count(204) == 1, (
        f"{lane.RACERS} racers REST POST /api/setup through nginx: statuses {c.statuses} codes {c.codes}"
    )
    losers = [code for st, code in zip(c.statuses, c.codes) if st != 204]
    assert losers and all(code == "setup_done" for code in losers), (
        f"{lane.RACERS} racers REST POST /api/setup: losers answered {losers} (want 409 setup_done)"
    )
    # Other tests may have added accounts since; look only at the racers' emails.
    racers = ", ".join(f"'{lane._racer_email(i).lower()}'" for i in range(lane.RACERS))
    holders = lane.psql(f"SELECT lower(email) FROM users WHERE lower(email) IN ({racers}) ORDER BY 1").split()
    assert holders == [c.winner_email.lower()], (
        f"database after the claim race: racer accounts {holders}, winner {c.winner_email}"
    )
    for i in range(lane.RACERS):
        email = lane._racer_email(i)
        if email == c.winner_email:
            continue
        r = lane.login("loser", email, lane.ADMIN_PASSWORD)
        assert r.status_code == 401, f"claim loser {email} REST login -> {r.status_code} (no account may exist)"


# ---------------------------------------------------------------------------
# Concurrent invite redeem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("round_", range(ROUNDS))
def test_concurrent_redeem_of_one_invite_has_exactly_one_winner(round_):
    admin = lane.require_admin()
    rid = lane.run_id()
    email = f"redeem-race-{rid}@ci-auth.example.org"
    uid, token = lane.invite(admin, email)
    passwords = [f"redeem racer {i} passphrase {rid}" for i in range(lane.RACERS)]
    responses = _together([lambda pw=pw: lane.redeem(token, pw) for pw in passwords])
    statuses = [r.status_code for r in responses]
    codes = [lane.error_code(r) for r in responses]
    assert statuses.count(204) == 1, (
        f"{lane.RACERS} racers REST POST /api/auth/links/redeem (one invite for {email}): statuses {statuses}"
    )
    losers = [code for st, code in zip(statuses, codes) if st != 204]
    assert all(code == "link_used" for code in losers), (
        f"{lane.RACERS} racers REST redeem: losers answered {losers} (want 409 link_used)"
    )
    winner = statuses.index(204)
    me = responses[winner].session.get(f"{lane.API_BASE}/api/auth/me", timeout=30)  # type: ignore[attr-defined]
    assert me.status_code == 200 and me.json()["id"] == uid, (
        f"redeem winner REST /api/auth/me -> {me.status_code} {me.text[:200]} (want the invited id {uid})"
    )
    for i, pw in enumerate(passwords):
        r = lane.login("redeemer", email, pw)
        want = 204 if i == winner else 401
        assert r.status_code == want, (
            f"{email} REST login with racer {i}'s password -> {r.status_code} (want {want}; winner was racer {winner})"
        )


# ---------------------------------------------------------------------------
# Last-admin demote / disable
# ---------------------------------------------------------------------------


def _active_admins() -> list[str]:
    return lane.psql(
        "SELECT id FROM users WHERE role = 'admin' AND disabled_at IS NULL "
        "AND password_hash IS NOT NULL ORDER BY 1"
    ).split()


def _rejoin_lane_admin(survivor: "lane.Person", lane_admin: "lane.Person") -> None:
    """Make the lane's admin an active admin with a live session again."""
    r = survivor.patch(f"/api/admin/users/{lane_admin.id}", json={"role": "admin", "disabled": False})
    assert r.status_code == 200, f"{survivor.label} REST restore the lane admin -> {r.status_code} {r.text[:200]}"
    fresh = lane.sign_in(lane_admin.label, lane_admin.email, lane_admin.password)
    lane_admin.s = fresh.s
    lane_admin.refresh_me()


def _retire(p: "lane.Person", by: "lane.Person") -> None:
    """Take the second admin out of the admin set again (demote → user)."""
    r = by.patch(f"/api/admin/users/{p.id}", json={"role": "user"})
    assert r.status_code == 200, f"{by.label} REST demote {p.label} after the race -> {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("kind", ["demote", "disable"])
@pytest.mark.parametrize("round_", range(ROUNDS))
def test_two_admins_removing_each_other_leave_exactly_one_admin(kind, round_):
    admin = lane.require_admin()
    assert len(_active_admins()) == 1, f"database: {len(_active_admins())} active admins before the {kind} race"
    zoe = lane.new_user(admin, "Zoe", lane.run_id(), role="admin")
    assert zoe.role == "admin", f"Zoe REST /api/auth/me role {zoe.role!r} (invited as admin)"
    assert len(_active_admins()) == 2, "database: Zoe did not become the second active admin"

    body = {"role": "user"} if kind == "demote" else {"disabled": True}
    r_admin, r_zoe = _together([
        lambda: admin.patch(f"/api/admin/users/{zoe.id}", json=body),
        lambda: zoe.patch(f"/api/admin/users/{admin.id}", json=body),
    ])
    statuses = sorted([r_admin.status_code, r_zoe.status_code])
    detail = (
        f"admin→Zoe REST PATCH {body} -> {r_admin.status_code} {lane.error_code(r_admin)}; "
        f"Zoe→admin REST PATCH {body} -> {r_zoe.status_code} {lane.error_code(r_zoe)}"
    )
    left = _active_admins()
    assert len(left) == 1, f"database after the concurrent {kind}: {len(left)} active admins ({detail})"
    assert statuses == [200, 409], f"concurrent {kind}: want one 200 and one 409 — {detail}"
    loser = r_admin if r_admin.status_code == 409 else r_zoe
    assert lane.error_code(loser) == "last_admin", f"concurrent {kind}: the refused side answered {detail}"

    # Restore: the lane admin is the one active admin again, Zoe is not an admin.
    if r_admin.status_code == 200:  # the lane admin won
        if kind == "demote":
            pass  # Zoe is a plain user already
    else:  # Zoe won — she restores the lane admin, then the lane admin retires her
        _rejoin_lane_admin(zoe, admin)
        _retire(zoe, admin)
    if kind == "disable" and r_admin.status_code == 200:
        # Zoe is disabled but still holds the admin role; take it away so the
        # next round starts with exactly one admin row.
        r = admin.patch(f"/api/admin/users/{zoe.id}", json={"role": "user"})
        assert r.status_code == 200, f"admin REST demote disabled Zoe -> {r.status_code} {r.text[:200]}"
    assert _active_admins() == [admin.id], f"database after restore: active admins {_active_admins()}"
