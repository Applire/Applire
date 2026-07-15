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

"""Region length-norm registry (E042 / US236, ADR-051 §1).

ADR-051 §1: NO component may hard-code a page number for CV/letter length —
everything reads REGION_NORMS through resolve_target_pages(). There is no
user-facing region picker yet; DEFAULT_REGION is the only region in play
until a future epic adds one, but the registry is already keyed by region so
that addition doesn't require touching every call site again.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegionNorm:
    """Length norm for one hiring region (DACH = Germany/Austria/Switzerland)."""

    cv_standard_pages: int
    cv_max_pages: int
    letter_pages: int


REGION_NORMS: dict[str, RegionNorm] = {
    "DACH": RegionNorm(cv_standard_pages=2, cv_max_pages=3, letter_pages=1),
}

DEFAULT_REGION = "DACH"


def resolve_target_pages(
    override: int | None,
    setting: int | None,
    region: str = DEFAULT_REGION,
) -> int:
    """Resolve the target CV page count.

    Precedence: per-generation ``override`` > user ``setting`` >
    ``REGION_NORMS[region].cv_standard_pages``.
    """
    if override is not None:
        return override
    if setting is not None:
        return setting
    return REGION_NORMS[region].cv_standard_pages
