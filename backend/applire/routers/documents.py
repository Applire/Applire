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

"""My Documents router — GET /api/documents."""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.schemas.documents import DocumentListResponse
from applire.services.documents import list_documents

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
async def get_documents(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DocumentListResponse:
    """List all generated CVs for the current user, newest first."""
    user = current_user
    return await list_documents(
        user_id=user.id,
        db=db,
        page=page,
        page_size=page_size,
        status=status,
    )
