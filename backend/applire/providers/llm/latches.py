# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Process-wide rejection latches for the gateway providers (#688 review, #756).

When a model refuses a request parameter (a response schema, a reasoning
switch), the provider stops sending it. That memory must outlive the provider
object: ``get_provider()`` builds a fresh provider for every request, so a
latch kept on the instance lasted one request, and a model that refuses
``reasoning:{enabled:false}`` cost one wasted 400 on every request rather than
once in the life of the process.

A latch is keyed by provider class, gateway base URL and model, so switching
the model in the admin panel starts clean. ``ProcessLatch`` is the descriptor
the providers declare; ``reset_latches()`` is the test seam (both unit trees
reset it before every test).
"""

from __future__ import annotations

_LATCHES: set[tuple[str, ...]] = set()


def _scope(obj: object) -> tuple[str, str, str]:
    return (
        type(obj).__name__,
        str(getattr(obj, "_latch_scope", "")),
        str(getattr(obj, "_model", "")),
    )


def _user_paid(obj: object) -> bool:
    """ADR-096 cl. 12: a user-paid provider neither reads nor writes a latch.

    A user key's 401, 429 or 5xx — or its model refusing a parameter — is that
    person's, never the process's: a latch written by it would degrade every
    other user on the same gateway and model (``SF-ALLOW.8``)."""
    return getattr(obj, "_payer", None) == "user"


def is_latched(obj: object, kind: str) -> bool:
    if _user_paid(obj):
        return False
    return (*_scope(obj), kind) in _LATCHES


def latch(obj: object, kind: str) -> None:
    if _user_paid(obj):
        return
    _LATCHES.add((*_scope(obj), kind))


def unlatch(obj: object, kind: str) -> None:
    if _user_paid(obj):
        return
    _LATCHES.discard((*_scope(obj), kind))


def any_latched(obj: object, kind_prefix: str) -> bool:
    if _user_paid(obj):
        return False
    scope = _scope(obj)
    return any(k[:3] == scope and k[3].startswith(kind_prefix) for k in _LATCHES)


def unlatch_prefix(obj: object, kind_prefix: str) -> None:
    if _user_paid(obj):
        return
    scope = _scope(obj)
    for k in [k for k in _LATCHES if k[:3] == scope and k[3].startswith(kind_prefix)]:
        _LATCHES.discard(k)


def reset_latches() -> None:
    """Test seam: forget every rejection."""
    _LATCHES.clear()


class ProcessLatch:
    """A boolean attribute whose value lives in the process-wide registry."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return is_latched(obj, self.kind)

    def __set__(self, obj, value: bool) -> None:
        (latch if value else unlatch)(obj, self.kind)
