# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Strawberry W0 stub (frozen interface F10) — body owned by package 1a.

Planned content: auth_sessions, personal_tokens, auth_links, reauth_grants — ADR-091 cl. 11, 17, 22, 23.

W0 commits every revision of the Strawberry chain as a no-op so each package
builds on a real, single head: 0070 <- 0071 <- ... <- 0079
(``tests/unit/test_alembic_single_head.py``). The owner replaces ``upgrade`` /
``downgrade`` and this docstring; the revision id and ``down_revision`` stay.
A revision still a no-op at merge time stays a harmless empty step.
"""

from typing import Sequence, Union

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
