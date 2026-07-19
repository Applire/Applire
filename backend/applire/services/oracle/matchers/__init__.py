# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — deterministic Oracle matchers (pure functions, no LLM).

Grounding reuses THE shared presence predicate (``surface_present``,
ADR-048/US212) — never a fourth near-dupe implementation (ADR-046 lesson).
"""
from applire.services.oracle.matchers.attribution import find_foreign_owner
from applire.services.oracle.matchers.figures import Figure, extract_figures
from applire.services.oracle.matchers.vault import EvidenceUnit, VaultIndex, build_vault_index
from applire.services.oracle.matchers.grounding import (
    GroundingResult,
    ground_skill_claim,
    ground_text_claim,
    match_figures,
)

__all__ = [
    "Figure",
    "extract_figures",
    "find_foreign_owner",
    "EvidenceUnit",
    "VaultIndex",
    "build_vault_index",
    "GroundingResult",
    "ground_skill_claim",
    "ground_text_claim",
    "match_figures",
]
