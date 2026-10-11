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

from sqlalchemy import Boolean, DateTime, Float, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

_JSON = JSONB().with_variant(JSON(), "sqlite")


class ColorScheme(Base):
    __tablename__ = "system_color_schemes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ADR-023 amended 2026-10-11 (US357): the admin's offer list. People pick
    # among offered schemes; the default (is_active) is always offered.
    offered_to_users: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    seed_primary: Mapped[str] = mapped_column(String(7), nullable=False)
    seed_accent: Mapped[str] = mapped_column(String(7), nullable=False)
    seed_secondary: Mapped[str] = mapped_column(String(7), nullable=False)
    surface_lightness: Mapped[float] = mapped_column(Float, nullable=False, default=0.97)
    derived: Mapped[dict] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
