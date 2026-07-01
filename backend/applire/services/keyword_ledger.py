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

"""
Deterministic Keyword Ledger builder (ADR-048, E037/US198).

The ledger is the single source of truth for every JD expectation. Each entry
carries a ``concept`` (drives the fit score via ADR-035) and its literal
``surface_forms`` (drive ATS coverage via ADR-039), classified ``direct`` /
``partial`` / ``gap`` against the profile with supporting ``evidence``.

The LLM supplies only the status, evidence, and surface forms. Python is
authoritative for ``sources`` (which JD list each expectation came from) and the
derived ``fit_weight`` — never the LLM (mirrors ADR-035's slot-weight rule).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# fit_weight by the strongest source a concept belongs to.
REQUIRED_WEIGHT = 1.0
NICE_TO_HAVE_WEIGHT = 0.5
KEYWORD_ONLY_WEIGHT = 0.0

_VALID_STATUS = {"direct", "partial", "gap"}


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def _matches(a_norm: str, b_norm: str) -> bool:
    """Exact or substring (either direction) — mirrors compute_match_score()."""
    if not a_norm or not b_norm:
        return False
    return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm


def _fit_weight(sources: set[str]) -> float:
    if "required" in sources:
        return REQUIRED_WEIGHT
    if "nice_to_have" in sources:
        return NICE_TO_HAVE_WEIGHT
    return KEYWORD_ONLY_WEIGHT  # keyword-only


def split_ledger_for_prompt(
    keyword_ledger: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split a Keyword Ledger into the two lists a generator prompt needs (ADR-048 §8).

    Returns ``(claimable, forbidden)`` where:
      * ``claimable`` — entries the candidate truthfully supports (``claimable`` True,
        i.e. status direct/partial). Each carries ``concept``, ``surface_forms`` and the
        profile ``evidence`` so the generator surfaces the term ONLY where supported.
      * ``forbidden`` — the honest-gap concepts (``claimable`` False). These are NOT in
        the profile and must NEVER be claimed — surfaced to the prompt as a do-not-claim
        list only.

    Pure; tolerant of ``None``/empty (legacy pre-E037 gap rows have no ledger).
    """
    claimable: list[dict[str, Any]] = []
    forbidden: list[str] = []
    for entry in keyword_ledger or []:
        if entry.get("claimable"):
            claimable.append(entry)
        else:
            concept = entry.get("concept")
            if concept:
                forbidden.append(concept)
    return claimable, forbidden


def render_ledger_prompt_block(keyword_ledger: list[dict[str, Any]] | None) -> str:
    """Render the Keyword Ledger as a prompt fragment shared by the CV and cover-letter
    generators (ADR-048 §8 / US200/US201).

    States the grounding-outranks-coverage precedence, lists each CLAIMABLE concept with
    its literal surface forms + the profile evidence that supports it, and lists the
    honest-gap concepts under an explicit DO NOT CLAIM heading. Returns "" when the ledger
    is empty so legacy callers and pre-E037 rows add nothing.
    """
    claimable, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not claimable and not forbidden:
        return ""

    lines: list[str] = [
        "KEYWORD LEDGER (ADR-048) — grounding strictly OUTRANKS coverage:",
        "Surface a claimable keyword ONLY where the listed profile evidence supports it; "
        "if surfacing it would need any stretch, drop it. These are estimates of an "
        "unknowable target, so do NOT over-stuff. NEVER claim a do-not-claim term.",
        "",
        "CLAIMABLE (supported by the profile — surface these, using the evidence as your basis):",
    ]
    if claimable:
        for entry in claimable:
            forms = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
            evidence = entry.get("evidence", "") or "(no extra evidence given)"
            lines.append(f"  - {entry.get('concept', '')} [forms: {forms}] — evidence: {evidence}")
    else:
        lines.append("  (none)")

    lines += [
        "",
        "DO NOT CLAIM (honest gaps — NOT in the profile; never present these as something "
        "the candidate has, has done, or knows):",
    ]
    if forbidden:
        for concept in forbidden:
            lines.append(f"  - {concept}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def claimable_surface_forms(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Flatten every surface form of every CLAIMABLE ledger entry (ADR-048 / US203).

    Used by the ATS audit to decide whether a MISSING keyword is a *missing-claimable*
    (the candidate supports it per the ledger, so it should have been surfaced) or a
    *missing-honest-gap* (not in the profile — honestly absent). De-duplicated, order
    preserved. ``None``/empty tolerant (legacy pre-E037 rows have no ledger).
    """
    forms: list[str] = []
    seen: set[str] = set()
    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    for entry in claimable:
        for sf in entry.get("surface_forms") or [entry.get("concept", "")]:
            key = _norm(sf)
            if key and key not in seen:
                seen.add(key)
                forms.append(sf)
    return forms


def keyword_only_honest_gaps(keyword_ledger: list[dict[str, Any]] | None) -> list[str]:
    """Honest gaps that are pure ATS keywords (US204, ADR-048 §10).

    Returns the concepts of ledger entries that are NOT claimable AND carry no fit
    weight (keyword-only). Required/nice_to_have honest gaps already reach the
    interview via ``category_c`` (compute_match_score_from_ledger), so they are
    excluded here to avoid double-routing. Pure; tolerant of ``None``/empty.
    """
    return [
        entry.get("concept", "")
        for entry in (keyword_ledger or [])
        if not entry.get("claimable") and not entry.get("fit_weight") and entry.get("concept")
    ]


def render_ledger_reviewer_block(
    keyword_ledger: list[dict[str, Any]] | None,
) -> str:
    """Render the Keyword Ledger as a block appended to the REVIEWER source (ADR-048 §8 /
    US202).

    The reviewer reads this to perform two checks that the bounded ADR-047 refine loop
    then acts on (no new loop, no forced injection):
      * report which CLAIMABLE keywords are ABSENT from the draft, and
      * flag any forbidden honest-gap concept that appears as a claim.

    Grounding still strictly OUTRANKS coverage — an absent-claimable note is a *surfacing*
    suggestion, never licence to fabricate. Returns "" for an empty/legacy ledger.
    """
    claimable, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not claimable and not forbidden:
        return ""

    lines: list[str] = [
        "KEYWORD LEDGER (ADR-048) — for your two ledger checks. Grounding strictly "
        "OUTRANKS coverage: an absent claimable keyword is a surfacing suggestion only, "
        "NEVER a reason to fabricate or stretch.",
        "",
        "CLAIMABLE KEYWORDS (the candidate truthfully supports these). Report any that are "
        "ABSENT from the draft as an issue so the writer can surface them where the evidence "
        "supports it — do NOT force a term that does not fit:",
    ]
    if claimable:
        for entry in claimable:
            forms = ", ".join(entry.get("surface_forms") or [entry.get("concept", "")])
            lines.append(f"  - {entry.get('concept', '')} [forms: {forms}]")
    else:
        lines.append("  (none)")

    lines += [
        "",
        "DO NOT CLAIM (honest gaps — NOT in the profile). Flag any of these that the draft "
        "presents as something the candidate has, has done, or knows — that is a fabrication:",
    ]
    if forbidden:
        for concept in forbidden:
            lines.append(f"  - {concept}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def build_keyword_ledger(
    classifications: list[dict[str, Any]],
    required_skills: list[str],
    nice_to_have_skills: list[str],
    keywords: list[str],
) -> list[dict[str, Any]]:
    """Build the Keyword Ledger from LLM classifications + the JD's own lists.

    Args:
        classifications: LLM output, one per concept:
            ``{concept, status, evidence, surface_forms?}``.
        required_skills / nice_to_have_skills / keywords: the JD's three lists.

    Returns:
        A list of ledger-entry dicts, each:
            ``{concept, surface_forms[], sources[], fit_weight, status,
               evidence, claimable}``.
        ``claimable`` is ``status in {direct, partial}``.
    """
    # Authoritative JD expectation set: norm_key -> {"text", "sources"}.
    union: dict[str, dict[str, Any]] = {}
    for src, items in (
        ("required", required_skills or []),
        ("nice_to_have", nice_to_have_skills or []),
        ("keyword", keywords or []),
    ):
        for raw in items:
            key = _norm(raw)
            if not key:
                continue
            entry = union.setdefault(key, {"text": raw, "sources": set()})
            entry["sources"].add(src)

    covered: set[str] = set()
    ledger: list[dict[str, Any]] = []

    for item in classifications:
        concept = item.get("concept", "")
        if not _norm(concept):
            continue
        surface_forms = item.get("surface_forms") or [concept]
        # Match the concept + each surface form against the JD union.
        probes = {_norm(concept)} | {_norm(sf) for sf in surface_forms}
        matched_keys = {
            ukey for ukey in union for p in probes if _matches(p, ukey)
        }
        if not matched_keys:
            logger.warning(
                "build_keyword_ledger: concept %r matches no JD expectation — dropped",
                concept,
            )
            continue
        covered |= matched_keys

        sources: set[str] = set()
        for ukey in matched_keys:
            sources |= union[ukey]["sources"]

        status = item.get("status", "gap")
        if status not in _VALID_STATUS:
            logger.warning(
                "build_keyword_ledger: unknown status %r for %r, treating as gap",
                status,
                concept,
            )
            status = "gap"
        claimable = status in {"direct", "partial"}

        ledger.append(
            {
                "concept": concept,
                "surface_forms": list(surface_forms),
                "sources": sorted(sources),
                "fit_weight": _fit_weight(sources),
                "status": status,
                "evidence": (item.get("evidence", "") if claimable else ""),
                "claimable": claimable,
            }
        )

    # Any JD expectation the LLM did not classify defaults to a gap entry —
    # never silent credit (mirrors ADR-035's unclassified-defaults-to-gap rule).
    for ukey, info in union.items():
        if ukey in covered:
            continue
        sources = info["sources"]
        ledger.append(
            {
                "concept": info["text"],
                "surface_forms": [info["text"]],
                "sources": sorted(sources),
                "fit_weight": _fit_weight(sources),
                "status": "gap",
                "evidence": "",
                "claimable": False,
            }
        )

    return ledger
