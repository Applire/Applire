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
    # Feedforward body-word budget that reliably fits letter_pages in the letter
    # template (ADR-051 §6 amended, #177: enforcement, not just detection, for
    # letters — the CV's guarantee shape, extended to cover letters).
    letter_body_word_budget: int
    # #272 Task 6: the LOWER bound ADR-051 §1 never set — a thin letter body
    # (insufficient selected evidence) previously passed silently since only an
    # upper bound existed. Read by the deterministic reviewer wrapper
    # (services/cover_letter_positioning.word_floor_reviewer_prompt_fn); never
    # a hard-coded word number outside this registry (ADR-051 §1).
    #
    # Calibrated against the blocker it exists for, not against "comfortably
    # below the budget": charter run #5 shipped a 187-word body that BOTH blind
    # reviewers rejected as thin ("~200 words … suggests either low genuine
    # interest or a rushed application"). A floor the blocker case passes is not
    # a floor, so it sits ABOVE 187. With the 300-word budget this leaves a
    # 200–300 band — the normal length of a one-page DACH Anschreiben.
    #
    # The failure mode when a body is under the floor is an honest reviewer
    # issue naming insufficient SELECTED EVIDENCE — never padding, never
    # invented content (#271 supplies the evidence that makes the band
    # reachable honestly).
    letter_body_word_floor: int


REGION_NORMS: dict[str, RegionNorm] = {
    "DACH": RegionNorm(cv_standard_pages=2, cv_max_pages=3, letter_pages=1,
                        letter_body_word_budget=300, letter_body_word_floor=200),
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

    Raises ``ValueError`` on a target below 1 — REST callers block these via
    schema validation (``Field(ge=1)``), but the resolver is the chokepoint
    every channel funnels through, so no caller can persist an invalid target
    (E042 Task 1.1 review finding).
    """
    if override is not None:
        if override < 1:
            raise ValueError("target_pages must be >= 1")
        return override
    if setting is not None:
        if setting < 1:
            raise ValueError("target_cv_pages setting must be >= 1")
        return setting
    return REGION_NORMS[region].cv_standard_pages
