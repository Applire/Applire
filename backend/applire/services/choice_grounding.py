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

"""Deterministic grounding filter for interview starting-point chips (#110).

The Mode-A question generator drafts 2-3 first-person "starting point" chips a
candidate can select into the answer box. The LLM sees rich JD context but only
a thin profile summary, so a drafted chip can attribute JD technology to a
candidate who has never touched it (blind PQ 2026-07-02, F5) — one careless
click away from an invented project.

The prompt now instructs the model to ground chips in profile evidence; THIS
module is the guarantee. A chip that *asserts* experience with a cluster/JD
term survives only when the profile actually evidences that term (judged by
``surface_present``, the shared US212 presence predicate). Honesty frames —
chips that deny direct experience — may name the term; denying is the point.

Truthfulness-critical: sits directly beside the honesty pipeline (ADR-040).
"""

from typing import Any

from applire.services.ats_audit import _norm, surface_present

# A chip containing one of these (casefolded) markers is an honesty frame:
# it names a term to DENY or hedge direct experience, not to claim it.
# UI languages are en/de (chips are generated in the user's language).
_HONESTY_MARKERS: tuple[str, ...] = (
    # English
    "haven't",
    "have not",
    "has not",
    "not directly",
    "no direct",
    "not yet",
    "never worked",
    "don't have",
    "do not have",
    "closest experience",
    # German
    "nicht direkt",
    "bisher nicht",
    "noch nicht",
    "noch keine",
    "noch nie",
    "keine direkte",
    "habe ich nicht",
)


def _evidence_norm(profile: dict[str, Any]) -> str:
    """Normalised free-text haystack of everything the profile evidences."""
    parts: list[str] = []
    for skill in profile.get("skills") or []:
        if isinstance(skill, dict):
            parts.extend(str(v) for v in (skill.get("name"), skill.get("category")) if v)
        elif skill:
            parts.append(str(skill))
    for entry in (
        (profile.get("work_experience") or [])
        + (profile.get("projects") or [])
        + (profile.get("volunteer_activities") or [])
    ):
        if not isinstance(entry, dict):
            continue
        for key in ("company", "role", "name", "title", "industry_context", "description"):
            if entry.get(key):
                parts.append(str(entry[key]))
        for key in ("technologies", "responsibilities", "achievements"):
            parts.extend(str(v) for v in (entry.get(key) or []) if v)
    for entry in profile.get("certifications") or []:
        if isinstance(entry, dict) and entry.get("name"):
            parts.append(str(entry["name"]))
    for entry in profile.get("education") or []:
        if isinstance(entry, dict):
            parts.extend(str(v) for v in (entry.get("degree"), entry.get("field")) if v)
    return _norm("\n".join(parts))


def _cluster_terms(cluster: dict[str, Any]) -> list[str]:
    """The JD-side terms a chip could wrongly attribute to the candidate."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in [
        cluster.get("label") or "",
        *(cluster.get("gaps") or []),
        *(cluster.get("jd_skills") or []),
    ]:
        term = str(term).strip()
        n = _norm(term)
        if term and n and n not in seen:
            seen.add(n)
            terms.append(term)
    return terms


def _is_honesty_frame(choice: str) -> bool:
    # Real models emit typographic apostrophes ("haven’t", U+2019/U+02BC) —
    # normalise to ASCII before marker matching (blind agent probe 2026-07-11:
    # the ASCII-only match over-dropped truthful frames).
    folded = choice.casefold().replace("’", "'").replace("ʼ", "'")
    return any(marker in folded for marker in _HONESTY_MARKERS)


def filter_ungrounded_choices(
    choices: list[str] | None,
    cluster: dict[str, Any],
    profile: dict[str, Any],
    gap_category: str | None,  # noqa: ARG001 — one rule for all categories; kept for call-site clarity
) -> list[str] | None:
    """Drop chips that assert experience the profile doesn't evidence.

    Keep a chip when it is an honesty frame, or when every cluster/JD term it
    mentions is evidenced in the profile. Returns ``None`` when nothing
    survives (the UI then shows the plain answer box — no scaffold beats a
    fabricated one).
    """
    if not choices:
        return None

    evidence = _evidence_norm(profile)
    terms = _cluster_terms(cluster)

    kept: list[str] = []
    for choice in choices:
        text = str(choice).strip()
        if not text:
            continue
        if _is_honesty_frame(text):
            kept.append(text)
            continue
        choice_norm = _norm(text)
        asserted = [t for t in terms if surface_present(t, choice_norm)]
        if all(surface_present(t, evidence) for t in asserted):
            kept.append(text)
    return kept or None
