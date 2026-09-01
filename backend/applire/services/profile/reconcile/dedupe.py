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

"""Deterministic near-duplicate detection for profile entry sections (#177).

ADR-046 (amended 2026-07-16) generalises the skills path's three-band policy
(#172) to every entity section:

* MATCH      — every evidenced identity field is exact or a strict near-dupe
               → safe to auto-merge (fill empties, never overwrite)
* AMBIGUOUS  — related only by bare single-token containment on some field
               → RequestConfirmation, never guess
* DISTINCT   — append as a new entry

Built on the shared tokeniser (ats_audit.skill_tokens), so formatting and
morphological variants land on one token set. Section-agnostic on purpose: a
future entry kind inherits the policy by declaring its identity fields at its
call site instead of re-implementing a predicate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from applire.services.ats_audit import (
    skill_tokens,
    skills_near_dupe,
    skills_single_token_containment,
)

_DISTINCT, _AMBIG, _SAME = 0, 1, 2


def _field_relation(a: str | None, b: str | None, *, containment_is_same: bool) -> int | None:
    """Relation of one identity-field pair; None = no evidence (a side is empty)."""
    if not a or not b:
        return None
    if skill_tokens(a) == skill_tokens(b) or skills_near_dupe(a, b):
        return _SAME
    if skills_single_token_containment(a, b):
        # Closed domains (languages; org names with/without their legal form)
        # may treat containment as identity; open domains ask the user.
        return _SAME if containment_is_same else _AMBIG
    return _DISTINCT


@dataclass
class DupeVerdict:
    match: Any | None = None
    ambiguous: list[Any] = field(default_factory=list)


def classify_dupe(
    incoming: dict[str, str | None],
    existing_entries: list[Any],
    getters: dict[str, Callable[[Any], str | None]],
    *,
    containment_is_same: bool = False,
) -> DupeVerdict:
    verdict = DupeVerdict()
    for entry in existing_entries:
        relations = [
            _field_relation(incoming.get(name), getter(entry),
                            containment_is_same=containment_is_same)
            for name, getter in getters.items()
        ]
        evidenced = [r for r in relations if r is not None]
        if not evidenced or any(r == _DISTINCT for r in evidenced):
            continue
        if all(r == _SAME for r in evidenced):
            verdict.match = entry
            return verdict
        verdict.ambiguous.append(entry)
    return verdict


def _month(iso_date: str) -> str:
    return iso_date[:7]


def classify_engagement_dupe(
    *,
    org: str | None,
    role: str | None,
    start_date: str | None,
    existing: list[Any],
    org_getter: Callable[[Any], str | None],
) -> DupeVerdict:
    """New-entry guard for ExperienceBase engagements (work/project/volunteer).

    The LLM reconciler owns entity identity (ADR-046) — this fires only when it
    said "new entry" (no target). MATCH needs a strong signal: org near-dupe AND
    equal start month on both sides. Org near-dupe with a matching/contained
    role but absent or differing dates is AMBIGUOUS → confirmation.

    ADR-046 (amended 2026-07-16, #177 review): bare single-token org containment
    ('Ford' ⊂ 'Ford Foundation') is NEVER identity here — two distinct employers
    can share one token. It always routes to AMBIGUOUS (ask), regardless of
    dates; only a 2+-token containment or a full near-dupe counts as SAME and
    can go on to the date-based MATCH check below.

    ADR-046 (amended 2026-07-16, #181 review): once the org is a strong match
    (SAME) but the start months don't confirm one stint, the only way to APPEND
    silently is a clearly DISTINCT role — that's a genuine second position at the
    same employer. Any weaker role signal (near/exact role, or NO role evidence at
    all) is ambiguous → ask. The old rule appended silently when role was absent,
    which could hide a duplicate whenever the reconciler omitted the role.
    """
    verdict = DupeVerdict()
    for entry in existing:
        org_rel = _field_relation(org, org_getter(entry), containment_is_same=False)
        if org_rel == _AMBIG:
            verdict.ambiguous.append(entry)
            continue
        if org_rel != _SAME:
            continue
        entry_start = getattr(entry, "start_date", None)
        if start_date and entry_start and _month(start_date) == _month(entry_start):
            verdict.match = entry
            return verdict
        role_rel = _field_relation(role, getattr(entry, "role", None),
                                   containment_is_same=False)
        # Strong org, unconfirmed dates: append only when the roles clearly differ
        # (_DISTINCT). Otherwise — including no role evidence (role_rel is None) — ask.
        if role_rel != _DISTINCT:
            verdict.ambiguous.append(entry)
    return verdict


# ── #239: certification identity — cross-language + symbol/cognate fold ──────
# A two-source import (CV PDF + LinkedIn PDF) produced EN/DE duplicate pairs
# ("Expert for Computersystemvalidation" / "Experte für Computervalidierung"),
# a trademark-symbol variant ("ITIL Foundation Level" / "ITIL® Foundation" —
# the ® fuses onto the adjacent token under skill_tokens, "itil®" ≠ "itil"),
# and a cognate-stem variant ("...Software Architect..." / "...Software
# Architecture..." — Jaccard 0.71, just under the 0.75 near-dupe threshold).
# skill_tokens/skills_near_dupe are lexical-only and ADR-046-hardened for
# SKILLS specifically (#172) — deliberately NOT touched here. Instead, a small
# curated pre-fold, scoped to this call site only, collapses the certification
# vocabulary variants seen in real imports before reusing the same token-set
# containment/Jaccard policy.

_CERT_SYMBOL_STRIP = re.compile(r"[®™]")

# Curated EN/DE cross-language + cognate-stem folds for certification names.
# Intentionally narrow (named pairs actually observed, #239) — not a general
# translator. "for"/"of" etc. are already dropped as English stopwords by
# skill_tokens; "für" is the German equivalent preposition and isn't, so it is
# folded to "for" here and then dropped by the same stopword pass below.
_CERT_TOKEN_FOLD: dict[str, str] = {
    "experte": "expert",
    "für": "for",
    "computervalidierung": "computersystemvalidation",
    "architecture": "architect",
}
_CERT_DROP_TOKENS = frozenset({"for"})

# Certifications get their own near-dupe threshold constant (same value as
# ats_audit._NEAR_DUPE_JACCARD today) so a future change to the skills
# threshold doesn't silently retune certification identity as a side effect.
_CERT_NEAR_DUPE_JACCARD = 0.75


def _cert_tokens(name: str) -> frozenset[str]:
    """Certification-specific token set (#239).

    Strips ®/™ trademark symbols (which otherwise fuse onto the adjacent
    token instead of being separated by whitespace) and folds a small curated
    set of EN/DE cross-language + cognate-stem pairs, then reuses the shared
    ``skill_tokens`` tokeniser for everything else (NFKC, dash-fold, casefold,
    stopwords, guarded plural fold).
    """
    stripped = _CERT_SYMBOL_STRIP.sub("", name)
    folded = {_CERT_TOKEN_FOLD.get(t, t) for t in skill_tokens(stripped)}
    return frozenset(t for t in folded if t not in _CERT_DROP_TOKENS)


def _cert_name_relation(a: str | None, b: str | None) -> int | None:
    """Relation between two certification names (#239).

    Same three-band policy as :func:`_field_relation` (SAME / AMBIGUOUS /
    DISTINCT / no evidence), computed over :func:`_cert_tokens` so the
    cross-language and symbol/cognate variants collapse before the
    containment-or-Jaccard check runs. Bare single-token containment is
    AMBIGUOUS, never auto-merged — same strict rule as skills (#172).
    """
    if not a or not b:
        return None
    ta, tb = _cert_tokens(a), _cert_tokens(b)
    if not ta or not tb:
        return None
    if ta == tb:
        return _SAME
    if ta <= tb and len(ta) >= 2:
        return _SAME
    if tb <= ta and len(tb) >= 2:
        return _SAME
    if len(ta & tb) / len(ta | tb) >= _CERT_NEAR_DUPE_JACCARD:
        return _SAME
    if (ta < tb and len(ta) == 1) or (tb < ta and len(tb) == 1):
        return _AMBIG
    return _DISTINCT


def _norm_credential_id(credential_id: str | None) -> str | None:
    if not credential_id:
        return None
    normed = credential_id.strip().casefold()
    return normed or None


def classify_certification_dupe(
    *,
    name: str,
    issuing_organization: str | None,
    credential_id: str | None,
    existing: list[Any],
    name_getter: Callable[[Any], str | None],
    org_getter: Callable[[Any], str | None],
    credential_id_getter: Callable[[Any], str | None],
) -> DupeVerdict:
    """New-entry guard for Certification identity (#239).

    The generic lexical near-dupe machinery let EN/DE cross-language pairs
    and symbol/morphological variants of the SAME certification through as
    silent new entries — worse than AMBIGUOUS, no confirmation ever. Identity
    anchors, strongest first:

    1. ``credential_id`` — an exact match (normalised) is definitive
       regardless of name or issuer; two providers can title the same
       credential differently, but they don't reuse each other's ID.
    2. name + issuing_organization, combined:
       - name SAME (near-dupe, containment, or same after the certification
         fold — see :func:`_cert_tokens`) and org SAME/absent → MATCH.
       - name SAME but org DISTINCT (a *confirmed* different issuer) →
         AMBIGUOUS — a name match against a confirmed-different issuer is
         exactly the "unsure" case, not a silent decision either way.
       - name AMBIGUOUS (bare single-token containment on the folded
         tokens), regardless of org → AMBIGUOUS.
       - name DISTINCT (genuinely different certification) → not a dupe
         candidate here; a matching issuer can't rescue it (two AWS certs
         from the same issuer are still two different certs).

    Never silently merges across a confirmed-different issuer, and never
    silently appends a weak/cross-language near-match — both surface to the
    user instead (#239 direction 3).
    """
    verdict = DupeVerdict()

    incoming_cred = _norm_credential_id(credential_id)
    if incoming_cred:
        for entry in existing:
            if _norm_credential_id(credential_id_getter(entry)) == incoming_cred:
                verdict.match = entry
                return verdict

    for entry in existing:
        name_rel = _cert_name_relation(name, name_getter(entry))
        org_rel = _field_relation(issuing_organization, org_getter(entry),
                                   containment_is_same=True)

        if name_rel == _DISTINCT:
            continue
        if name_rel == _SAME:
            if org_rel == _DISTINCT:
                verdict.ambiguous.append(entry)
            else:
                verdict.match = entry
                return verdict
            continue
        if name_rel == _AMBIG:
            verdict.ambiguous.append(entry)
            continue
        # name_rel is None: no name evidence at all (shouldn't happen — name
        # is always present on a certification); fall back to org alone.
        if org_rel == _SAME:
            verdict.ambiguous.append(entry)

    return verdict


# ── #618 (education half): education identity — date-range + institution-noise
# fold + a purely mechanical degree fold ──────────────────────────────────────
# A two-source import produced the SAME apprenticeship twice under Education:
# one source names the institution by its long legal form and the degree by an
# EN-ish job-title-shaped phrase; the other names the institution by its short
# colloquial name and the degree by the DE qualification name, and states the
# same date range at coarser precision (year-only vs month+year). The plain
# ``classify_dupe`` natural key (institution, degree) folds neither the
# institution alias nor the date-precision difference, so the pair created two
# rows (#618; see ``test_the_applier_natural_key_cannot_recognise_this_pair``
# in ``test_618_reconcile_no_duplicate_after_set_field.py`` for the ground
# truth that pinned this as ``classify_dupe``'s own gap, not a batch-shape bug).

_EDU_NEAR_DUPE_JACCARD = 0.75  # own name/value — mirrors _CERT_NEAR_DUPE_JACCARD's
# own comment: a future retune of the skills/cert threshold must not silently
# retune this one as a side effect.

# Legal-form and generic institution-type words, DROPPED (not folded) from an
# institution name before comparison — purely mechanical, no translation
# judgement. "Partner für Bildung" is the multi-word descriptor tail observed
# in real school-operator names; "für" itself also has to be dropped as its
# own token since skill_tokens only drops the English "for" as a stopword.
_EDU_INSTITUTION_DROP_TOKENS = frozenset({
    "gmbh", "co", "kg", "ag", "mbh", "ug", "ev",
    "hochschule", "universität", "universitat", "universitaet", "university",
    "fachhochschule", "fh", "schule", "school", "akademie", "academy",
    "institut", "institute", "college",
    "partner", "für", "bildung", "education",
})

# A SMALL, purely mechanical German-preposition/conjunction fold for degree
# text, routed through the same drop skill_tokens already gives "for"/"and"/
# "with" (English stopwords) — mirrors _CERT_TOKEN_FOLD's "für"->"for" trick.
# Deliberately NOT an EN/DE occupational-title translator: folding a
# "Computer System Developer" / "Fachinformatiker Anwendungsentwicklung"-shaped
# pair is a semantic judgement ADR-062 clause 1 reserves for the model — the
# same ruling ``test_the_applier_natural_key_cannot_recognise_this_pair``
# already draws for ``classify_dupe`` itself (see that test's own docstring).
# See ``classify_education_dupe``'s docstring for what this means downstream:
# a degree pair the fold cannot bridge does not make the pair DISTINCT, it
# makes the whole entry AMBIGUOUS once the institution already says SAME.
_EDU_DEGREE_TOKEN_FOLD: dict[str, str] = {
    "für": "for",
    "und": "and",
    "mit": "with",
}
_EDU_DEGREE_DROP_TOKENS = frozenset({"for", "and", "with"})


def _edu_institution_tokens(name: str) -> frozenset[str]:
    """Institution token set with legal-form/generic-institution-type noise
    dropped — see :data:`_EDU_INSTITUTION_DROP_TOKENS`. Purely subtractive,
    built on the shared :func:`skill_tokens` tokeniser."""
    return frozenset(
        t for t in skill_tokens(name) if t not in _EDU_INSTITUTION_DROP_TOKENS
    )


def _edu_degree_tokens(name: str) -> frozenset[str]:
    """Degree token set with the small mechanical preposition/conjunction fold
    applied — see :data:`_EDU_DEGREE_TOKEN_FOLD`."""
    folded = {_EDU_DEGREE_TOKEN_FOLD.get(t, t) for t in skill_tokens(name)}
    return frozenset(t for t in folded if t not in _EDU_DEGREE_DROP_TOKENS)


def _token_set_relation(
    ta: frozenset[str], tb: frozenset[str], *, jaccard: float
) -> int | None:
    """SAME/AMBIGUOUS/DISTINCT over two pre-tokenised sets — the same policy
    :func:`_cert_name_relation` implements for certifications, generalised
    here so the education instrument below does not re-implement it a second
    time. The two classifiers stay independent copies on purpose (editing one
    must never silently change the other's behaviour): exact equality, or
    containment where the contained side has >= 2 tokens, or a Jaccard
    overlap >= ``jaccard`` -> SAME; a bare single-token containment ->
    AMBIGUOUS (never silent identity — the #172 rule); any other evidenced
    pair -> DISTINCT. ``None`` when either set is empty (no evidence).
    """
    if not ta or not tb:
        return None
    if ta == tb:
        return _SAME
    if ta <= tb and len(ta) >= 2:
        return _SAME
    if tb <= ta and len(tb) >= 2:
        return _SAME
    if len(ta & tb) / len(ta | tb) >= jaccard:
        return _SAME
    if (ta < tb and len(ta) == 1) or (tb < ta and len(tb) == 1):
        return _AMBIG
    return _DISTINCT


def _edu_institution_relation(a: str | None, b: str | None) -> int | None:
    """Institution identity: the STRONGER of two signals wins (never vetoes).

    PLAIN — the existing ``classify_dupe`` behaviour (:func:`_field_relation`
    on the raw name), unchanged, so an already-working case like "Universität
    Würzburg" contained in "Julius-Maximilians-Universität Würzburg" keeps
    matching (the word "Universität" surviving as a token is exactly what
    lets that 2-token containment clear the >= 2 floor).

    STRIPPED — the noise-dropped relation (:func:`_edu_institution_tokens`),
    which catches an alias pair where the generic word IS what differs:
    "Provadis Partner für Bildung GmbH" / "Provadis Hochschule"-shaped pairs
    share no raw token at all, but both reduce to a single shared distinctive
    token once legal-form/institution-type noise is dropped from both sides.

    The combination is ``max(plain, stripped)`` (SAME > AMBIGUOUS > DISTINCT):
    stripping never loses a match the plain signal already had, and the plain
    signal never blocks a match only the stripped one can see.
    """
    if not a or not b:
        return None
    plain = _field_relation(a, b, containment_is_same=False)
    stripped = _token_set_relation(
        _edu_institution_tokens(a), _edu_institution_tokens(b),
        jaccard=_EDU_NEAR_DUPE_JACCARD,
    )
    candidates = [r for r in (plain, stripped) if r is not None]
    return max(candidates) if candidates else None


def _edu_degree_relation(a: str | None, b: str | None) -> int | None:
    """Degree identity over the mechanically-folded token set
    (:func:`_edu_degree_tokens`) — see :func:`classify_education_dupe`'s
    docstring for why this deliberately does not attempt EN/DE
    occupational-title translation."""
    if not a or not b:
        return None
    return _token_set_relation(
        _edu_degree_tokens(a), _edu_degree_tokens(b), jaccard=_EDU_NEAR_DUPE_JACCARD
    )


# ── education date-range relation ──────────────────────────────────────────────
# EducationEntry.start_date/end_date are free-form strings, NOT run through
# schemas.profile._coerce_partial_date (that helper doesn't even parse
# "09/2002" — see apply.py's _apply_upsert_education, which keeps them raw).

_EDU_MONTH_YEAR_RE = re.compile(r"\b(0[1-9]|1[0-2])[./](\d{4})\b")   # "09/2002", "09.2002"
_EDU_YEAR_MONTH_RE = re.compile(r"\b(\d{4})[-/.](0[1-9]|1[0-2])\b")  # "2002-09"
_EDU_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")                      # "2002"


def _parse_edu_date(value: str | None) -> tuple[int, int | None] | None:
    """Best-effort ``(year, month)`` from a free-form education date string.
    ``month`` is ``None`` when only a year could be read; the whole result is
    ``None`` when nothing date-shaped is found at all."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    m = _EDU_MONTH_YEAR_RE.search(s)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _EDU_YEAR_MONTH_RE.search(s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _EDU_YEAR_RE.search(s)
    if m:
        return int(m.group(0)), None
    return None


def _edu_ordinal(year: int, month: int | None, *, is_end: bool) -> int:
    """Month-resolution ordinal. A missing month expands to the COARSEST bound
    for its role — January for a range start, December for a range end — so a
    bare year "2002" reads as "no earlier than Jan 2002" when it is a start
    and "no later than Dec 2002" when it is an end."""
    if month is None:
        month = 12 if is_end else 1
    return year * 12 + (month - 1)


def _edu_range(start: str | None, end: str | None) -> tuple[int, int] | None:
    """``(lo, hi)`` month-ordinal range from an entry's start/end date
    strings, at whatever precision is available. ``None`` when neither side
    parses to anything date-shaped."""
    parsed_start = _parse_edu_date(start)
    parsed_end = _parse_edu_date(end)
    if parsed_start is None and parsed_end is None:
        return None
    lo = _edu_ordinal(*parsed_start, is_end=False) if parsed_start else None
    hi = _edu_ordinal(*parsed_end, is_end=True) if parsed_end else None
    if lo is None:
        lo = hi
    if hi is None:
        hi = lo
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _edu_date_relation(
    a_start: str | None, a_end: str | None, b_start: str | None, b_end: str | None
) -> int | None:
    """Date-range identity (#618): one range containing (or equalling) the
    other — at whatever precision each side states, a bare year expanding to
    Jan-Dec of that year — is SAME ("2002–2005" contains "09/2002–01/2005").
    A partial, non-containing overlap is AMBIGUOUS; no overlap at all is
    DISTINCT; unparseable/absent dates on either side are no evidence at all
    (``None``) and never veto a match built on the other two signals."""
    ra = _edu_range(a_start, a_end)
    rb = _edu_range(b_start, b_end)
    if ra is None or rb is None:
        return None
    (a_lo, a_hi), (b_lo, b_hi) = ra, rb
    if a_lo <= b_lo and b_hi <= a_hi:
        return _SAME
    if b_lo <= a_lo and a_hi <= b_hi:
        return _SAME
    if a_hi < b_lo or b_hi < a_lo:
        return _DISTINCT
    return _AMBIG


def classify_education_dupe(
    *,
    institution: str,
    degree: str,
    start_date: str | None,
    end_date: str | None,
    existing: list[Any],
    institution_getter: Callable[[Any], str | None],
    degree_getter: Callable[[Any], str | None],
    start_date_getter: Callable[[Any], str | None],
    end_date_getter: Callable[[Any], str | None],
) -> DupeVerdict:
    """New-entry guard for ``EducationEntry`` identity (#618 education half).

    Institution is the REQUIRED anchor (mirrors
    :func:`classify_certification_dupe`'s credential-id/name anchor): a
    DISTINCT or unevidenced institution rules an existing entry out as a
    candidate entirely, regardless of degree or dates. Once the institution
    says SAME, degree and dates are corroborators that can only ESCALATE the
    verdict to AMBIGUOUS — never silently downgrade it to DISTINCT. The
    failure this closes is a silent SECOND ROW; silently assuming two
    differently-worded degree titles are the same qualification would only
    trade that for an equally silent WRONG MERGE. Neither is acceptable, so
    the deterministic layer surfaces the question instead — the caller raises
    ``RequestConfirmation`` on an AMBIGUOUS verdict (mirrors every other
    ``_apply_upsert_*`` dupe classifier in this module; this function never
    raises anything itself).

    See :func:`_edu_institution_relation` for the institution fold (two
    combined signals — plain containment AND legal-form-noise-stripped
    aliasing), :func:`_edu_degree_relation` for the degree fold (a small
    mechanical preposition fold ONLY — deliberately not an EN/DE
    occupational-title translator, see its docstring), and
    :func:`_edu_date_relation` for the date-range containment/overlap signal.

    An AMBIGUOUS institution signal (bare single-token containment) parks
    regardless of degree/dates, mirroring how ``classify_certification_dupe``
    treats an AMBIGUOUS name relation.
    """
    verdict = DupeVerdict()
    for entry in existing:
        inst_rel = _edu_institution_relation(institution, institution_getter(entry))
        if inst_rel is None or inst_rel == _DISTINCT:
            continue
        if inst_rel == _AMBIG:
            verdict.ambiguous.append(entry)
            continue
        # inst_rel == _SAME — the anchor holds. Degree/dates may only escalate
        # to AMBIGUOUS from here, never veto to DISTINCT (see docstring).
        degree_rel = _edu_degree_relation(degree, degree_getter(entry))
        date_rel = _edu_date_relation(
            start_date, end_date, start_date_getter(entry), end_date_getter(entry)
        )
        if degree_rel in (_DISTINCT, _AMBIG) or date_rel in (_DISTINCT, _AMBIG):
            verdict.ambiguous.append(entry)
            continue
        if degree_rel != _SAME and date_rel != _SAME:
            # Adversarial pass 2026-09-01 — the institution anchor is necessary
            # but never sufficient. With NEITHER corroborator carrying evidence
            # (blank/unparseable degree AND absent/unparseable dates — the shape
            # a thin second source such as a LinkedIn education row produces),
            # the loop used to fall through to MATCH on whichever entry came
            # first: at a university holding a Bachelor and a Master, an
            # institution-only op merged into the Bachelor and _fill_empties
            # wrote the Master's field/grade onto that row. Two qualifications
            # became one, with no confirmation raised, and import_witness —
            # which shares this classifier by design — reported the discarded
            # one as matched rather than lost. That is precisely the "equally
            # silent WRONG MERGE" this function's own docstring refuses to
            # trade the second row for, so an unevidenced candidate is parked
            # as a question like any other ambiguity.
            verdict.ambiguous.append(entry)
            continue
        verdict.match = entry
        return verdict
    return verdict
