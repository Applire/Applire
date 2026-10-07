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

"""``instance_settings`` — admin overrides of registry settings (ADR-093 cl. 1).

One row per overridden key; ``key`` is the registry's env-var name and must be
one of ``settings_registry.PANEL_KEYS`` (the service refuses anything else).
Exactly one of ``value`` (a non-secret JSON scalar) and ``secret_ciphertext``
(a Fernet token, ADR-093 cl. 4) is set.

Not ``instance_state``: a row here is validated against the registry, carries
its author, and may hold a secret — ADR-087 cl. 5 keeps all three out of that
key/value table. Not owned (ADR-092: no ``user_id``; ``updated_by_user_id`` is
an author, SET NULL on erasure), not on the MCP surface (ADR-054).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

_JSON = JSONB().with_variant(JSON(), "sqlite")


class InstanceSetting(Base):
    """One admin override of a panel-editable setting."""

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object | None] = mapped_column(_JSON, nullable=True)
    #: Fernet token; never logged, never returned (ADR-093 cl. 4).
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "users.id", name="fk_instance_settings_updated_by_users", ondelete="SET NULL"
        ),
        nullable=True,
    )

    def __repr__(self) -> str:  # never the ciphertext
        return f"<InstanceSetting key={self.key!r}>"
