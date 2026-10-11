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

# backend/applire/services/color_schemes.py
"""Color scheme derivation and DB service functions."""
import colorsys
import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.color_scheme import ColorScheme
from applire.models.user_settings import UserSettings
from applire.services import audit
from applire.services.profile.owner import resolve_owner


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class SchemeDeleteError(Exception):
    """Base class for delete_scheme errors."""


class SchemeNotFound(SchemeDeleteError):
    """Raised when the scheme does not exist."""


class SchemeIsBuiltin(SchemeDeleteError):
    """Raised when attempting to delete a built-in scheme."""


class SchemeIsActive(SchemeDeleteError):
    """Raised when attempting to delete the currently active scheme."""


# ---------------------------------------------------------------------------
# Color math helpers
# ---------------------------------------------------------------------------

def _hex_to_hsl(hex_color: str) -> tuple[float, float, float]:
    """Convert #rrggbb → (h, s, l) with all values in [0, 1]."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    # colorsys returns (h, l, s) — note the l/s swap
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h, s, l


def _compose_rgba(hex_color: str, opacity: float) -> str:
    """Blend hex_color into white at the given opacity (0-1).

    opacity=0 → #ffffff, opacity=0.2 → 20% primary + 80% white.
    """
    r = int(hex_color.lstrip("#")[0:2], 16)
    g = int(hex_color.lstrip("#")[2:4], 16)
    b = int(hex_color.lstrip("#")[4:6], 16)
    r_i = max(0, min(255, round(r * opacity + 255 * (1 - opacity))))
    g_i = max(0, min(255, round(g * opacity + 255 * (1 - opacity))))
    b_i = max(0, min(255, round(b * opacity + 255 * (1 - opacity))))
    return f"#{r_i:02x}{g_i:02x}{b_i:02x}"


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    """Convert (h, s, l) with all values in [0, 1] → #rrggbb."""
    # colorsys expects (h, l, s) — note the l/s swap vs our convention
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    r_i = max(0, min(255, round(r * 255)))
    g_i = max(0, min(255, round(g * 255)))
    b_i = max(0, min(255, round(b * 255)))
    return f"#{r_i:02x}{g_i:02x}{b_i:02x}"


def _derive_color(hex_color: str, lightness: float, saturation: float) -> str:
    """Keep hue from hex_color, override lightness and saturation."""
    h, _, _ = _hex_to_hsl(hex_color)
    return _hsl_to_hex(h, saturation, lightness)


# ---------------------------------------------------------------------------
# Public derivation function (used by router + tests)
# ---------------------------------------------------------------------------

def derive_scheme(
    seed_primary: str,
    seed_accent: str,
    seed_secondary: str,
    surface_lightness: float,
) -> dict[str, str]:
    """Derive all 15 CSS custom property values from 3 seeds + surface lightness.

    surface_lightness (0.0–1.0):
      1.0 → surfaces are pure white (no primary tint)
      0.0 → surfaces match the primary color
    Internally inverted to tint_opacity so that higher lightness = more white.
    """
    tint_opacity = 1.0 - surface_lightness
    return {
        "--color-primary": seed_primary.lower(),
        "--color-primary-container": _derive_color(seed_primary, 0.90, 0.30),
        "--color-teal": seed_accent.lower(),
        "--color-teal-dim": _derive_color(seed_accent, 0.12, 1.00),
        "--color-teal-container": _derive_color(seed_accent, 0.92, 0.40),
        "--color-teal-container-light": _derive_color(seed_accent, 0.97, 0.15),
        "--color-gold": seed_secondary.lower(),
        "--color-gold-dim": _derive_color(seed_secondary, 0.20, 1.00),
        "--color-gold-container": _derive_color(seed_secondary, 0.92, 0.60),
        "--color-surface-dim": _compose_rgba(seed_primary, max(0.0, tint_opacity - 0.08)),
        "--color-surface-bright": "#ffffff",
        "--color-surface-container": _compose_rgba(seed_primary, max(0.0, tint_opacity - 0.05)),
        "--color-surface-container-high": _compose_rgba(seed_primary, max(0.0, tint_opacity - 0.02)),
        "--color-surface-container-highest": _compose_rgba(seed_primary, tint_opacity),
        "--color-neutral-light": _compose_rgba(seed_primary, tint_opacity * 0.5),
    }


# ---------------------------------------------------------------------------
# DB service functions
# ---------------------------------------------------------------------------

async def get_active_scheme(db: AsyncSession) -> ColorScheme | None:
    result = await db.execute(
        select(ColorScheme).where(ColorScheme.is_active == True)  # noqa: E712
    )
    active = result.scalar_one_or_none()
    if active is not None:
        return active
    # No scheme is flagged active (e.g. a freshly-reset or drifted DB). Fall back
    # to the oldest scheme — the first entry the admin list renders — so the UI
    # always has a palette to apply instead of rendering unthemed. Returns None
    # only when no schemes exist at all.
    fallback = await db.execute(
        select(ColorScheme).order_by(ColorScheme.created_at).limit(1)
    )
    return fallback.scalar_one_or_none()


async def list_schemes(db: AsyncSession) -> list[ColorScheme]:
    result = await db.execute(
        select(ColorScheme).order_by(ColorScheme.created_at)
    )
    return list(result.scalars().all())


async def create_scheme(
    db: AsyncSession,
    name: str,
    seed_primary: str,
    seed_accent: str,
    seed_secondary: str,
    surface_lightness: float,
    offered_to_users: bool = False,
) -> ColorScheme:
    derived = derive_scheme(seed_primary, seed_accent, seed_secondary, surface_lightness)
    scheme = ColorScheme(
        id=uuid.uuid4(),
        name=name,
        is_active=False,
        is_builtin=False,
        seed_primary=seed_primary.lower(),
        seed_accent=seed_accent.lower(),
        seed_secondary=seed_secondary.lower(),
        surface_lightness=surface_lightness,
        offered_to_users=offered_to_users,
        derived=derived,
    )
    db.add(scheme)
    await db.commit()
    await db.refresh(scheme)
    return scheme


async def activate_scheme(
    db: AsyncSession, scheme_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> ColorScheme | None:
    """Make ``scheme_id`` the instance default (ADR-023 amended 2026-10-11).

    The default is always offered, so activating also offers it. The change and
    its audit row commit together.
    """
    scheme = await db.get(ColorScheme, scheme_id)
    if scheme is None:
        return None
    previous = await get_active_scheme(db)
    # Use two pure-SQL UPDATEs to avoid SQLAlchemy stale-object issues when
    # re-activating a scheme that is already marked active in the identity map.
    await db.execute(update(ColorScheme).values(is_active=False))
    await db.execute(
        update(ColorScheme)
        .where(ColorScheme.id == scheme_id)
        .values(is_active=True, offered_to_users=True)
    )
    if previous is None or previous.id != scheme_id:
        await audit.record(
            db,
            actor_id=actor_id,
            action="appearance.default_changed",
            target_type="color_scheme",
            target_id=scheme_id,
            details={"from_scheme_id": previous.id if previous else None},
        )
    await db.commit()
    await db.refresh(scheme)
    return scheme


async def set_offered(
    db: AsyncSession,
    scheme_id: uuid.UUID,
    offered: bool,
    *,
    actor_id: uuid.UUID | None = None,
) -> ColorScheme:
    """Put a scheme on, or take it off, the offer list (ruling CS-1).

    Raises :class:`SchemeNotFound`, or :class:`SchemeIsActive` when the default
    would be taken off — the default is always offered. People who had picked a
    scheme that leaves the list fall back to the default at read time (CS-2).
    """
    scheme = await db.get(ColorScheme, scheme_id)
    if scheme is None:
        raise SchemeNotFound(scheme_id)
    default = await get_active_scheme(db)
    if not offered and default is not None and default.id == scheme.id:
        raise SchemeIsActive(scheme_id)
    if bool(scheme.offered_to_users) != offered:
        scheme.offered_to_users = offered
        await audit.record(
            db,
            actor_id=actor_id,
            action="appearance.offer_changed",
            target_type="color_scheme",
            target_id=scheme_id,
            details={"offered": offered},
        )
    await db.commit()
    await db.refresh(scheme)
    return scheme


# --- the person's choice (US357) ---------------------------------------------
#
# One predicate and one resolver (SF-THEME.5): the picker list, the settings
# validator, the settings read and /effective all go through these, so "offered"
# and "effective" cannot mean two different things in two places.


def is_offered(scheme: ColorScheme, default: ColorScheme | None) -> bool:
    """A scheme people may pick: on the offer list, or the default itself."""
    return bool(scheme.offered_to_users) or (
        default is not None and scheme.id == default.id
    )


async def list_offered_schemes(db: AsyncSession) -> list[ColorScheme]:
    default = await get_active_scheme(db)
    return [s for s in await list_schemes(db) if is_offered(s, default)]


async def resolve_choice(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> tuple[ColorScheme | None, ColorScheme | None]:
    """``(instance default, the person's choice)`` — the choice is ``None`` when
    the person follows the default, or when the stored scheme was deleted or is
    no longer offered (ruling CS-2: the fallback is silent)."""
    default = await get_active_scheme(db)
    result = await db.execute(
        select(UserSettings.ui_color_scheme_id).where(
            UserSettings.user_id == resolve_owner(user_id)
        )
    )
    choice_id = result.scalar_one_or_none()
    if choice_id is None:
        return default, None
    choice = await db.get(ColorScheme, choice_id)
    if choice is None or not is_offered(choice, default):
        return default, None
    return default, choice


async def resolve_effective_scheme(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> ColorScheme | None:
    """The scheme that paints this person's UI: their choice, else the default."""
    default, choice = await resolve_choice(db, user_id)
    return choice or default


async def validate_choice(db: AsyncSession, scheme_id: uuid.UUID) -> None:
    """Raise ``ValueError`` unless ``scheme_id`` is a scheme people may pick."""
    scheme = await db.get(ColorScheme, scheme_id)
    if scheme is None or not is_offered(scheme, await get_active_scheme(db)):
        raise ValueError(f"Colour scheme {scheme_id} is not offered on this instance.")


async def delete_scheme(db: AsyncSession, scheme_id: uuid.UUID) -> None:
    scheme = await db.get(ColorScheme, scheme_id)
    if scheme is None:
        raise SchemeNotFound(scheme_id)
    if scheme.is_builtin:
        raise SchemeIsBuiltin(scheme_id)
    if scheme.is_active:
        raise SchemeIsActive(scheme_id)
    await db.delete(scheme)
    await db.commit()
