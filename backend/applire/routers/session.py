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

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.gap import GapAnalysisResponse
from applire.schemas.session import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionStateResponse,
)
from applire.services.gap import analyze_gaps_for_session
from applire.services.session import create_session, get_session_state, send_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


def _get_provider() -> LLMProvider:
    return get_provider()


@router.post("", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def start_session(
    body: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SessionCreateResponse:
    try:
        return await create_session(body, db, provider)
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except Exception as exc:
        logger.exception("create_session failed for job %s", body.job_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/{session_id}",
    response_model=SessionStateResponse,
    status_code=status.HTTP_200_OK,
)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SessionStateResponse:
    try:
        return await get_session_state(session_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("get_session failed for session %s", session_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{session_id}/analyze-gaps",
    response_model=GapAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
async def analyze_session_gaps(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> GapAnalysisResponse:
    try:
        return await analyze_gaps_for_session(session_id, db, provider)
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except Exception as exc:
        logger.exception("analyze_session_gaps failed for session %s", session_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{session_id}/message",
    response_model=SessionMessageResponse,
    status_code=status.HTTP_200_OK,
)
async def post_message(
    session_id: uuid.UUID,
    body: SessionMessageRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> SessionMessageResponse:
    if not body.message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message must not be empty",
        )
    try:
        return await send_message(session_id, body.message.strip(), db, provider)
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM returned invalid JSON",
        )
    except Exception as exc:
        logger.exception("post_message failed for session %s", session_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
