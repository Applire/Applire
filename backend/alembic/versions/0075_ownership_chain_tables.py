# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Strawberry W0 stub (frozen interface F10) — body owned by package 3a.

Planned content: user_settings UNIQUE(user_id), chain tables user_id (backfill before NOT NULL) + re-keyed uniques, llm_usage.user_id, end-of-migration NULL assertion — ADR-092 cl. 1, 3.

W0 commits every revision of the Strawberry chain as a no-op so each package
builds on a real, single head: 0070 <- 0071 <- ... <- 0079
(``tests/unit/test_alembic_single_head.py``). The owner replaces ``upgrade`` /
``downgrade`` and this docstring; the revision id and ``down_revision`` stay.
A revision still a no-op at merge time stays a harmless empty step.
"""

from typing import Sequence, Union

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
