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

from fastapi import APIRouter
from pydantic import BaseModel

from applire._version import __version__
from applire.config import HAS_CLOUD, settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    edition: str
    version: str
    llm_provider: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        edition="cloud" if HAS_CLOUD else "community",
        version=__version__,
        llm_provider=settings.llm_provider,
    )
