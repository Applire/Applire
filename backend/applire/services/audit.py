# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one writer of ``audit_events`` (ADR-091 cl. 26, S-11, RD-11).

``record(db, *, actor_id, action, target_type, target_id, details)`` — the
signature every package codes against (1a: setup claim, password change; 1c:
token create/revoke; 1b: everything under ``/api/admin/users`` and the link flow).

What a row may hold is closed here, not at the call sites:

* ``action`` must be one of :data:`ACTIONS`;
* ``details`` keys must be a subset of that action's allowed key set — ids,
  roles, reasons, counts, scopes; **never** an email, password, token, IP or
  content (``tests/unit/test_audit_service.py`` walks every action);
* values must be scalars (``str``/``int``/``bool``/``None``/UUID); a string value
  containing ``@`` is refused (an email cannot slip in under an allowed key).

``target_type`` ``"user"`` fills ``target_user_id``; any other target (a token,
a probe token, a link) is kept as ``detail.target_type`` + ``detail.target_id``
— the ADR's column list is unchanged. The row is added to ``db`` and flushed
with the caller's transaction: an action and its audit row commit together, or
neither does.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.audit import AuditEvent

__all__ = ["ACTIONS", "AuditDetailRejected", "TargetType", "record"]

TargetType = Literal["user", "token", "probe_token", "link", "instance"]

#: action -> allowed ``details`` keys. Adding an action is a reviewed change here.
ACTIONS: dict[str, frozenset[str]] = {
    # 1a — first run, harness, own password
    "setup.claimed": frozenset({"via"}),  # via: web | cli
    "harness.boot": frozenset(),
    "password.changed": frozenset(),
    # 1d — OIDC
    "oidc.linked": frozenset({"issuer"}),
    "oidc.unlinked": frozenset(),
    # 1c — personal and probe tokens
    "token.created": frozenset({"token_id", "scope"}),
    "token.revoked": frozenset({"token_id", "scope"}),
    "tokens.revoked_all": frozenset({"count"}),
    # 1b — accounts
    "user.created": frozenset({"role", "mailed"}),  # created as pending + invite link
    "user.reinvited": frozenset({"link_id", "mailed"}),
    "invite.redeemed": frozenset({"link_id"}),
    "user.role_changed": frozenset({"from_role", "to_role"}),
    "user.disabled": frozenset(),
    "user.enabled": frozenset(),
    "user.deleted": frozenset({"by", "erased_rows"}),  # by: admin | self
    "reset_link.issued": frozenset({"link_id", "via", "mailed"}),  # via: admin | forgot
    "password.reset": frozenset({"via", "link_id"}),  # via: link | cli
    # Epic C — runtime instance settings (ADR-093 cl. 7/8) and #738. ``from_value``
    # / ``to_value`` are written for NON-secret keys only; ``write_only`` says which (a test forbids "secret" as a detail key name).
    "settings.changed": frozenset(
        {"key", "write_only", "from_source", "to_source", "from_value", "to_value"}
    ),
    "settings.reset": frozenset({"key", "write_only", "from_value", "to_source"}),
    "settings.env_observed": frozenset({"key", "from_value", "to_value", "to_source"}),
    "retention.skipped": frozenset({"source"}),
}

#: Detail keys whose string may contain ``@`` unless it is EMAIL-shaped — a
#: gateway model id can carry ``@<region>`` (ADR-093 cl. 7).
_AT_TOLERANT_KEYS = frozenset({"from_value", "to_value"})
_EMAIL_SHAPE = re.compile(r"^[^@\s/:]+@[^@\s/:]+\.[A-Za-z]{2,}$")

_SCALARS = (str, int, bool, float, type(None), uuid.UUID)


class AuditDetailRejected(ValueError):
    """An audit call carried an unknown action or a forbidden detail."""


def _clean(action: str, details: dict[str, Any]) -> dict[str, Any]:
    allowed = ACTIONS.get(action)
    if allowed is None:
        raise AuditDetailRejected(f"unknown audit action: {action!r}")
    extra = set(details) - allowed
    if extra:
        raise AuditDetailRejected(
            f"audit action {action!r} does not allow detail keys {sorted(extra)}"
        )
    out: dict[str, Any] = {}
    for key, value in details.items():
        if not isinstance(value, _SCALARS):
            raise AuditDetailRejected(f"audit detail {key!r} must be a scalar")
        if isinstance(value, str) and "@" in value:
            if key not in _AT_TOLERANT_KEYS or _EMAIL_SHAPE.match(value.strip()):
                raise AuditDetailRejected(f"audit detail {key!r} looks like an email")
        out[key] = str(value) if isinstance(value, uuid.UUID) else value
    return out


async def record(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: TargetType | None,
    target_id: uuid.UUID | None,
    details: dict[str, Any],
) -> None:
    """Append one audit row (flushed, committed with the caller's transaction)."""
    detail = _clean(action, details)
    target_user_id: uuid.UUID | None = None
    if target_type == "user":
        target_user_id = target_id
    elif target_type is not None:
        detail["target_type"] = target_type
        detail["target_id"] = str(target_id) if target_id is not None else None
    elif target_id is not None:
        raise AuditDetailRejected("target_id needs a target_type")
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            action=action,
            target_user_id=target_user_id,
            detail=detail,
        )
    )
    await db.flush()
