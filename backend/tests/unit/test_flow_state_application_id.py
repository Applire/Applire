# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FlowStateResponse.application_id population.

Asserts that get_flow_state / _build_state_response correctly surfaces
the application_id from FlowSession, whether or not an Application is linked.
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.flow import FlowSession
from applire.services.flow.orchestrator import get_flow_state
from tests.support.owners import HARNESS_USER_ID


async def _make_flow(
    db: AsyncSession,
    *,
    application_id: uuid.UUID | None = None,
) -> FlowSession:
    """Insert a minimal FlowSession with an optional application_id."""
    flow = FlowSession(
        user_id=HARNESS_USER_ID,  # ADR-092: get_flow_state is owner-keyed
        job_id=None,
        current_step="cv_generation",
        user_type="returning",
        available_actions={"next": "complete"},
        application_id=application_id,
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return flow


@pytest.mark.asyncio
async def test_application_id_is_none_when_no_application(async_db: AsyncSession):
    """application_id should be None when the flow has no linked Application."""
    flow = await _make_flow(async_db, application_id=None)
    state = await get_flow_state(flow.id, async_db)
    assert state.application_id is None


@pytest.mark.asyncio
async def test_application_id_populated_when_application_linked(
    async_db: AsyncSession,
    seed_application,
):
    """application_id should equal the linked Application's id."""
    application = await seed_application()

    # Create the FlowSession pointing back at this Application
    flow = await _make_flow(async_db, application_id=application.id)

    state = await get_flow_state(flow.id, async_db)
    assert state.application_id == application.id
