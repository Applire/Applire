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

# #237 (run-4 residual): ``skill_tokens`` was built for SHORT SKILL NAMES — its
# stopword list is deliberately minimal (bare conjunctions/articles), because
# a skill name almost never carries a pronoun or an auxiliary verb. Full
# narrative sentences/clauses (the letter path's own claim shape, #237) do,
# constantly — "My background in...", "I have extensive experience...",
# "additionally, as Founder of..." — and every one of those function words
# counts as a "content token" the coverage floor has to explain, diluting a
# genuine paraphrase below ``GROUNDED_MIN_COVERAGE`` for no evidentiary
# reason. This set is PURELY function words (pronouns, demonstratives,
# common prepositions/conjunctions/auxiliary verbs) — never a domain or
# action word — and is subtracted ONLY inside prose-grounding paths
# (:func:`ground_text_claim`, :func:`ground_via_role_union`), never from
# ``skill_tokens`` itself (a shared instrument other modules rely on
# unchanged) and never from the skill-union fallback's own scaffold list
# (a different, enumeration-specific concern). Removing pure function words
# can only ever RAISE a genuine paraphrase's coverage — a fabricated clause's
# real (non-function) content words still won't match anything, so this
# cannot manufacture a false positive on its own.
#
# Deliberately EXCLUDES prepositions ("on", "at", "in", "within"...) — unlike
# pronouns/auxiliaries, a preposition occasionally IS the coincidental word
# that makes a real match ("My focus is increasingly on AI" vs a claim
# "...focus on AI..." — stripping "on" there costs a genuine hit, not just
# noise), so the dilution risk isn't worth it for a token class that rarely
# dominates a claim's token count anyway.
_NARRATIVE_STOPWORDS = frozenset(
    {
        # EN pronouns / demonstratives
        "i", "my", "me", "mine", "myself", "we", "us", "our", "ours",
        "you", "your", "yours", "it", "its", "this", "that", "these", "those",
        # EN connective adverbs (additive framing, never content-bearing)
        "as", "also", "additionally", "further", "furthermore", "moreover",
        "therefore", "thus", "hence", "so",
        # EN auxiliary/linking verbs
        "have", "has", "had", "is", "are", "was", "were", "be", "been",
        "being", "will", "would", "can", "could", "should", "shall", "may",
        "might", "do", "does", "did",
        # DE mirror (pronouns, demonstratives, common connectives)
        "ich", "mein", "meine", "meiner", "meinem", "meinen", "mir", "mich",
        "wir", "uns", "unser", "unsere", "sie", "ihr", "ihre", "es",
        "diese", "dieser", "dieses", "diesen", "diesem",
        "auch", "sowie", "zudem", "außerdem", "ausserdem", "weiterhin",
        "daher", "somit",
        "habe", "hat", "hatte", "bin", "ist", "war", "waren", "sein",
        "werde", "wird", "kann", "koennte", "könnte", "soll",
    }
)


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
    tokens = sorted(skill_tokens(text) - _NARRATIVE_STOPWORDS)
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


# ── role-union fallback for anchored narrative claims (#237 run-4 residual) ──
#
# A letter claim genuinely ANCHORED to one position (``source_experience_id``
# set — by the strict per-sentence anchor, #237, or its same-company
# current-role tie-break / paragraph-continuation inheritance) often narrates
# several distinct facts about that SAME role in one sentence ("...my hands-on
# experience in production AI, cross-functional collaboration, and commercial
# AI product development.") — content scattered across that role's OWN
# responsibilities/achievements/technologies, no single bullet of which
# clears ``GROUNDED_MIN_COVERAGE`` alone. Exactly the skill-union fallback's
# shape, generalized from "the union of role-agnostic skills[]" to "the union
# of evidence OWNED BY THIS ONE EXPERIENCE ID" — safe because the pool is
# scoped to a single, already-resolved owner: it can never blend evidence
# across roles the way a whole-vault union would (the #196 lesson the
# skill-union docstring itself calls out), and unlike the skill-union path it
# is deliberately NOT offered to unanchored claims (there is no "this role"
# to scope the union to without a real anchor).
#
# #237 round-3: the pool also includes ``same_employer_ids`` — every OTHER
# position at the SAME company as ``source_id`` (``VaultIndex.
# same_employer_ids``). A tenure-spanning sentence anchored to the CURRENT
# role (the extract.py current-role tie-break) narrating a fact that
# actually lives on a PAST role at the identical employer is an ordinary
# same-employer claim, not a cross-role blend — mirrors
# ``matchers.attribution.find_foreign_owner``'s identical widening, so the
# grounding fallback and the attribution guard never disagree about what
# counts as "this claim's own company".
def _role_union_units(
    index: VaultIndex, source_id: str, same_employer_ids: frozenset[str] = frozenset()
) -> list[EvidenceUnit]:
    allowed = same_employer_ids | {source_id}
    return [u for u in index.units if u.owner_ids & allowed]


def ground_via_role_union(
    text: str,
    index: VaultIndex,
    source_id: str,
    same_employer_ids: frozenset[str] = frozenset(),
) -> GroundingResult | None:
    """Union-of-one-EMPLOYER grounding for a narrative claim anchored to it.

    Mirrors :func:`ground_via_skill_union`'s mechanics (narrative stopwords
    stripped, coverage measured over the union rather than one best unit),
    scoped to the units :func:`_role_union_units` returns for ``source_id``
    and its same-employer siblings. Returns ``None`` (never a verdict) when
    the pool is empty or coverage doesn't clear the floor — callers fall
    through to their existing unverifiable/entailment/skill-union path.
    """
    tokens = sorted(skill_tokens(text) - _NARRATIVE_STOPWORDS)
    if not tokens:
        return None
    role_units = _role_union_units(index, source_id, same_employer_ids)
    if not role_units:
        return None
    matched_units: list[EvidenceUnit] = []
    hits = 0
    for t in tokens:
        for unit in role_units:
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
