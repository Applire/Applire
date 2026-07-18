# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — grounding + figure matching against the vault index.

Pure functions, no LLM. Presence testing goes through THE shared surface
predicate (``surface_present``, ADR-048/US212) and the shared skill-token
instrument (``skill_tokens`` / ``skills_near_dupe``, #172) — the Oracle adds
no new near-dupe implementation by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from applire.services.ats_audit import (
    skill_tokens,
    skills_near_dupe,
    surface_present,
)
from applire.services.oracle.matchers.figures import Figure
from applire.services.oracle.matchers.vault import EvidenceUnit, VaultIndex

# A text claim counts as deterministically grounded when this fraction of its
# content tokens is present in a single vault evidence unit.
GROUNDED_MIN_COVERAGE = 0.6


@dataclass
class FigureMatchResult:
    matched: list[tuple[Figure, list[EvidenceUnit]]] = field(default_factory=list)
    unmatched: list[Figure] = field(default_factory=list)


def match_figures(figures: list[Figure], index: VaultIndex) -> FigureMatchResult:
    """Match document figures against the vault by (kind, canonical value)."""
    result = FigureMatchResult()
    for fig in figures:
        units = index.figure_map.get((fig.kind, fig.value), [])
        if units:
            result.matched.append((fig, units))
        else:
            result.unmatched.append(fig)
    return result


@dataclass
class GroundingResult:
    # Fraction of the claim's content tokens found in the single best unit.
    best_coverage: float
    best_unit: EvidenceUnit | None
    # Fraction found anywhere in the vault (paraphrase ceiling).
    overall_coverage: float
    content_tokens: int
    # Best-scoring units in descending hit order (entailment context).
    top_units: list[EvidenceUnit] = field(default_factory=list)


def ground_text_claim(text: str, index: VaultIndex) -> GroundingResult:
    tokens = sorted(skill_tokens(text))
    if not tokens:
        return GroundingResult(0.0, None, 0.0, 0)

    overall_hits = sum(1 for t in tokens if surface_present(t, index.all_text_norm))
    scored: list[tuple[int, int, EvidenceUnit]] = []
    for order, unit in enumerate(index.units):
        hits = sum(1 for t in tokens if surface_present(t, unit.text_norm))
        if hits > 0:
            scored.append((-hits, order, unit))
    scored.sort()
    top = [u for _, _, u in scored[:3]]
    best_unit = top[0] if top else None
    best_hits = -scored[0][0] if scored else 0
    n = len(tokens)
    return GroundingResult(best_hits / n, best_unit, overall_hits / n, n, top)


def ground_skill_claim(name: str, index: VaultIndex) -> EvidenceUnit | None:
    """Evidence unit backing a skill claim, or None if the vault has nothing.

    A skill is backed when its surface form appears anywhere in the vault
    (skills, technologies, bullets) or a vault skill is a near-dupe of it —
    both via the shared instruments, mirroring the reconciler and ATS layers.
    """
    for unit in index.units:
        if surface_present(name, unit.text_norm):
            return unit
    for i, vault_skill in enumerate(index.skill_names):
        if skills_near_dupe(name, vault_skill):
            for unit in index.units:
                if unit.path == f"skills[{i}]":
                    return unit
    return None
