# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The page ladder: what a CV gives up, in order, when its CONTENT outgrows the page
target after normal condensing (Hazelnut build 3, #786 L9/L18, founder design).

``condense_to_budget`` (``cv_budget``) cuts role bullets only, so a CV whose length is
carried by list sections — a top-level PROJEKTE block repeating two role projects,
31 skill chips, eight identical "Branche:" lines, five certifications — returned
``changed=False`` and shipped at 3 or even 5 pages on a 2-page target (edge
2026-08-29 CV 4121fa73; 2026-09-18 CV 8ad60e4d; UX audit 2026-10-20 Emma).

The founder's rule: never more than ``target + 1`` pages; the user decides whether
the extra page stays. The ladder runs, rung by rung, only while the rendered page
count is over the limit:

1. a top-level project that repeats a project already nested under a role,
2. skill chips with no relevance to THIS job (the #192 rank, tier 3),
3. a "Branche:" line that repeats the line of an earlier role,
4. the oldest certification, one at a time,
5. the oldest education entry beyond the two most recent, one at a time.

It never touches a role's bullets (a role never loses its last line to this ladder),
a summary, a JD-relevant skill or a pinned fact's carrier, and every cut is named so
the length finding can say exactly what went.

This module is pure: it proposes cut steps over a ``tailored_data`` dict. The caller
(``services.cv._measure_and_condense``) renders between steps and stops at the limit
(ADR-062 clause 1: every step is a fact — a duplicate name, a tier, an identical
string, a date order — never a judgement about content).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

# The kinds a cut can have — stable ids the frontend and the ATS finding key on.
DUPLICATE_PROJECT = "duplicate_project"
OFF_JOB_SKILLS = "off_job_skills"
REPEATED_INDUSTRY = "repeated_industry"
OLDEST_CERTIFICATION = "oldest_certification"
OLDEST_EDUCATION = "oldest_education"

_KEEP_EDUCATION = 2
_YEAR = re.compile(r"(\d{4})")


@dataclass(frozen=True)
class LadderCut:
    """One applied (or proposed) cut: its kind and the human-readable items it removed."""

    kind: str
    items: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "items": list(self.items)}


@dataclass
class LadderStep:
    """One step: the data after the cut, and the cut itself."""

    data: dict
    cut: LadderCut
    extra: dict = field(default_factory=dict)


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _year_key(*values: Any) -> int:
    for v in values:
        m = _YEAR.search(str(v or ""))
        if m:
            return int(m.group(1))
    return 0


def _duplicate_projects(data: dict) -> list[int]:
    """Indexes of top-level projects whose name repeats a role's nested project."""
    nested = {
        _norm(p.get("name"))
        for w in data.get("work_history") or []
        for p in (w.get("projects") or [])
        if _norm(p.get("name"))
    }
    return [
        i for i, p in enumerate(data.get("projects") or [])
        if _norm(p.get("name")) and _norm(p.get("name")) in nested
    ]


def _repeated_industry(data: dict) -> list[int]:
    """Indexes of roles whose industry line repeats an EARLIER role's line verbatim
    (case/space-insensitive); the first occurrence stays."""
    seen: set[str] = set()
    out: list[int] = []
    for i, w in enumerate(data.get("work_history") or []):
        key = _norm(w.get("industry_context"))
        if not key:
            continue
        if key in seen:
            out.append(i)
        seen.add(key)
    return out


def ladder_steps(
    data: dict,
    skill_tier: Callable[[str], int] | None = None,
    *,
    pinned: Callable[[str], bool] | None = None,
) -> Iterator[LadderStep]:
    """Yield the ladder's steps in order, each applied on top of the previous one.

    ``skill_tier`` is the #192 rank (``services.cv.skill_jd_tier_fn``); without it
    the skills rung is skipped (no rank, no cut). ``pinned`` names text a fact pin
    protects — a protected skill or certification is never cut.
    """
    current = copy.deepcopy(data)
    is_pinned = pinned or (lambda _s: False)

    # 1. a top-level project repeating a role's nested project
    dup = _duplicate_projects(current)
    if dup:
        projects = current.get("projects") or []
        names = tuple(str(projects[i].get("name") or "") for i in dup)
        current["projects"] = [p for i, p in enumerate(projects) if i not in set(dup)]
        yield LadderStep(copy.deepcopy(current), LadderCut(DUPLICATE_PROJECT, names))

    # 2. skill chips with no relevance to this job
    if skill_tier is not None:
        skills = [s for s in current.get("skills") or [] if isinstance(s, str)]
        off = [s for s in skills if skill_tier(s) >= 3 and not is_pinned(s)]
        if off:
            current["skills"] = [s for s in skills if s not in set(off)]
            yield LadderStep(copy.deepcopy(current), LadderCut(OFF_JOB_SKILLS, tuple(off)))

    # 3. a repeated industry ("Branche:") line
    rep = _repeated_industry(current)
    if rep:
        work = current.get("work_history") or []
        removed = tuple(
            f"{work[i].get('company') or work[i].get('role') or ''}: {work[i].get('industry_context')}"
            for i in rep
        )
        for i in rep:
            work[i]["industry_context"] = None
        yield LadderStep(copy.deepcopy(current), LadderCut(REPEATED_INDUSTRY, removed))

    # 4. the oldest certification, one at a time
    while True:
        certs = current.get("certifications") or []
        candidates = [
            (i, c) for i, c in enumerate(certs) if not is_pinned(str(c.get("name") or ""))
        ]
        if not candidates:
            break
        i, oldest = min(
            candidates, key=lambda ic: (_year_key(ic[1].get("date_obtained")), -ic[0])
        )
        current["certifications"] = [c for j, c in enumerate(certs) if j != i]
        yield LadderStep(
            copy.deepcopy(current),
            LadderCut(OLDEST_CERTIFICATION, (str(oldest.get("name") or ""),)),
        )

    # 5. the oldest education entry beyond the two most recent, one at a time
    while len(current.get("education") or []) > _KEEP_EDUCATION:
        edu = current["education"]
        i, oldest = min(
            enumerate(edu),
            key=lambda ie: (_year_key(ie[1].get("end_date"), ie[1].get("start_date")), -ie[0]),
        )
        current["education"] = [e for j, e in enumerate(edu) if j != i]
        label = " — ".join(x for x in (oldest.get("degree"), oldest.get("institution")) if x)
        yield LadderStep(copy.deepcopy(current), LadderCut(OLDEST_EDUCATION, (label,)))


def merge_cuts(cuts: list[LadderCut]) -> list[dict[str, Any]]:
    """Fold consecutive cuts of one kind into one entry (the two certification
    steps become one "oldest_certification: [A, B]") for reporting."""
    out: list[dict[str, Any]] = []
    for c in cuts:
        if out and out[-1]["kind"] == c.kind:
            out[-1]["items"].extend(c.items)
        else:
            out.append(c.as_dict())
    return out
