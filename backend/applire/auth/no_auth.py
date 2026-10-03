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

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.base import AuthProvider
from applire.models.user import User

# Stable stub identity for single-user Community Edition deployments.
_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_STUB_EMAIL = "local@applire.community"


class NoAuthProvider(AuthProvider):
    """No-enforcement auth provider for Community Edition (ADR 008).

    Returns a fixed single-user stub. No tokens, no sessions, no enforcement.
    Behaviour is identical to pre-auth MVP — existing routes are unaffected.
    """

    async def get_current_user(  # type: ignore[override]
        self, request: Request, db: AsyncSession | None = None
    ) -> User:
        return User(
            id=_STUB_USER_ID,
            email=_STUB_EMAIL,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            deleted_at=None,
            # ADR-091 cl. 3: the harness identity acts as admin.
            role="admin",
        )
