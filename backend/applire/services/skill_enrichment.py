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
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from applire.constants import SKILL_ESTIMATION_MAX_TOKENS
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
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


def _parse_partial_date_end(s: str) -> date:
    """The EXCLUSIVE end of a partial date — the day after the last day it covers.

    "2023" → 2024-01-01, "2023-12" → 2024-01-01, "2023-12-15" → 2023-12-16.
    An end month or end year is inclusive in a CV ("01/2016 – 12/2023" is
    eight years). Reading it as its first day (``_parse_partial_date``) loses a
    month per role boundary — adversarial finding 3, 2026-10-07: four
    back-to-back roles over 96 months read as 7.77 years and a TRUE "seit acht
    Jahren" was accused. Used only by the ``bound="upper"`` reading.
    """
    from datetime import timedelta

    parts = s.strip().split("-")
    year = int(parts[0])
    if len(parts) == 1 or not parts[1]:
        return date(year + 1, 1, 1)
    month = int(parts[1])
    if len(parts) == 2 or not parts[2]:
        return date(year + (month // 12), month % 12 + 1, 1)
    return date(year, month, int(parts[2])) + timedelta(days=1)


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


def _union_years(ranges: list[tuple[date, date]]) -> float:
    """Total non-overlapping span of ``ranges`` in (fractional) years (#747).

    The unrounded quantity behind every ``computed`` duration. Kept separate so
    a consumer that must not round UP — the letter's hedge view and the
    Oracle's duration check (ADR-052/ADR-078 amended 2026-10-07, ruling T-1:
    7.6 years is "über sieben" or "knapp acht", never "acht") — reads the same
    arithmetic the stored value was rounded from.
    """
    sorted_ranges = sorted(((s, e) for s, e in ranges if e > s), key=lambda r: r[0])
    if not sorted_ranges:
        return 0.0
    merged: list[tuple[date, date]] = []
    cur_start, cur_end = sorted_ranges[0]
    for start, end in sorted_ranges[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return sum((end - start).days for start, end in merged) / 365.25


def _calculate_years(ranges: list[tuple[date, date]]) -> int:
    """Return total non-overlapping experience in years (rounded integer).

    Overlapping ranges are merged before summing — a skill used at two
    concurrent jobs is not double-counted.

    Returns 0 for an empty list. Returns minimum 1 if any entry exists,
    to avoid reporting 0 years for a brief engagement.
    """
    if not ranges:
        return 0
    return max(1, round(_union_years(ranges)))


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


def _evidencing_ranges(
    profile: MasterProfileData, skill_name: str, today: date, bound: str = "stored"
) -> tuple[list[tuple[date, date]], list[str]]:
    """Dated ranges + org labels of every experience whose own text names the skill.
    (A thin view over :func:`_evidencing_entries`, which holds the join.)

    THE single implementation of phase 1's join (ADR-066) — shared by
    enrichment and :func:`evidenced_span_years`, so the Oracle's duration check
    and the letter's hedge view can never disagree with the stored
    ``computed`` duration about which roles count.

    ``bound`` picks how a partial date is READ (adversarial findings 3 + 5,
    2026-10-07 — one instrument, calibrated per consumer):

    * ``"stored"`` — the arithmetic the stored ``computed`` value has always
      used (start and end read as their first day; a missing end = today).
    * ``"upper"`` — the Oracle's permissive reading: an end month/year is
      inclusive (exclusive end = first day after it); a missing end = today.
      Used to ACCUSE, so every choice resolves toward the larger span.
    * ``"floor"`` — the letter view's reading: ``"stored"`` arithmetic, and a
      range whose end is missing while the role is not marked current is
      dropped — it has no checkable floor (ruling T-1b's rule for estimates,
      applied to an unknown end). Used to OFFER a number, so every choice
      resolves toward the smaller span.
    """
    ranges: list[tuple[date, date]] = []
    orgs: list[str] = []
    for entry, start, end in _evidencing_entries(profile, skill_name, today, bound):
        ranges.append((start, end))
        label = entry.org_label()
        if label and label not in orgs:
            orgs.append(label)
    return ranges, orgs


def _evidencing_entries(
    profile: MasterProfileData, skill_name: str, today: date, bound: str = "stored"
) -> list[tuple[Any, date, date]]:
    """``(experience, start, end)`` for every dated experience whose own text names
    the skill — THE join behind :func:`_evidencing_ranges` (see its docstring for
    ``bound``), exposed with the experience itself so a consumer that must say WHICH
    positions a duration is counted from (the letter's TOOL DURATIONS fact, R-3,
    Hazelnut collectors 2026-11-04) reads the same positions the stored value counts."""
    out: list[tuple[Any, date, date]] = []
    for entry in profile.all_experiences:
        if not _demonstrates(skill_name, experience_evidence_text(entry)):
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
            if bound == "floor" and getattr(entry, "is_current", None) is not True:
                continue
            end = today
        else:
            try:
                end = (
                    _parse_partial_date_end(entry.end_date)
                    if bound == "upper"
                    else _parse_partial_date(entry.end_date)
                )
            except (ValueError, AttributeError):
                if bound == "floor":
                    continue
                end = today
        out.append((entry, start, end))
    return out


def evidenced_span_years(
    profile: MasterProfileData, skill_name: str, *, bound: str
) -> tuple[float, list[str]] | None:
    """How long the dated experiences that NAME this skill span (#747).

    Returns ``(years, org_labels)`` — fractional, de-overlapped years and the
    roles they come from — or ``None`` when no dated experience's own text
    names the skill. ADR-062: a FACT (whole-token presence + date arithmetic),
    the same one phase 1 stores, rounded, as a ``computed`` duration.

    ``bound`` is REQUIRED (adversarial finding 3): ``"upper"`` for the
    Oracle's duration check (it accuses, so it must not under-read an
    inclusive end month), ``"floor"`` for the letter view's hedge (it offers
    a number, so it must not over-read an unknown end — finding 5). See
    :func:`_evidencing_ranges`.
    """
    if bound not in ("upper", "floor"):
        raise ValueError(f"bound must be 'upper' or 'floor', not {bound!r}")
    ranges, orgs = _evidencing_ranges(profile, skill_name, date.today(), bound)
    if not ranges:
        return None
    return _union_years(ranges), orgs


def evidenced_positions(
    profile: MasterProfileData, skill_name: str, *, bound: str = "floor"
) -> tuple[float, list[tuple[Any, date, date]]] | None:
    """R-3 (founder, 2026-11-04): ``(years, [(experience, start, end), ...])`` —
    the de-overlapped span of the dated positions that reference the skill (in
    their technologies OR their own text) AND those positions, from the ONE join
    the stored ``computed`` value and the Oracle's duration check read
    (:func:`_evidencing_entries`). ``None`` when no dated experience names it.
    ``bound`` as in :func:`evidenced_span_years`; the letter offers a number, so
    its default is ``"floor"``."""
    if bound not in ("upper", "floor"):
        raise ValueError(f"bound must be 'upper' or 'floor', not {bound!r}")
    today = date.today()
    # A dated end in the future (a contract end, a notice period) counts only up
    # to today, and a range that contributes no time names no position
    # (adversarial pass 2026-11-04: 2024-01 → 2031-01 read as "over 7 years").
    rows = [
        (entry, s, min(e, today))
        for entry, s, e in _evidencing_entries(profile, skill_name, today, bound)
        if s < min(e, today)
    ]
    if not rows:
        return None
    return _union_years([(s, e) for _, s, e in rows]), rows


def stored_span_years(
    profile: MasterProfileData, skill_name: str, today: date | None = None
) -> int | None:
    """The span phase 1 would STORE as this skill's ``computed`` duration (#764).

    Exactly the arithmetic of :func:`_match_and_enrich`'s computed branch —
    ``_calculate_years`` over ``_evidencing_ranges(..., bound="stored")`` — so
    a reader asking "what would recomputation give?" (the Health hub's
    laundered-span check, ADR-061 cl. 7 amended 2026-10-16) can never
    disagree with what recomputation then writes (ADR-066). ``None`` when no
    dated experience's own text names the skill: nothing to recompute from.
    """
    ranges, _orgs = _evidencing_ranges(profile, skill_name, today or date.today())
    if not ranges:
        return None
    return _calculate_years(ranges)


# ── SF-WRITE.113 (Sorbet WP-T, ADR-078 amended 2026-11-13): a STATED START ──
#
# Hazelnut build 8 delivery: the writer was offered "Hygiene- und
# Dokumentationsdisziplin — since 04/2017 … über 9 Jahre" — the union of the dated
# positions whose own text names the skill — while the person had said "… (Sauber-
# raumbereich bei <employer> seit 2021)". The own-figure channel (R4-1,
# ``VaultIndex.stated_skill_tenures``) reads "N Jahre", never a start. A start the
# person states beats the computed span exactly as their own figure does.
#
# A superset of the Oracle's ``figures._TENURE_SINCE_RE`` (which stays as it is for
# the #469 envelope check): it also reads a month, numeric or named, and a year at a
# sentence end ("seit 2021.").
_MONTH_NAMES = {
    "januar": 1, "jänner": 1, "jaenner": 1, "january": 1, "jan": 1,
    "februar": 2, "february": 2, "feb": 2,
    "märz": 3, "maerz": 3, "march": 3, "mär": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6,
    "juli": 7, "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "december": 12, "dez": 12, "dec": 12,
}
_STATED_START_RE = re.compile(
    r"\b(?:seit|since)\s+"
    r"(?:(?P<mm>0?[1-9]|1[0-2])\s*[/.]\s*"
    r"|(?P<mname>" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r")\.?\s+)?"
    r"(?P<yyyy>(?:19|20)\d{2})(?!\d)(?![.,]\d)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")


@dataclass(frozen=True)
class StatedStart:
    """A start the person states for a skill in their OWN text (ADR-078 am. 2026-11-13).

    ``tight``: the start lies inside the skill's subject window
    (``vault.skills_near_duration`` — R4-1's predicate, its barriers included), so
    the start is the skill's and is offered as stated. Not tight: the start and the
    skill only share a sentence — whose start it is cannot be told, so the computed
    span for the skill is withheld (no duration), never replaced."""

    year: int
    month: int | None
    raw: str
    path: str
    sentence: str
    tight: bool
    #: MD-8 (adversarial F4/F5): False for a start that cannot be the skill's
    #: start as written — negated ("nicht seit 2021", "seit 2020 nicht mehr"),
    #: a range ("seit 2019-2021"), before 1950, more than
    #: ``_START_MARGIN_YEARS`` before the person's first dated position, or in
    #: the future. Such a start is never offered, and it withholds the computed
    #: span for every skill it is linked to (no duration).
    usable: bool = True

    @property
    def label(self) -> str:
        """The start as a CV/letter states it: ``"2021"`` or ``"04/2021"``."""
        return f"{self.month:02d}/{self.year}" if self.month else str(self.year)


#: MD-8 F4 — the words around a "seit YYYY" that make it no start as written.
_START_NEGATED_BEFORE = re.compile(r"\b(?:nicht|not|nie|never|kein\w*)\s*$", re.IGNORECASE)
_START_NEGATED_AFTER = re.compile(
    r"^[^.;:!?\n]{0,40}?\b(?:nicht|not|no\s+longer|nie|never|kein\w*)\b", re.IGNORECASE,
)
_START_RANGE_AFTER = re.compile(
    r"^\s*(?:[-–—/]|bis|to|until)\s*(?:\d{1,2}\s*[/.]\s*)?(?:19|20)\d{2}", re.IGNORECASE,
)
#: F5 — the oldest plausible start: 1950, and no more than this many years before
#: the person's first dated position.
_START_FLOOR_YEAR = 1950
_START_MARGIN_YEARS = 10


def _start_matches(text: str) -> list[tuple[int, int | None, str, int, bool]]:
    """``(year, month | None, raw, offset, as_written)`` for every "seit/since
    [MM/|Monat ]YYYY" in ``text``; ``as_written`` is False for a negated or a
    ranged one (MD-8 F4). Facts (ADR-062 cl. 1): closed patterns only."""
    out: list[tuple[int, int | None, str, int, bool]] = []
    text = text or ""
    for m in _STATED_START_RE.finditer(text):
        month: int | None = None
        if m.group("mm"):
            month = int(m.group("mm"))
        elif m.group("mname"):
            month = _MONTH_NAMES.get(m.group("mname").lower())
        as_written = not (
            _START_NEGATED_BEFORE.search(text[max(0, m.start() - 12):m.start()])
            or _START_NEGATED_AFTER.match(text[m.end():])
            or _START_RANGE_AFTER.match(text[m.end():])
        )
        out.append((int(m.group("yyyy")), month, m.group(0), m.start(), as_written))
    return out


def extract_stated_starts(text: str) -> list[tuple[int, int | None, str, int]]:
    """``(year, month | None, raw, offset)`` for every "seit/since [MM/|Monat ]YYYY"
    in ``text``. A FACT (ADR-062 cl. 1): a closed pattern, no reading of meaning."""
    return [(y, mo, raw, off) for y, mo, raw, off, _ok in _start_matches(text)]


def _first_position_year(profile_json: dict[str, Any]) -> int | None:
    years = []
    for w in profile_json.get("work_experience") or []:
        m = re.match(r"\s*((?:19|20)\d{2})", str((w or {}).get("start_date") or ""))
        if m:
            years.append(int(m.group(1)))
    return min(years) if years else None


def _plausible(year: int, month: int | None, first_year: int | None, today: date) -> bool:
    """MD-8 F5 — a start the person could have meant as written."""
    if year < _START_FLOOR_YEAR:
        return False
    if first_year is not None and year < first_year - _START_MARGIN_YEARS:
        return False
    return (year, month or 1) <= (today.year, today.month)


def stated_skill_starts(
    profile_json: dict[str, Any] | None, names: list[str]
) -> dict[str, StatedStart]:
    """Per skill (casefolded name): the start the person's OWN record states for it.

    The corpus is R4-1's (``letter_v2._person_stated_tool_years``): the vault
    index's evidence units plus the person's denial statements — the person's own
    words; ledger evidence, gap reasons, questions and every block the system
    writes are no unit and never a source (ADR-061/048 amended, "a paraphrase is
    not a source"). Unconfirmed entries are excluded first (790:61).

    A tight start beats a sentence-only one; among equals the EARLIEST start wins
    (the larger span, R4-1's "the larger wins"). Fail-open: ``{}``."""
    if not isinstance(profile_json, dict) or not names:
        return {}
    try:
        from applire.services.ats_audit import _norm, surface_present_whole_token
        from applire.services.oracle.matchers.vault import build_vault_index, skills_near_duration
        from applire.services.profile.reconcile.stance import exclude_unconfirmed

        idx = build_vault_index(exclude_unconfirmed(profile_json))
    except Exception:  # noqa: BLE001 — a prompt input must never fail generation
        return {}
    texts: list[tuple[str, str]] = [
        (u.path, u.text) for u in idx.units if not u.path.startswith("personal_info")
    ] + [(u.path, u.text) for u in idx.denial_units]
    out: dict[str, StatedStart] = {}
    first_year = _first_position_year(profile_json)
    today = date.today()

    def _key(s: StatedStart) -> tuple[int, int, int, int]:
        # An unusable start linked to the skill outranks every other (MD-8 F4):
        # it withholds the span, and earliest-wins never picks a negated start.
        return (0 if not s.usable else 1, 0 if s.tight else 1, s.year, s.month or 1)

    for path, text in texts:
        starts = _start_matches(text)
        if not starts:
            continue
        sentences: list[tuple[int, str]] = []
        pos = 0
        for part in _SENTENCE_SPLIT_RE.split(text):
            at = text.find(part, pos)
            sentences.append((at, part))
            pos = at + len(part)
        for year, month, raw, offset, as_written in starts:
            usable = as_written and _plausible(year, month, first_year, today)
            sentence = next(
                (s for at, s in reversed(sentences) if 0 <= at <= offset), text
            )
            tight_names = {n for n, _d in skills_near_duration(text, raw, names)}
            sentence_norm = _norm(sentence)
            for name in names:
                if name in tight_names:
                    tight = True
                elif surface_present_whole_token(name, sentence_norm):
                    tight = False
                else:
                    continue
                cand = StatedStart(
                    year, month, raw.strip(), path, sentence.strip()[:240], tight, usable,
                )
                cur = out.get(name.casefold())
                if cur is None or _key(cand) < _key(cur):
                    out[name.casefold()] = cand
    return out


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

        matched_ranges, matched_orgs = _evidencing_ranges(profile, skill.name, today)

        # Never REMOVE provenance. The reconciler records evidence as entity ids
        # on the same field (``UpsertSkill.evidence`` → apply.py), so replacing
        # the list wholesale would delete the merge's own trail when enrichment
        # re-runs over a merged profile (#327). Both vocabularies coexist —
        # ``experience_refs`` is documented as "ids/labels".
        matched_orgs = list(skill.experience_refs) + [
            o for o in matched_orgs if o not in skill.experience_refs
        ]

        if matched_ranges:
            # ADR-061 amended 2026-09-08 (#684) — a TRANSCRIBED span outranks a
            # computed one. Since that amendment the reconciler may carry a span
            # the candidate actually STATED ("15+ years in GMP-regulated
            # pharmaceutical manufacturing IT") on `UpsertSkill.years_experience`,
            # and the applier stamps `source = "transcribed"` for it. This pass
            # runs AFTER `apply_ops` on the import seam (`reconcile_import`), so
            # without this branch its date arithmetic would silently replace the
            # candidate's own words with a number derived from role dates —
            # which is clause 6 ("elapsed time is not proficiency") read one
            # field to the left, and the inflation direction clause 5 exists to
            # forbid. `experience_refs` still updates: that is evidence, not the
            # span, and more evidence is never worse.
            #
            # ADR-061 cl. 7 amended 2026-10-07 (#747): a span that arrives with
            # NO source is the extractor's reading of the document (only the
            # extraction emits a bare ``years_experience``; every later writer
            # stamps a source) — the same thing the unmatched path below has
            # always labelled it. It outranks a computed span exactly like a
            # transcribed one, and is labelled so.
            if skill.source in (_TRANSCRIBED, None) and skill.years_experience is not None:
                enriched.append(skill.model_copy(update={
                    "experience_refs": matched_orgs,
                    "source": _TRANSCRIBED,
                }))
                continue
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
            with llm_log_stage("skill_estimation"):
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
