# Copyright (C) 2024-2026 Tobias Rosenbaum
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

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base


#: ADR-091 cl. 7 — the two roles. ``admin`` manages people and the instance.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


class User(Base):
    """A person with an account (ADR-091; 1 User -> 1 Profile, ADR-002/ADR-022).

    Identity columns (migration ``0071``): a local ``password_hash``
    (``scrypt$…``, ADR-091 cl. 5) and/or an OIDC binding ``(oidc_issuer,
    oidc_subject)`` (cl. 6). *Has a credential* ⇔ either is set — the setup
    claim, the harness fence and ``/api/auth/state.setup_required`` all read that
    predicate. ``link_epoch`` is part of every signed document link's MAC and is
    bumped on agent-token revoke, disable and delete (cl. 18). Email uniqueness is
    case-insensitive: ``UNIQUE(lower(email))`` (cl. 9); compare in SQL with
    ``lower(email) = lower(:x)``, never Python ``casefold()``.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # GDPR Art. 9(2)(a) — explicit consent for special category data (photo)
    photo_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    photo_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- ADR-091 identity (migration 0071) ---------------------------------
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ROLE_USER, server_default=ROLE_USER
    )
    oidc_issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    link_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("photo_consent", False)
        kwargs.setdefault("role", ROLE_USER)
        kwargs.setdefault("link_epoch", 0)
        super().__init__(**kwargs)


# ADR-091 cl. 9 — case-insensitive email uniqueness (expression index, 0071).
Index("uq_users_email_lower", func.lower(User.email), unique=True)
