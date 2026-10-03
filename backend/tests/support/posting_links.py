# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test helper: a user's link to a shared posting (ADR-092 cl. 5c, RD-2; Strawberry 4a).

Since Strawberry a posting (``job_analyses``) is reachable for a user only
through their own ``applications`` row — the link analyze creates. Tests that
insert a ``JobAnalysis`` directly and then exercise a door taking its id
(``create_application``, ``create_flow``, the job/gap routes, the claims door)
must create that link, exactly as ``POST /api/job/analyze`` would.

``hidden=True`` (default) creates the link soft-deleted: the user can reach the
posting, but no tracking card exists yet — so a test's "create the application"
step still starts from "nothing in the pipeline" (``create_application``
reactivates the row) and list/count assertions are unchanged.
``hidden=False`` is the visible tracking card analyze leaves behind.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from applire.models.application import Application


async def link_posting(
    db,
    job,
    user_id: uuid.UUID,
    *,
    hidden: bool = True,
    commit: bool = False,
) -> Application:
    """Insert ``user_id``'s link to ``job`` (a ``JobAnalysis`` or its id); flushes."""
    job_id = getattr(job, "id", job)
    app = Application(
        user_id=user_id,
        job_analysis_id=job_id,
        role_title=getattr(job, "role_title", None),
        company_name=getattr(job, "company_name", None),
        deleted_at=datetime.now(timezone.utc) if hidden else None,
    )
    db.add(app)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return app
