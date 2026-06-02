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

"""Re-color the EU Blue built-in scheme to the canonical EU palette

The 0021 seed labeled a murky slate-teal palette (#1b4f72 / #2a8f9d / #c9a84c)
"EU Blue", which neither matches the EU flag nor the Continental Excellence
design system (docs/DESIGN.md: primary #003399, gold #fecb00). Because this is
the active scheme, the client overrode the correct globals.css palette with the
wrong colors a few frames after load — the residual flash in GitHub issue #30.

This migration re-colors the EU Blue built-in in place (by id, so a renamed or
re-seeded row is still corrected) to the canonical EU palette. The UPDATE is
idempotent and fixes both fresh and existing databases. GNOME Blue is left
untouched — its name correctly describes its Adwaita palette.

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-02
"""
from typing import Sequence, Union
import json

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EU_BLUE_ID = "a0000000-0000-0000-0000-000000000001"

# Canonical Continental Excellence EU palette (seed_primary, seed_accent,
# seed_secondary) and the surface lightness that yields light, airy surfaces.
_EU_BLUE_SEEDS = ("#003399", "#3557bc", "#fecb00")
_EU_BLUE_SURFACE_LIGHTNESS = 0.90

# Precomputed derived values — frozen snapshot of
# derive_scheme(*_EU_BLUE_SEEDS, _EU_BLUE_SURFACE_LIGHTNESS) at ship time.
# A unit test asserts this stays consistent with the algorithm.
_EU_BLUE_DERIVED = {
    "--color-primary": "#003399",
    "--color-primary-container": "#dee3ed",
    "--color-teal": "#3557bc",
    "--color-teal-dim": "#000f3d",
    "--color-teal-container": "#e2e7f3",
    "--color-teal-container-light": "#f6f7f8",
    "--color-gold": "#fecb00",
    "--color-gold-dim": "#665200",
    "--color-gold-container": "#f7f2de",
    "--color-surface-dim": "#fafbfd",
    "--color-surface-bright": "#ffffff",
    "--color-surface-container": "#f2f5fa",
    "--color-surface-container-high": "#ebeff7",
    "--color-surface-container-highest": "#e6ebf5",
    "--color-neutral-light": "#f2f5fa",
}

# Original (pre-0030) murky palette — restored on downgrade.
_OLD_SEEDS = ("#1b4f72", "#2a8f9d", "#c9a84c")
_OLD_SURFACE_LIGHTNESS = 0.80
_OLD_DERIVED = {
    "--color-primary": "#1b4f72",
    "--color-primary-container": "#dee7ed",
    "--color-teal": "#2a8f9d",
    "--color-teal-dim": "#00363d",
    "--color-teal-container": "#e2f1f3",
    "--color-teal-container-light": "#f6f8f8",
    "--color-gold": "#c9a84c",
    "--color-gold-dim": "#664b00",
    "--color-gold-container": "#f7f0de",
    "--color-surface-dim": "#e4eaee",
    "--color-surface-bright": "#ffffff",
    "--color-surface-container": "#dde5ea",
    "--color-surface-container-high": "#d6dfe6",
    "--color-surface-container-highest": "#d1dce3",
    "--color-neutral-light": "#e8edf1",
}

_UPDATE = (
    "UPDATE system_color_schemes SET "
    "seed_primary = :sp, seed_accent = :sa, seed_secondary = :ss, "
    "surface_lightness = :sl, derived = CAST(:derived AS JSONB) "
    "WHERE id = CAST(:id AS UUID)"
)


def _apply(seeds, sl, derived) -> None:
    sp, sa_, ss = seeds
    op.execute(
        sa.text(_UPDATE).bindparams(
            id=_EU_BLUE_ID, sp=sp, sa=sa_, ss=ss, sl=sl, derived=json.dumps(derived)
        )
    )


def upgrade() -> None:
    _apply(_EU_BLUE_SEEDS, _EU_BLUE_SURFACE_LIGHTNESS, _EU_BLUE_DERIVED)


def downgrade() -> None:
    _apply(_OLD_SEEDS, _OLD_SURFACE_LIGHTNESS, _OLD_DERIVED)
