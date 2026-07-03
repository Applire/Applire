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
import re
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


def _tokens(s: str) -> list[str]:
    """Normalised word tokens of a concept, e.g. "CI/CD pipelines" -> ["ci", "cd", "pipelines"]."""
    return [t for t in re.split(r"[^a-z0-9]+", _norm(s)) if t]


def _is_token_prefix_dup(a_tokens: list[str], b_tokens: list[str]) -> bool:
    """True when the shorter token list is a leading slice of the longer one.

    This catches the LLM emitting both a short keyword and the JD's full phrase
    ("Kubernetes" vs "Kubernetes (production at scale)" -> ["kubernetes"] is a
    prefix of ["kubernetes", "production", "at", "scale"]). It deliberately does
    NOT treat a sub-token ("java" in "javascript") or a mid-phrase token ("saas"
    inside "multi tenant saas platform scaling") as a duplicate.
    """
    if not a_tokens or not b_tokens:
        return False
    short, long = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    return long[: len(short)] == short


def _surface_norm_set(entry: dict[str, Any]) -> set[str]:
    """Normalised surface forms of an entry, including its own concept name."""
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    s = {_norm(f) for f in forms if _norm(f)}
    s.add(_norm(entry.get("concept", "")))
    s.discard("")
    return s


def _surface_forms_norm(entry: dict[str, Any]) -> set[str]:
    """Normalised surface forms only — WITHOUT the concept name folded in."""
    forms = entry.get("surface_forms") or [entry.get("concept", "")]
    return {_norm(f) for f in forms if _norm(f)}


def _is_mirror_surface_dup(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two entries are clearly the same concept via their surface forms.

    Two independent signals, both conservative:

    1. **Mutual concept membership** — each entry's canonical name appears among
       the other's surface forms (``AI`` ↔ ``Artificial Intelligence``). A
       one-directional overlap (``Collaborative Research`` lists ``Research`` but
       not vice versa) is deliberately NOT enough.
    2. **Identical surface-form sets** — the LLM emitted the same concept twice
       under different canonical names (``Artificial Intelligence (AI)`` vs
       ``AI``) where neither name is the other's surface form, but both list the
       exact same forms. Distinct concepts never share an identical multi-form
       set (``Algorithm design`` and ``Algorithm development`` do not), so this
       stays safe from false merges.
    """
    a_c, b_c = _norm(a.get("concept", "")), _norm(b.get("concept", ""))
    if not a_c or not b_c:
        return False
    if a_c in _surface_norm_set(b) and b_c in _surface_norm_set(a):
        return True
    a_sf = _surface_forms_norm(a)
    return bool(a_sf) and a_sf == _surface_forms_norm(b)


def _collapse_prefix_duplicates(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate concepts that share the same status (E037 F2 + follow-up).

    The LLM often emits the same skill twice — either as a short keyword and the
    JD's full requirement phrase (``Kubernetes`` vs ``Kubernetes (production at
    scale)``, a *token prefix*), or as an acronym and its expansion that each list
    the other as a surface form (``AI`` ↔ ``Artificial Intelligence``, a *mirror
    surface-form* duplicate). Left as two entries they clutter the gap list and
    double-count the fit slot. Merge each such pair into one entry keyed by the
    *shorter* concept (the cleaner ATS label), unioning surface forms and sources,
    recomputing ``fit_weight`` from the merged sources, and keeping the first
    non-empty evidence. Merging is confined to entries with an identical status so
    a claimable form can never absorb a gap form (or vice versa). Group order
    follows first appearance; downstream consumers count entries, never assume a
    fixed count.
    """
    groups: list[dict[str, Any]] = []
    for entry in ledger:
        toks = _tokens(entry.get("concept", ""))
        home = None
        for g in groups:
            if g["status"] != entry.get("status"):
                continue
            if _is_token_prefix_dup(toks, g["tokens"]) or any(
                _is_mirror_surface_dup(entry, m) for m in g["members"]
            ):
                home = g
                break
        if home is None:
            groups.append({"status": entry.get("status"), "tokens": toks, "members": [entry]})
        else:
            home["members"].append(entry)
            # Keep the shortest member's tokens as the group's canonical anchor.
            if len(toks) < len(home["tokens"]):
                home["tokens"] = toks

    merged: list[dict[str, Any]] = []
    for g in groups:
        members = g["members"]
        if len(members) == 1:
            merged.append(members[0])
            continue
        # Canonical concept = the member with the fewest tokens (shortest label).
        canonical = min(members, key=lambda m: len(_tokens(m.get("concept", ""))))
        surface_forms: list[str] = []
        seen_forms: set[str] = set()
        sources: set[str] = set()
        evidence = ""
        for m in members:
            for sf in m.get("surface_forms") or [m.get("concept", "")]:
                if _norm(sf) and _norm(sf) not in seen_forms:
                    seen_forms.add(_norm(sf))
                    surface_forms.append(sf)
            sources |= set(m.get("sources", []))
            if not evidence and m.get("evidence"):
                evidence = m["evidence"]
        merged.append(
            {
                "concept": canonical.get("concept", ""),
                "surface_forms": surface_forms,
                "sources": sorted(sources),
                "fit_weight": _fit_weight(sources),
                "status": g["status"],
                "evidence": evidence if canonical.get("claimable") else "",
                "claimable": canonical.get("claimable", False),
            }
        )
    return merged


def _enforce_gap_stance(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ledger's own honest-gap verdict outranks claimable aliasing (F4, blind
    PQ 2026-07-02 — trust-critical).

    The LLM classified the JD's compound requirement "Cloud environment
    qualification (AWS, Azure)" as partial on AWS-only evidence but echoed
    "Azure" among its surface forms, while the SAME response classified "Azure"
    itself as a gap. Every claimable-surface-form consumer (ATS audit buckets,
    generator/reviewer prompt blocks) then presented Azure as "supported by your
    profile" — inverting the truthfulness promise for a skill the user had
    explicitly denied. Two deterministic rules, applied after duplicate collapse:

    1. A claimable entry whose CONCEPT the ledger elsewhere classifies "gap" is
       dropped — the honest side wins a direct contradiction (never-claim
       outranks claim, ADR-040/ADR-048).
    2. A claimable entry's surface forms are stripped of any form norm-EQUAL to
       an honest-gap concept (exact match only — never substrings, so "Docker"
       survives a "Docker Swarm" gap). If nothing survives, the entry keeps its
       own concept (builder invariant: surface_forms is never empty).
    """
    gap_concepts = {
        _norm(e.get("concept", "")) for e in ledger if not e.get("claimable")
    }
    gap_concepts.discard("")
    if not gap_concepts:
        return ledger

    result: list[dict[str, Any]] = []
    for entry in ledger:
        if entry.get("claimable"):
            if _norm(entry.get("concept", "")) in gap_concepts:
                logger.warning(
                    "_enforce_gap_stance: dropped claimable duplicate of honest-gap "
                    "concept %r (gap verdict wins)",
                    entry.get("concept"),
                )
                continue
            forms = [
                f
                for f in (entry.get("surface_forms") or [entry.get("concept", "")])
                if _norm(f) not in gap_concepts
            ]
            if len(forms) != len(entry.get("surface_forms") or []):
                logger.info(
                    "_enforce_gap_stance: stripped honest-gap surface forms from "
                    "claimable concept %r",
                    entry.get("concept"),
                )
            entry = {**entry, "surface_forms": forms or [entry.get("concept", "")]}
        result.append(entry)
    return result


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


def unclaimable_surface_forms(
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Flatten every surface form of every NON-claimable (honest-gap) ledger entry.

    Used by the ATS audit's fourth quadrant (ADR-048 amended 2026-07-03, #117): a
    keyword PRESENT in the document whose only ledger backing is an honest gap is
    an unsupported claim and gets a truthfulness warning. De-duplicated, order
    preserved. ``None``/empty tolerant (legacy pre-E037 rows have no ledger).
    """
    forms: list[str] = []
    seen: set[str] = set()
    for entry in keyword_ledger or []:
        if entry.get("claimable"):
            continue
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

    return _enforce_gap_stance(_collapse_prefix_duplicates(ledger))
