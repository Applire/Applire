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
Deterministic match-score computation (ADR-035, US113).

Turns per-requirement classifications (direct / partial / gap / denied — the
four statuses of ADR-048 §1 as amended 2026-07-27) produced by the gap-analysis
LLM into a single normalised score and derived category lists.
The JD requirement set is authoritative — sources and slot weights are assigned
here, never by the LLM.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Slot weights by JD source
REQUIRED_SLOT = 1.0
NICE_TO_HAVE_SLOT = 0.5

# Earning factors by classification status
# "denied" earns exactly what "gap" earned (ADR-048 amended 2026-07-27), so the
# four-value vocabulary leaves the ADR-035 headline number undrifted — a denial
# was already unclaimable, it is now merely distinguishable from "unknown".
_FACTOR: dict[str, float] = {"direct": 1.0, "partial": 0.5, "gap": 0.0, "denied": 0.0}

# Categorisation of `denied` (#383, ADR-059 amended 2026-07-27 / ADR-048 §1
# amended the same day)
# ----------------------------------------------------------------------------
# The four statuses are `direct` | `partial` | `gap` (**unknown** — no signal)
# | `denied` (asked, and the candidate stated they do not have it). Scoring
# treats the last two alike on purpose (see _FACTOR); CATEGORISING them alike
# was a defect. Until #383 the categorisation branch ended in a catch-all
# `else: # gap`, so a denied required concept was appended to `critical_gaps`
# — the list that reached the CV writer as "CRITICAL GAPS (acknowledge in
# summary if applicable)", contradicting the same prompt's rule 3a ("EXPLICITLY
# DENIED … simply OMIT it"), and reached the gaps screen and the clustering
# input as an open gap to address, re-asking what the candidate had already
# answered.
#
# A `denied` requirement therefore enters NO derived list here: not
# `category_a`/`b`/`c`, not `critical_gaps`, not `minor_gaps`. It is neither a
# match nor an unknown, and there is no un-denial path for the candidate to
# "address" it (ADR-059 amended 2026-07-26). It is not suppressed either: it
# keeps its slot in the denominator, it appears in `requirement_breakdown`
# carrying `status: "denied"`, and the ledger entry it came from is what routes
# it to POSITIONING in the writers (ADR-059 amended 2026-07-27 clause 6 —
# `split_ledger_for_prompt`'s "EXPLICITLY DENIED BY THE CANDIDATE" block), a
# channel that does not run through any gap list.
#
# ADR-062 clause 1/6 declaration: this is a FACT, not a judgement — it reads one
# enum off a data structure and never interprets prose.


def _norm(s: str) -> str:
    """Normalise a requirement string for case-insensitive matching."""
    return (s or "").strip().casefold()


def compute_match_score(
    classifications: list[dict[str, Any]],
    required_skills: list[str],
    nice_to_have_skills: list[str],
) -> dict[str, Any]:
    """Compute a deterministic match score from LLM classifications.

    Args:
        classifications: List of dicts with keys ``requirement``, ``status``
            (``"direct"`` | ``"partial"`` | ``"gap"`` | ``"denied"``), and
            ``reason``.
        required_skills: JD required-skills list (slot weight 1.0).
        nice_to_have_skills: JD nice-to-have list (slot weight 0.5).

    Returns:
        Dict with keys:
            match_score, category_a, category_b, category_c,
            critical_gaps, minor_gaps, requirement_breakdown.
        ``match_score`` is ``None`` when there are no JD requirements.
    """
    # Build a normalised lookup from the LLM classifications.
    # If duplicates exist (same normalised key) keep the first occurrence.
    cls_map: dict[str, dict[str, Any]] = {}
    for item in classifications:
        key = _norm(item.get("requirement", ""))
        if key and key not in cls_map:
            cls_map[key] = item

    # Assemble the authoritative JD requirement set: (original_text, slot, source)
    jd_requirements: list[tuple[str, float, str]] = [
        (req, REQUIRED_SLOT, "required") for req in required_skills
    ] + [
        (req, NICE_TO_HAVE_SLOT, "nice_to_have") for req in nice_to_have_skills
    ]

    # Warn about LLM items that match no JD requirement.
    for ckey in cls_map:
        matched = any(
            ckey == _norm(req) or ckey in _norm(req) or _norm(req) in ckey
            for req, _, _ in jd_requirements
        )
        if not matched:
            logger.warning(
                "compute_match_score: LLM classification %r matches no JD requirement — dropped",
                ckey,
            )

    if not jd_requirements:
        return {
            "match_score": None,
            "category_a": [],
            "category_b": [],
            "category_c": [],
            "critical_gaps": [],
            "minor_gaps": [],
            "requirement_breakdown": [],
        }

    # Iterate over each JD requirement, find its classification (or default gap).
    category_a: list[str] = []
    category_b: list[str] = []
    category_c: list[str] = []
    critical_gaps: list[str] = []
    minor_gaps: list[str] = []
    breakdown: list[dict[str, Any]] = []

    earned_total = 0.0
    n_total = 0.0

    for req, slot, source in jd_requirements:
        rkey = _norm(req)

        # Fix 2: skip empty JD requirements — prevents "" from substring-matching everything.
        if not rkey:
            continue

        # Find a matching classification using exact → longest-substring fallback.
        # Longest-match prevents "React" from inheriting "React Native"'s classification
        # when both appear in the JD (Fix 1).
        matched_item: dict[str, Any] | None = None
        if rkey in cls_map:
            matched_item = cls_map[rkey]
        else:
            candidates = [
                (len(ckey), item)
                for ckey, item in cls_map.items()
                if rkey in ckey or ckey in rkey
            ]
            matched_item = max(candidates, key=lambda c: c[0])[1] if candidates else None

        status = matched_item["status"] if matched_item else "gap"
        reason = matched_item.get("reason", "") if matched_item else ""

        # Sanitise unknown statuses to "gap".
        if status not in _FACTOR:
            logger.warning(
                "compute_match_score: unknown status %r for %r, treating as gap",
                status,
                req,
            )
            status = "gap"

        factor = _FACTOR[status]
        earned = slot * factor

        n_total += slot
        earned_total += earned

        # Categorise.
        # ADR-035: a `partial` on a required skill is half-credit and goes to
        # minor_gaps, NOT critical_gaps — intentional design, do not change.
        if status == "direct":
            category_a.append(req)
        elif status == "partial":
            category_b.append(req)
            minor_gaps.append(req)
        elif status == "gap":  # gap == UNKNOWN, never "the candidate said no"
            category_c.append(req)
            if source == "required":
                critical_gaps.append(req)
            else:
                minor_gaps.append(req)
        # `denied` (#383) deliberately reaches NO derived list — see the
        # categorisation note above _FACTOR. It is still scored and still
        # reported in `requirement_breakdown` below.

        breakdown.append(
            {
                "requirement": req,
                "source": source,
                "status": status,
                "slot": slot,
                "earned": earned,
                "reason": reason,
            }
        )

    # Compute and clamp the final score.
    raw_score = earned_total / n_total
    match_score = max(0.0, min(1.0, raw_score))  # algebraically bounded to [0,1]; clamped defensively

    return {
        "match_score": match_score,
        "category_a": category_a,
        "category_b": category_b,
        "category_c": category_c,
        "critical_gaps": critical_gaps,
        "minor_gaps": minor_gaps,
        "requirement_breakdown": breakdown,
    }


def compute_match_score_from_ledger(
    ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the match score from the Keyword Ledger (ADR-048 §5, US199).

    Re-sources the ADR-035 score from the ledger's fit-weighted slice — the
    single source of truth — instead of a parallel classification list. The
    formula and weights are unchanged from :func:`compute_match_score`: only the
    input moves. Each ledger entry with ``fit_weight > 0`` is one requirement
    slot — ``required`` source → slot 1.0, otherwise nice_to_have → slot 0.5 —
    and ``status`` drives the earning factor (direct 1.0, partial 0.5, gap 0.0,
    denied 0.0). Keyword-only entries (``fit_weight == 0``) never affect the
    score. A ``denied`` entry scores exactly as before but is categorised into
    no gap list (#383) — see the note above ``_FACTOR``.

    Args:
        ledger: Keyword Ledger entries, each with keys ``concept``, ``sources``,
            ``fit_weight``, and ``status``.

    Returns:
        The same dict shape as :func:`compute_match_score`:
            match_score, category_a, category_b, category_c,
            critical_gaps, minor_gaps, requirement_breakdown.
        ``match_score`` is ``None`` when there are no fit-weighted entries.
    """
    category_a: list[str] = []
    category_b: list[str] = []
    category_c: list[str] = []
    critical_gaps: list[str] = []
    minor_gaps: list[str] = []
    breakdown: list[dict[str, Any]] = []

    earned_total = 0.0
    n_total = 0.0

    for entry in ledger:
        slot = entry.get("fit_weight", 0.0)
        # Keyword-only entries carry no fit weight — they drive ATS coverage,
        # never the fit score (mirrors compute_match_score's slot model).
        if not slot:
            continue

        concept = entry.get("concept", "")
        source = "required" if "required" in entry.get("sources", []) else "nice_to_have"

        status = entry.get("status", "gap")
        if status not in _FACTOR:
            logger.warning(
                "compute_match_score_from_ledger: unknown status %r for %r, treating as gap",
                status,
                concept,
            )
            status = "gap"

        factor = _FACTOR[status]
        earned = slot * factor

        n_total += slot
        earned_total += earned

        # Categorise — identical rules to compute_match_score (ADR-035): a
        # `partial` on a required slot is half-credit and goes to minor_gaps.
        if status == "direct":
            category_a.append(concept)
        elif status == "partial":
            category_b.append(concept)
            minor_gaps.append(concept)
        elif status == "gap":  # gap == UNKNOWN, never "the candidate said no"
            category_c.append(concept)
            if source == "required":
                critical_gaps.append(concept)
            else:
                minor_gaps.append(concept)
        # `denied` (#383) deliberately reaches NO derived list — see the
        # categorisation note above _FACTOR. It is still scored and still
        # reported in `requirement_breakdown` below.

        breakdown.append(
            {
                "requirement": concept,
                "source": source,
                "status": status,
                "slot": slot,
                "earned": earned,
                "reason": entry.get("evidence", ""),
            }
        )

    if n_total == 0.0:
        return {
            "match_score": None,
            "category_a": [],
            "category_b": [],
            "category_c": [],
            "critical_gaps": [],
            "minor_gaps": [],
            "requirement_breakdown": [],
        }

    raw_score = earned_total / n_total
    match_score = max(0.0, min(1.0, raw_score))  # algebraically bounded to [0,1]; clamped defensively

    return {
        "match_score": match_score,
        "category_a": category_a,
        "category_b": category_b,
        "category_c": category_c,
        "critical_gaps": critical_gaps,
        "minor_gaps": minor_gaps,
        "requirement_breakdown": breakdown,
    }
