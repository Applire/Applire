# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test support for package 3c (Strawberry W2) — the CV side.

* ``link_job`` — the user's link to a shared posting (ADR-092 cl. 5c, RD-2: an
  ``applications`` row). Since W2 the CV doors reach a posting only through
  ``services.job.get_job_for_user``; a test that analyses a job by inserting a
  ``JobAnalysis`` directly and then generates a CV adds the link here, exactly
  as ``analyze`` does in production (``ensure_application_link``).

No isolation factories are registered here: every CV-side id (``cv_id``,
``doc_id@/api/cv``) already maps to 3a's ``OwnerWorld.cv``.
"""

from __future__ import annotations

import uuid

from applire.models.application import Application
from tests.support.owners import HARNESS_USER_ID


async def link_job(db, job_id: uuid.UUID, user_id: uuid.UUID = HARNESS_USER_ID, **fields) -> Application:
    """Insert (flush, no commit) the user's ``applications`` row for ``job_id``."""
    app = Application(user_id=user_id, job_analysis_id=job_id, **fields)
    db.add(app)
    await db.flush()
    return app
