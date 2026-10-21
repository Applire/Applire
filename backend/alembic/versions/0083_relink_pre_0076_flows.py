# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Re-link the flows 0076 left detached (ADR-092 amended 2026-10-20; Frontend #789 L59).

0076 gave every owner an ``applications`` link (``user_status='tracking'``) for each
posting they had worked on before the upgrade, but left ``flow_session_id`` NULL,
reasoning that attaching "would need a workflow status this step cannot know".
That premise does not hold: ``workflow_status`` is a pure function of
``flow_sessions.current_step`` (``models/application.py::STEP_TO_WORKFLOW_STATUS``,
the map ``sync_workflow_status`` applies on every advance). The symptom it left: a
pre-upgrade posting that had a flow shows a tracking card with no step until the
person presses "start" (which re-links the existing flow — ``_start_workflow``).

This step does that re-link for every such pair, without the button:

* the application is live, not cancelled (start is refused there), has no
  ``flow_session_id`` and ``workflow_status = 'none'``;
* the owner has a live ``flow_sessions`` row for the same posting
  (``uq_flow_session_user_job`` makes it the only one), which no other
  application claims;
* → ``applications.flow_session_id`` = the flow, ``workflow_status`` = the mapped
  step, and ``flow_sessions.application_id`` = the application.

No timestamp moves (a migration must not re-arm a retention clock). The step map
is COPIED here — a migration never imports application code, which keeps
changing after the migration has shipped. Idempotent: a re-linked pair no longer
matches. The count is logged.

**Downgrade** is a no-op: a re-link is indistinguishable from a "start" made
afterwards, and the older schema is unchanged (the 0076 precedent).
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: Union[str, None] = "0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

#: Frozen copy of ``STEP_TO_WORKFLOW_STATUS`` at 0083 (unknown step -> analyzing,
#: the same fallback ``sync_workflow_status`` uses).
_STEP_TO_STATUS = {
    "jd_analysis": "analyzing",
    "cv_import": "analyzing",
    "gap_analysis": "analyzing",
    "interview": "interviewing",
    "cv_generation": "cv_generating",
    "complete": "completed",
}

_PAIRS = sa.text(
    "SELECT a.id, f.id, f.current_step FROM applications a "
    "JOIN flow_sessions f ON f.user_id = a.user_id AND f.job_id = a.job_analysis_id "
    "WHERE a.deleted_at IS NULL AND a.flow_session_id IS NULL "
    "  AND a.workflow_status = 'none' AND a.user_status <> 'cancelled' "
    "  AND f.deleted_at IS NULL "
    "  AND (f.application_id IS NULL OR f.application_id = a.id) "
    "  AND NOT EXISTS (SELECT 1 FROM applications o WHERE o.flow_session_id = f.id) "
    "ORDER BY a.id"
)


def upgrade() -> None:
    conn = op.get_bind()
    pairs = list(conn.execute(_PAIRS))
    if not pairs:
        log.info("0083: no detached pre-0076 flow to re-link")
        return
    apps = sa.table(
        "applications",
        sa.column("id", sa.Uuid()),
        sa.column("flow_session_id", sa.Uuid()),
        sa.column("workflow_status", sa.String()),
    )
    flows = sa.table(
        "flow_sessions",
        sa.column("id", sa.Uuid()),
        sa.column("application_id", sa.Uuid()),
    )
    for app_id, flow_id, step in pairs:
        app_uuid, flow_uuid = _uuid(app_id), _uuid(flow_id)
        conn.execute(
            apps.update()
            .where(apps.c.id == app_uuid)
            .values(flow_session_id=flow_uuid, workflow_status=_STEP_TO_STATUS.get(step, "analyzing"))
        )
        conn.execute(flows.update().where(flows.c.id == flow_uuid).values(application_id=app_uuid))
    log.info(
        "0083: re-linked %d pre-0076 flow(s) to their application link: %s",
        len(pairs),
        ", ".join(f"{a}→{f}" for a, f, _s in pairs),
    )


def _uuid(value):
    import uuid

    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def downgrade() -> None:
    pass
