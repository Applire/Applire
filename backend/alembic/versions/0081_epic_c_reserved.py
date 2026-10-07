# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Strawberry build 2 — reserved for WP-C1 (Epic C); a no-op.

Kept as a real revision so the chain stays single-headed for the next package
(``0082.down_revision = "0081"``, WORK-PACKAGES build 2). A no-op at merge time
stays a harmless empty step.
"""

from typing import Sequence, Union

revision: str = "0081"
down_revision: Union[str, None] = "0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
