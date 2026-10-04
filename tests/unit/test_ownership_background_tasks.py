# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Background work carries its owner (ADR-092 cl. 8 "Declared entry points", cl. 14;
US332; System-FMEA SF-OWN.3).

Starlette ``BackgroundTasks`` and ``asyncio.create_task`` inherit the request's
owner context (adversarial re-check g2), but a task that outlives or re-enters
without it would raise ``OwnerContextMissing`` once the guard is on — so every
``add_task`` passes the user explicitly (the task sets ``owner_context`` itself),
and every ``create_task`` is a declared ``unscoped`` entry point.

AST enumeration over ``backend/applire``: a NEW site that passes no user fails
now. ``PENDING`` lists the W1 sites W2 still threads (strict ratchet: a site
that gains its ``user_id`` must leave the list).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "backend" / "applire"

#: (file, target) of add_task sites that do not yet pass the user — W2 owner in the comment.
PENDING: set[tuple[str, str]] = {
}

#: add_task sites on ANONYMOUS routes: there is no user to pass — the task resolves
#: the person itself and scopes every owned read with ``owner_context`` (W1
#: integration: 1b's forgot-password mail; ``services/admin/users.ui_language_of``).
SELF_SCOPED: dict[tuple[str, str], str] = {
    ("routers/auth_links.py", "_send_forgot_mail"): "anonymous POST /api/auth/forgot",
}

#: create_task sites and the unscoped reason each one declares (cl. 7/8).
DECLARED_CREATE_TASK: dict[tuple[str, str], str] = {
    ("services/ops/aggregate.py", "_refresh_loop"): "ops-aggregate",  # 1c sets it
}


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _target_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ast.dump(node)[:40]


def _mentions_user(call: ast.Call) -> bool:
    nodes = list(call.args[1:]) + [k.value for k in call.keywords] + [
        ast.Name(id=k.arg or "") for k in call.keywords
    ]
    for n in nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and "user" in sub.id:
                return True
            if isinstance(sub, ast.Attribute) and "user" in sub.attr:
                return True
    return False


def _sites(method: str) -> list[tuple[str, str, bool, int]]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == method
                and node.args
            ):
                rel = str(path.relative_to(ROOT))
                out.append((rel, _target_name(node.args[0]), _mentions_user(node), node.lineno))
    return out


def test_the_enumeration_sees_the_known_sites():
    assert len(_sites("add_task")) >= 6
    assert len(_sites("create_task")) >= 1


def test_every_add_task_passes_the_user_or_is_pending():
    missing = sorted(
        f"{f}:{line} {target}" for f, target, ok, line in _sites("add_task")
        if not ok and (f, target) not in PENDING and (f, target) not in SELF_SCOPED
    )
    assert missing == [], "a background task must receive user_id (ADR-092 cl. 14)"


@pytest.mark.parametrize("site", sorted(PENDING), ids=lambda s: f"{s[0]}::{s[1]}")
def test_pending_sites_still_exist_and_still_lack_the_user(site):
    """The ratchet: when W2 threads a site, this fails until the entry is removed."""
    found = [(f, t, ok) for f, t, ok, _ in _sites("add_task") if (f, t) == site]
    assert found, f"{site} no longer exists — remove it from PENDING"
    assert not any(ok for *_, ok in found), f"{site} now passes the user — remove it from PENDING"


def test_every_create_task_is_a_declared_entry_point():
    undeclared = sorted(
        f"{f}:{line} {target}" for f, target, _ok, line in _sites("create_task")
        if (f, target) not in DECLARED_CREATE_TASK
    )
    assert undeclared == [], "declare the unscoped reason (ADR-092 cl. 8) or pass an owner"


@pytest.mark.parametrize("site", sorted(SELF_SCOPED), ids=lambda s: f"{s[0]}::{s[1]}")
def test_self_scoped_sites_still_exist(site):
    """A declared anonymous site that disappears must leave the list."""
    assert any((f, t) == site for f, t, _ok, _ in _sites("add_task")), f"{site} is gone"
