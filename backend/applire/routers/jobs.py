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

"""
GET /api/jobs/match — return the current profile's ranked job list with combined scores.

Query parameters:
  top_n (int, default 10): maximum results to return
  berufsbild_code (str, optional): KldB 2020 prefix filter (e.g. "43" matches all IT codes)
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from applire.internal_errors import internal_server_error
from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.services.matching import JobMatchResult, rank_jobs
from applire.services.profile import get_profile_for_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobMatchResultResponse(BaseModel):
    job_id: uuid.UUID
    role_title: str
    company_name: Optional[str] = None
    berufsbild_code: Optional[str] = None
    berufsbild_label: Optional[str] = None
    llm_match_score: Optional[float] = None
    embedding_similarity: Optional[float] = None
    combined_score: float
    gap_analysis_id: Optional[uuid.UUID] = None


@router.get("/match", response_model=list[JobMatchResultResponse], status_code=status.HTTP_200_OK)
async def match_jobs(
    top_n: int = Query(default=10, ge=1, le=100, description="Maximum number of results"),
    berufsbild_code: Optional[str] = Query(default=None, description="KldB 2020 prefix filter"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> list[JobMatchResultResponse]:
    """Return jobs ranked by combined score (embedding similarity + LLM match score).

    The score weights are controlled by MATCHING_SCORE_EMBEDDING_WEIGHT and
    MATCHING_SCORE_LLM_WEIGHT environment variables (default: 0.4 / 0.6).
    """
    # Resolve the caller's profile (ADR-092 cl. 2 — the one vault read path)
    profile = await get_profile_for_user(db, user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile found — import a CV first",
        )

    try:
        results: list[JobMatchResult] = await rank_jobs(
            profile_id=profile.id,
            db=db,
            top_n=top_n,
            berufsbild_code=berufsbild_code,
            user_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise internal_server_error(exc, where="jobs.match_jobs")

    return [
        JobMatchResultResponse(
            job_id=r.job_id,
            role_title=r.role_title,
            company_name=r.company_name,
            berufsbild_code=r.berufsbild_code,
            berufsbild_label=r.berufsbild_label,
            llm_match_score=r.llm_match_score,
            embedding_similarity=r.embedding_similarity,
            combined_score=r.combined_score,
            gap_analysis_id=r.gap_analysis_id,
        )
        for r in results
    ]
