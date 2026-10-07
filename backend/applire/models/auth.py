# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identity tables (ADR-091; frozen interface F11; migration ``0072``).

``auth_sessions`` (cl. 11), ``personal_tokens`` (cl. 17), ``auth_links`` (cl. 22)
and ``reauth_grants`` (cl. 23). They carry ``user_id`` but are **not**
``__owned__``: they are read before a user context exists (cookie/token/link
resolution) and belong to ``applire.ownership.IDENTITY_TABLES`` (ADR-092 cl. 3).

No secret is ever stored: every ``token_hash`` is the hex sha256 of the raw value
the person holds (a 256-bit random secret, so a fast hash is the right one).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


TOKEN_SCOPES = ("agent", "api", "probe")
LINK_PURPOSES = ("invite", "reset")
REAUTH_ACTIONS = ("account.delete", "oidc.unlink")


class AuthSession(Base):
    """A server-side browser session (cookie ``applire_session``, ADR-091 cl. 11).

    Live ⇔ ``revoked_at IS NULL`` and ``last_seen_at`` within the idle window
    (14 d) and ``created_at`` within the absolute window (90 d) — RD-8.
    ``last_seen_at`` is refreshed at most hourly.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PersonalToken(Base):
    """A personal ``agent`` / ``api`` token or an admin's ``probe`` token (cl. 17).

    Format ``apl_<prefix>_<43 b64url>``; lookup by the unique 8-char ``prefix``,
    then ``hmac.compare_digest`` on ``token_hash``. Shown once at creation.
    """

    __tablename__ = "personal_tokens"
    __table_args__ = (
        CheckConstraint("scope IN ('agent', 'api', 'probe')", name="ck_personal_tokens_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthLink(Base):
    """A single-use invite (7 d) or reset (1 h) link (cl. 22, RD-8).

    Redemption is one statement: ``UPDATE auth_links SET used_at=now() WHERE
    token_hash=:h AND used_at IS NULL AND expires_at>now() RETURNING user_id``.
    """

    __tablename__ = "auth_links"
    __table_args__ = (
        CheckConstraint("purpose IN ('invite', 'reset')", name="ck_auth_links_purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReauthGrant(Base):
    """A one-shot fresh-OIDC re-authentication for a destructive action (cl. 23).

    Bound to session, action and target; consumed atomically by the destructive
    endpoint (``… WHERE verified_at IS NOT NULL AND used_at IS NULL AND
    expires_at > now() RETURNING id``). Filled by package 1d.
    """

    __tablename__ = "reauth_grants"
    __table_args__ = (
        CheckConstraint(
            "action IN ('account.delete', 'oidc.unlink')", name="ck_reauth_grants_action"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
