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
from datetime import datetime, timedelta, timezone

from applire.models.application import Application

# Migration-0076 fixture (W2-1): used by the SQLite and the PostgreSQL proof.


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


def seed_0076(db, ids: dict) -> dict:
    from tests.support.nougat_fixture import STUB, T0

    """Migration-0076 hazards on top of ``tests.support.nougat_fixture.seed_nougat``: the 0076 hazards.

    seed_nougat already holds work on j_scraped (CVs, gap, interview) and j_pasted
    (letter) with no application. Added here: a posting the owner already has a
    card for (no second link), a posting with a soft-deleted card (counts as
    existing), a posting with a flow only, a posting whose only work is
    soft-deleted (no link), a soft-deleted posting with live work (no link), and a
    posting with no work at all (no link).
    """
    more = {k: uuid.uuid4() for k in (
        "j_carded", "app_carded", "cv_carded", "j_removed", "app_removed", "cl_removed",
        "j_flow", "flow_only", "j_deadwork", "cv_dead", "j_gone", "cv_on_gone", "j_untouched",
    )}
    for key, hash_ in (
        ("j_carded", "h3"), ("j_removed", "h4"), ("j_flow", "h5"),
        ("j_deadwork", "h6"), ("j_untouched", "h8"),
    ):
        db.insert("job_analyses", id=more[key], raw_text_hash=hash_, role_title=f"Role {hash_}",
                  company_name=f"Co {hash_}")
    db.insert("job_analyses", id=more["j_gone"], raw_text_hash="h7", deleted_at=T0)
    db.insert("applications", id=more["app_carded"], user_id=STUB, job_analysis_id=more["j_carded"])
    db.insert("generated_cvs", id=more["cv_carded"], job_analysis_id=more["j_carded"], profile_id=ids["p_new"])
    db.insert("applications", id=more["app_removed"], user_id=STUB, job_analysis_id=more["j_removed"],
              deleted_at=T0 + timedelta(days=2))
    db.insert("generated_cover_letters", id=more["cl_removed"], job_analysis_id=more["j_removed"],
              profile_id=ids["p_new"])
    db.insert("flow_sessions", id=more["flow_only"], user_id=STUB, job_id=more["j_flow"],
              current_step="jd_analysis", user_type="new", available_actions={})
    db.insert("generated_cvs", id=more["cv_dead"], job_analysis_id=more["j_deadwork"],
              profile_id=ids["p_new"], deleted_at=T0)
    db.insert("generated_cvs", id=more["cv_on_gone"], job_analysis_id=more["j_gone"], profile_id=ids["p_new"])
    return more
