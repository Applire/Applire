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

"""Skill enrichment service — deterministic + LLM hybrid (Sprint 28).

Public API:
    enrich_skills(profile, provider) -> MasterProfileData

Pipeline:
  1. Match each technical/soft/domain skill against all experience kinds
     (WorkEntry, ProjectEntry, VolunteerActivity) via profile.all_experiences.
     Uses org_label() for provenance so project names and volunteer orgs are
     recorded in experience_refs alongside company names (ADR-044 / US172).
     Calculate non-overlapping years from matched date ranges and record them
     in years_experience. Record provenance in experience_refs.
  2. For unmatched technical/soft skills, make a single batch LLM call with all
     experience entries to estimate years_experience. Source = "llm_estimated".
  3. language skills, and anything phase 1 and phase 2 leave without a computed
     or estimated duration, keep whatever the document said: "transcribed".

#327 — what phase 1 matches against. Until 2026-07-28 the join looked the skill
name up in the experience's ``technologies`` list, and nothing else. That list
is deliberately narrow: US176's TECHNOLOGIES-vs-PRACTICES rule instructs the
extractor to keep it to concrete tools and route practices/standards/
methodologies to ``skills`` with category "domain" — the one category this
service then excluded outright. For any career whose competencies are practices
rather than tools the matching corpus was empty by construction, so the
deterministic path could not fire at all: charter run #9 (a 14-year production
manager, real provider) produced ONE ``computed`` skill out of 24, with 9 of the
extractor's 11 skills landing in "domain" and ``technologies`` holding ``["SAP"]``
twice and ``[]`` once — while the role bullets named ISO-9001, Shopfloor-
Management, SMED, 5S and SAP verbatim, each inside a dated span. The evidence
was in the vault the whole time, one field over.

So the corpus is now the experience's own text — technologies AND
responsibilities AND achievements — the same three fields ``choice_grounding``
and ``gap_inference`` already read to decide what a role demonstrates. The
predicate is the shared one (``ats_audit.surface_present``, ADR-048); no second
matcher is introduced, so this consumer cannot drift from the ATS panel's notion
of presence.

ADR-062 classification: **fact**. Normalised surface presence of a name in a
text is settled by the data, and so is arithmetic over dated ranges. What the
service deliberately does NOT do is infer a skill from a related word — the
bullet "Ausbildung zum Lean-Multiplikator" does not credit "Lean Management";
that reading is a judgement, and this module has no business making it.

ADR-061 (clauses 5 & 6, 2026-07-27): this service NEVER writes ``proficiency``.
Both phases above only ever produce a *duration* (years_experience) and a
*provenance trail* (experience_refs) — both computed/inferred quantities, not
attributes read off the document. Whatever proficiency tier the candidate
declared (extracted verbatim from the CV/LinkedIn text upstream of this
module) passes through completely untouched: "SAP (Anwender)" stays at
whatever tier the extractor read, never promoted because fifteen years have
elapsed since the skill was first mentioned in some role's technologies list.
Elapsed time is a fact about duration, not a judgement about skill level, and
a judgement must trace to a statement (#304 / ADR-061). The historical
years→proficiency ladder (``_years_to_proficiency`` + the old
``_apply_floor``) is retired as a *writer* of proficiency; the former is kept
as a pure, still-tested mapping (no production caller), the latter — whose own
docstring stated the inverted rule this ADR overturns — has been removed.
"""
from __future__ import annotations

import logging
from datetime import date

from applire.constants import SKILL_ESTIMATION_MAX_TOKENS
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import MasterProfileData, Skill
from applire.prompts.skill_estimation import (
    SKILL_ESTIMATION_SYSTEM_PROMPT,
    build_skill_estimation_prompt,
)

logger = logging.getLogger(__name__)

# Categories phase 1 may compute a duration for from dated evidence. "domain"
# is here because US176 routes every practice/standard/methodology into it
# (#327) — excluding it made the deterministic path unreachable for whole
# occupations. "language" is not: a language level is a tier, not a tenure.
_MATCHABLE_CATEGORIES = frozenset({"technical", "soft", "domain"})

# Categories phase 2 may *estimate* a duration for. Deliberately NARROWER than
# _MATCHABLE_CATEGORIES: making "domain" matchable against real dated evidence
# does not make it guessable. A duration the candidate's own record cannot
# support stays absent rather than being invented (ADR-062).
_ESTIMABLE_CATEGORIES = frozenset({"technical", "soft"})

# Provenance vocabulary for ``Skill.source`` (ADR-061 clause 7 — transcribed vs
# computed). Every skill leaving this service carries exactly one of these.
_COMPUTED = "computed"          # years derived from the dated roles that evidence it
_LLM_ESTIMATED = "llm_estimated"  # years came from the phase-2 estimator
_TRANSCRIBED = "transcribed"    # read off the document; nothing was inferred


def _parse_partial_date(s: str) -> date:
    """Parse a partial date string to a date.

    Accepted formats: "YYYY", "YYYY-MM", "YYYY-MM-DD".
    Partial dates are expanded to the first of the month / year.
    """
    parts = s.strip().split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    day = int(parts[2]) if len(parts) > 2 and parts[2] else 1
    return date(year, month, day)


def _max_plausible_years(profile: MasterProfileData) -> int:
    """Deterministic plausibility ceiling for an LLM-estimated skill duration (#264).

    No skill can genuinely predate the candidate's professional history, so the
    ceiling is the calendar span from the EARLIEST experience start date (across
    jobs, projects, and volunteering) to today. A profile with no dated experience
    at all has no basis for any duration estimate — the ceiling is 0, which drops
    every positive estimate downstream rather than clamping to a false floor of 1.

    This is a deterministic control, not an LLM reviewer: the estimation call
    already runs once per profile enrichment, and the failure mode (a numeric
    duration exceeding real career length) is fully closed by a bound — adding a
    second LLM pass here would cost a call without checking anything a plausibility
    clamp can't already guarantee.
    """
    starts: list[date] = []
    for entry in profile.all_experiences:
        if not entry.start_date:
            continue
        try:
            starts.append(_parse_partial_date(entry.start_date))
        except (ValueError, AttributeError):
            continue
    if not starts:
        return 0
    span_days = (date.today() - min(starts)).days
    return max(0, round(span_days / 365.25))


def _calculate_years(ranges: list[tuple[date, date]]) -> int:
    """Return total non-overlapping experience in years (rounded integer).

    Overlapping ranges are merged before summing — a skill used at two
    concurrent jobs is not double-counted.

    Returns 0 for an empty list. Returns minimum 1 if any entry exists,
    to avoid reporting 0 years for a brief engagement.
    """
    if not ranges:
        return 0

    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    sorted_ranges = [(s, e) for s, e in sorted_ranges if e > s]
    if not sorted_ranges:
        return 1  # ranges existed but all were zero-duration: treat as minimum 1
    merged: list[tuple[date, date]] = []
    cur_start, cur_end = sorted_ranges[0]

    for start, end in sorted_ranges[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))

    total_days = sum((end - start).days for start, end in merged)
    years = total_days / 365.25
    return max(1, round(years))


def _years_to_proficiency(years: int) -> str:
    """Map years of experience to a proficiency level.

    Thresholds:
        < 1  → basic
        1–2  → intermediate
        3–5  → advanced
        ≥ 6  → expert

    RETIRED as a writer of ``Skill.proficiency`` (ADR-061 clause 6, #304/#317).
    This was the arithmetic ladder both enrichment phases funnelled a
    duration through to produce a proficiency tier — "years since a skill was
    first mentioned anywhere" is not the same quantity as "how good the
    candidate is at it", and a German CV's deliberately modest "Anwender"
    self-declaration was silently promoted to "expert" this way. Duration is
    a fact and keeps its own field (``years_experience``); skill level is a
    judgement that must trace to a statement. No production code calls this
    function any more — it is kept as a pure, still-tested mapping only.
    """
    if years < 1:
        return "basic"
    if years < 3:
        return "intermediate"
    if years < 6:
        return "advanced"
    return "expert"


def experience_evidence_text(entry) -> str:
    """The normalised text in which an experience demonstrates a skill (#327).

    Technologies AND responsibilities AND achievements — the same three fields
    ``choice_grounding`` and ``gap_inference`` already treat as "what this role
    shows". Returned pre-normalised (NFKC, dash→space, casefold, whitespace
    collapse) because ``surface_present`` expects a normalised haystack; the
    dash fold is what makes the German compounding in "ISO-9001-Audits" and
    "5S- und Kaizen-Workshops" match the skill names "ISO 9001" and "5S".
    """
    from applire.services.ats_audit import _norm

    parts: list[str] = []
    for field in ("technologies", "responsibilities", "achievements"):
        parts.extend(str(v) for v in (getattr(entry, field, None) or []))
    return _norm(" \n ".join(parts))


def _demonstrates(skill_name: str, evidence_norm: str) -> bool:
    """Does this experience's own text name the skill? Deterministic, no LLM.

    Two shared instruments in conjunction, both from ``ats_audit``:

    * ``surface_present`` — THE presence predicate (ADR-048). Requires the
      normalised name to appear contiguously, so "ISO 9001" is not satisfied by
      an "iso" and a "9001" three bullets apart.
    * ``skill_tokens`` containment — the #172 skill-name tokeniser, applied to
      name and evidence alike, so the hit must land on WHOLE tokens.

    The second is a pure narrowing of the first and exists because
    ``surface_present`` is a substring predicate: "R" is inside
    "Reklamationen" and "C" inside "Schicht". For the ATS panel such a hit only
    marks a keyword covered; here it would credit years and cite an employer,
    and that number reaches the rendered CV. Narrowing rather than forking
    keeps this consumer unable to claim a presence the shared predicate denies.
    A raw minimum name length would not do the same job — it would refuse the
    genuine two-character skills a shop-floor CV is full of ("5S").
    """
    from applire.services.ats_audit import skill_tokens, surface_present

    name_tokens = skill_tokens(skill_name)
    if not name_tokens:
        return False
    if not name_tokens <= skill_tokens(evidence_norm):
        return False
    return surface_present(skill_name, evidence_norm)


def _match_and_enrich(
    profile: MasterProfileData,
) -> tuple[list[Skill], list[Skill]]:
    """Phase 1: Deterministic skill-to-career-step matching.

    Returns:
        enriched:  Matched technical/soft/domain skills (years calculated) +
                   language skills (passed through, marked transcribed).
        unmatched: Matchable skills no experience's own text demonstrates.
                   Phase 2 decides which of those it may estimate.
    """
    today = date.today()
    enriched: list[Skill] = []
    unmatched: list[Skill] = []

    for skill in profile.skills:
        # language skills: a level is a tier, not a tenure — pass through, but
        # never with a null provenance (#327).
        if skill.category not in _MATCHABLE_CATEGORIES:
            enriched.append(skill.model_copy(update={"source": _TRANSCRIBED}))
            continue

        matched_ranges: list[tuple[date, date]] = []
        matched_orgs: list[str] = []

        for entry in profile.all_experiences:
            if not _demonstrates(skill.name, experience_evidence_text(entry)):
                continue

            # Parse start date — skip entry if absent or unparseable
            if not entry.start_date:
                continue
            try:
                start = _parse_partial_date(entry.start_date)
            except (ValueError, AttributeError):
                continue

            # Parse end date — null means current role/engagement → today
            if entry.end_date is None:
                end = today
            else:
                try:
                    end = _parse_partial_date(entry.end_date)
                except (ValueError, AttributeError):
                    end = today

            matched_ranges.append((start, end))
            label = entry.org_label()
            if label and label not in matched_orgs:
                matched_orgs.append(label)

        # Never REMOVE provenance. The reconciler records evidence as entity ids
        # on the same field (``UpsertSkill.evidence`` → apply.py), so replacing
        # the list wholesale would delete the merge's own trail when enrichment
        # re-runs over a merged profile (#327). Both vocabularies coexist —
        # ``experience_refs`` is documented as "ids/labels".
        matched_orgs = list(skill.experience_refs) + [
            o for o in matched_orgs if o not in skill.experience_refs
        ]

        if matched_ranges:
            years = _calculate_years(matched_ranges)
            # ADR-061 clauses 5 & 6 (#304/#317): proficiency is NOT touched here.
            # years_experience and experience_refs are code-computed from the
            # extractor's own `technologies` co-occurrence — that inference is
            # exactly what makes this provenance "computed", not "transcribed"
            # (clause 7); whatever proficiency tier the candidate declared on
            # the page passes through unchanged, and is never raised by how
            # much time has elapsed since a role first mentioned the skill.
            enriched.append(skill.model_copy(update={
                "years_experience": years,
                "experience_refs": matched_orgs,
                "source": _COMPUTED,
            }))
        else:
            unmatched.append(skill)

    return enriched, unmatched


def enrich_skills_deterministic(profile: MasterProfileData) -> MasterProfileData:
    """Phase 1 only — no provider, no LLM call, no network (#327).

    For callers that must (re-)establish provenance on a profile they just
    rebuilt but have no budget for an estimation round-trip: the merge seam
    (``reconcile_import``) runs this over every merged profile so a skill the
    ADR-046 op vocabulary minted still says where its duration came from.

    Idempotent: re-running assigns the same ``computed`` durations from the same
    dated evidence, and only ever ADDS to ``experience_refs``. Skills phase 1
    cannot evidence keep whatever the document said and are labelled
    ``transcribed`` — this function never invents a duration.
    """
    if not profile.skills:
        return profile
    enriched, unmatched = _match_and_enrich(profile)
    enriched.extend(
        s.model_copy(update={"source": s.source or _TRANSCRIBED}) for s in unmatched
    )
    return profile.model_copy(update={"skills": enriched})


async def enrich_skills(
    profile: MasterProfileData,
    provider: LLMProvider,
) -> MasterProfileData:
    """Enrich all skills with deterministic calculation and LLM estimation.

    Phase 1 (deterministic): Match each technical/soft skill against the
        technologies of every experience entry (jobs, projects, volunteering).
        Calculate non-overlapping years from date ranges into years_experience.
        Record experience_refs (org_label per entry).

    Phase 2 (LLM): For unmatched technical/soft skills, make a single batch
        LLM call with the full experience history to estimate years_experience.

    Neither phase writes ``proficiency`` (ADR-061 clauses 5 & 6, #304/#317) —
    it is left exactly as declared/extracted, never raised by a computed or
    estimated duration.

    language/domain skills are passed through unchanged in both phases.

    Returns a new MasterProfileData — does not mutate the input.
    """
    if not profile.skills:
        return profile

    enriched_skills, unmatched_skills = _match_and_enrich(profile)

    # Only skills the estimator is allowed to score AND that have no duration
    # yet are sent. A skill whose years the extractor read off the page ("14
    # Jahre Erfahrung") keeps that figure — a model guess must never overwrite a
    # stated fact (ADR-062) — and re-running enrichment over an already-enriched
    # profile therefore costs no extra call.
    estimable: list[Skill] = []
    for skill in unmatched_skills:
        if skill.category in _ESTIMABLE_CATEGORIES and skill.years_experience is None:
            estimable.append(skill)
        else:
            enriched_skills.append(
                skill.model_copy(update={"source": skill.source or _TRANSCRIBED})
            )
    unmatched_skills = estimable

    if unmatched_skills:
        all_exp_dicts = [e.model_dump(mode="json") for e in profile.all_experiences]
        skill_names = [s.name for s in unmatched_skills]

        try:
            estimates: dict = await provider.aparse_json(
                build_skill_estimation_prompt(all_exp_dicts, skill_names),
                system=SKILL_ESTIMATION_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=SKILL_ESTIMATION_MAX_TOKENS,
            )
        except Exception:
            logger.warning(
                "Skill estimation LLM call failed — unmatched skills stored without years."
            )
            estimates = {}

        # #264 — deterministic plausibility ceiling: no basis for a duration claim
        # that outlives the candidate's own career span (see _max_plausible_years).
        max_years = _max_plausible_years(profile)

        for skill in unmatched_skills:
            raw = estimates.get(skill.name)
            years: int | None = None
            if isinstance(raw, (int, float)) and raw > 0:
                # #327 — no floor. ``max(1, ...)`` turned an unsure 0.3 into a
                # vault fact reading "1 year", indistinguishable from a genuine
                # one-year skill; an estimate that rounds away is no basis at
                # all, and "unknown" must stay representable.
                years = int(round(raw)) or None
                if years is not None and years > max_years:
                    logger.warning(
                        "skill_enrichment: LLM estimated %d years for %r, exceeding the "
                        "candidate's %d-year career span; clamping (#264 plausibility guard)",
                        years, skill.name, max_years,
                    )
                    years = max_years or None  # 0 span → no basis at all, drop the estimate

            # ``experience_refs`` is deliberately NOT reset here (#327). An
            # estimate adds no provenance of its own, but the reconciler's
            # evidence ids live on this field and wiping them on a re-run would
            # delete a trail this phase never established.
            if years is not None:
                # ADR-061 clauses 5 & 6 (#304/#317): the estimate is a duration
                # only — proficiency is never written from it, matching the
                # deterministic phase above.
                enriched_skills.append(skill.model_copy(update={
                    "years_experience": years,
                    "source": _LLM_ESTIMATED,
                }))
            else:
                # #327 — the estimator declined to score this skill (no number,
                # or one that rounds away, or one the plausibility ceiling
                # dropped). Stamping it "llm_estimated" asserted an estimate
                # that was never made; nothing was inferred, so the provenance
                # is what the document said.
                enriched_skills.append(skill.model_copy(update={
                    "source": _TRANSCRIBED,
                }))

    return profile.model_copy(update={"skills": enriched_skills})
