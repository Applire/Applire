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
    # EVERY unit that independently clears GROUNDED_MIN_COVERAGE — the backing
    # set for the attribution matcher (#196). Deliberately NOT capped at the
    # top-3 entailment window: a same-role unit ranked 4th by tie-break must
    # still clear the claim (2026-07-19 adversarial review).
    qualifying_units: list[EvidenceUnit] = field(default_factory=list)


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
    qualifying = [
        u for neg_hits, _, u in scored if -neg_hits / n >= GROUNDED_MIN_COVERAGE
    ]
    return GroundingResult(
        best_hits / n, best_unit, overall_hits / n, n, top, qualifying
    )


# ── skill-union fallback for enumeration clauses (adversarial-pass residual,
# 2026-07-23) ─────────────────────────────────────────────────────────────
#
# A truthful multi-skill enumeration ("My experience includes designing and
# implementing RESTful APIs with Python, FastAPI") spans several independent
# vault skill units — no SINGLE unit clears ``GROUNDED_MIN_COVERAGE``, so
# ``ground_text_claim`` alone leaves it unverifiable even though every named
# skill is individually attested. This fallback computes coverage over the
# UNION of role-agnostic vault ``skills[]`` units instead of one best unit.
#
# Only skill units may aggregate this way — union-ing work-experience
# narrative units would let a cross-role blend slip past the attribution
# matcher (the #196 lesson), so callers MUST run the attribution red-flag
# check against the single-unit grounding's own evidence BEFORE ever trying
# this fallback (see services/oracle/audit.py) — a misattribution verdict is
# never eligible to be rescued by it.
#
# Raw connective verbs/nouns that frame a skill list without being checkable
# content themselves. Passed through ``skill_tokens`` too (below) so the guarded
# plural fold (e.g. "includes" -> "include") lines up with how claim text is
# tokenized — a raw/stemmed mismatch would silently leave a scaffold word
# uncovered and wrongly count against the enumeration's coverage.
_UNION_SCAFFOLD_WORDS_RAW = (
    # EN
    "my", "i", "have", "has", "had",
    "experience", "includes", "including", "included",
    "designing", "designed", "implementing", "implemented",
    "developing", "developed", "building", "built",
    "managing", "automating", "automated", "integrating", "maintaining",
    "supporting", "delivering", "leading", "establishing",
    "operating", "configuring", "administering", "coordinating",
    "overseeing", "leveraging",
    "worked", "working", "work",
    "use", "used", "using", "utilizing", "utilized",
    "such", "as", "various", "several", "multiple",
    # DE
    "erfahrung", "einschliesslich", "einschließlich",
    "entwickelt", "entwickelte", "entwickelten",
    "implementiert", "implementierte",
    "gearbeitet", "arbeite", "arbeitete",
    "mit", "und", "sowie", "verwendet", "genutzt", "nutzung",
)
_UNION_SCAFFOLD_STOPWORDS = frozenset(skill_tokens(" ".join(_UNION_SCAFFOLD_WORDS_RAW)))


def _skill_union_units(index: VaultIndex) -> list[EvidenceUnit]:
    """Role-agnostic vault skill units — the only pool the union may draw on."""
    return [u for u in index.units if u.path.startswith("skills[")]


def ground_via_skill_union(text: str, index: VaultIndex) -> GroundingResult | None:
    """Skill-union grounding for enumeration clauses, or ``None`` if it fails.

    Content tokens are stripped of the scaffold/verb stopwords above (union
    path only — ``ground_text_claim`` is untouched) and coverage is measured
    against the union of ALL ``skills[]`` units, not one best unit. Returns a
    :class:`GroundingResult` (``qualifying_units`` = every matched skill unit)
    when the remaining tokens clear ``GROUNDED_MIN_COVERAGE``, else ``None`` —
    callers fall through to their existing unverifiable/entailment path.
    """
    tokens = sorted(skill_tokens(text) - _UNION_SCAFFOLD_STOPWORDS)
    if not tokens:
        return None
    union_units = _skill_union_units(index)
    if not union_units:
        return None
    matched_units: list[EvidenceUnit] = []
    hits = 0
    for t in tokens:
        for unit in union_units:
            if surface_present(t, unit.text_norm):
                hits += 1
                if unit not in matched_units:
                    matched_units.append(unit)
                break
    n = len(tokens)
    coverage = hits / n
    if coverage < GROUNDED_MIN_COVERAGE or not matched_units:
        return None
    return GroundingResult(
        best_coverage=coverage,
        best_unit=matched_units[0],
        overall_coverage=coverage,
        content_tokens=n,
        top_units=matched_units,
        qualifying_units=matched_units,
    )


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
