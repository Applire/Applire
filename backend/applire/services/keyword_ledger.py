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
