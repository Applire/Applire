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
