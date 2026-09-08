# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ops layer: retention_runs + llm_usage (ADR-086, US312 #145, US313).

Two additive tables, no change to any existing one.

`retention_runs` gives the GDPR worker's stdout JSON report its first consumer
(ADR-086 clause 5). `llm_usage` records what every provider call cost
(clause 7). Neither carries prompt or completion text; `llm_usage` has no text
column at all, which is the structural half of the PII boundary between it and
the debug log (SF-OPS.9).

**No foreign keys on `llm_usage.document_id` / `.application_id`, deliberately.**
The retention worker hard-DELETEs `generated_cvs` / `generated_cover_letters`
and tombstones `applications`; an FK here would abort those DELETEs on
PostgreSQL — the same failure class the 2026-07-13 real-LLM PQ found on
`flow_sessions`' document references. A cost record may never be the reason a
GDPR sweep fails, and a dangling id in a counters table harms nobody: the
aggregations group by it, they do not resolve it.

Chain: 0062 is US310's `instance_state` (the ops layer's `backup` probe reads
its `last_backup_at` key through a Table declared in its own module, never by
importing that model).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "retention_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report", _JSON, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retention_runs_run_at", "retention_runs", ["run_at"])

    op.create_table(
        "llm_usage",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("stage", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("method", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("document_kind", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_document_id", "llm_usage", ["document_id"])
    op.create_index("ix_llm_usage_application_id", "llm_usage", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_application_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_document_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_index("ix_retention_runs_run_at", table_name="retention_runs")
    op.drop_table("retention_runs")
