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
5. the oldest education entry beyond the two most recent, one at a time,
6. (the person's page-length proposal only — ``old_roles_before``) the bullets and
   nested projects of a role that ended more than ``OLD_ROLE_YEARS`` years ago, oldest
   first, one role at a time: the role keeps its title, company and dates in THIS CV;
   the profile is untouched (U-6, founder ruling 2026-10-28: "in case of us needing
   to reach the limit, shorten the old jobs").

Rungs 1–5 never touch a role's bullets; rung 6 runs only where the person decides
("Kürzen" on the page card), never in the automatic ``target + 1`` ladder. Nothing
touches a summary, a JD-relevant skill or a pinned fact's carrier (a pinned bullet
of an old role stays), and every cut is named so the length finding can say
exactly what went.

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
OLD_ROLE_BULLETS = "old_role_bullets"

#: U-6: a role that ended MORE than this many years ago is "old" (rung 6).
OLD_ROLE_YEARS = 15

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
    old_roles_before: int | None = None,
    pinned_bullets: Callable[[dict, list[str]], set[int]] | None = None,
) -> Iterator[LadderStep]:
    """Yield the ladder's steps in order, each applied on top of the previous one.

    ``skill_tier`` is the #192 rank (``services.cv.skill_jd_tier_fn``); without it
    the skills rung is skipped (no rank, no cut). ``pinned`` names text a fact pin
    protects — a protected skill or certification is never cut.

    ``old_roles_before`` arms rung 6: a role whose end year is BEFORE it (the caller
    passes ``this year - OLD_ROLE_YEARS``) loses its bullets and nested projects.
    ``pinned_bullets(entry, texts)`` returns the indexes of ``texts`` a CV pin
    protects (``pin_reach.bullet_pin_carrier_indices``) — those stay.
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

    # 6. an old role's bullets and nested projects, oldest first (proposal only)
    if old_roles_before is None:
        return
    pin_idx = pinned_bullets or (lambda _e, _t: set())
    done: set[int] = set()
    while True:
        work = current.get("work_history") or []
        old = [
            (i, w) for i, w in enumerate(work)
            if i not in done and isinstance(w, dict) and not w.get("is_current")
            and 0 < _year_key(w.get("end_date")) < old_roles_before
            and (w.get("bullets") or w.get("projects"))
        ]
        if not old:
            return
        i, role = min(old, key=lambda iw: (_year_key(iw[1].get("end_date")), -iw[0]))
        done.add(i)
        bullets = [b for b in role.get("bullets") or [] if isinstance(b, str)]
        keep_b = pin_idx(role, bullets)
        kept_projects = [
            p for p in role.get("projects") or []
            if isinstance(p, dict) and pin_idx(role, [b for b in p.get("bullets") or [] if isinstance(b, str)])
        ]
        new_bullets = [b for j, b in enumerate(bullets) if j in keep_b]
        if len(new_bullets) == len(bullets) and len(kept_projects) == len(role.get("projects") or []):
            continue  # everything on it is pinned: nothing to cut
        role["bullets"] = new_bullets
        role["projects"] = kept_projects
        label = " — ".join(
            x for x in (role.get("role"), role.get("company"), str(role.get("end_date") or "")) if x
        )
        yield LadderStep(copy.deepcopy(current), LadderCut(OLD_ROLE_BULLETS, (label,)))


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


# ---------------------------------------------------------------------------
# Undo after a later edit (MD3-3, Hazelnut build 3 fix round, adversarial A-3)
# ---------------------------------------------------------------------------


def _edu_key(e: dict) -> str:
    return f"{_norm(e.get('degree'))}|{_norm(e.get('institution'))}"


def _merge_back(full: list, current: list, cut: list, key: Callable[[Any], str]) -> tuple[list, list]:
    """Re-insert into ``current`` the elements of ``full`` the cut removed (in ``full``,
    not in ``cut``), each right after the nearest element that precedes it in ``full``
    and is still in ``current`` (at the front when none is). An element ``current``
    already holds (the person re-added it) is not inserted twice. Returns the merged
    list and the keys it re-inserted, in ``full`` order."""
    cut_keys = {key(x) for x in cut}
    out = list(current)
    restored: list = []
    for idx, item in enumerate(full):
        k = key(item)
        if k in cut_keys or any(key(x) == k for x in out):
            continue
        pos = 0
        for prev in reversed(full[:idx]):
            hits = [i for i, x in enumerate(out) if key(x) == key(prev)]
            if hits:
                pos = hits[-1] + 1
                break
        out.insert(pos, copy.deepcopy(item))
        restored.append(k)
    return out, restored


def _role_key(w: dict) -> str:
    return f"{_norm(w.get('company'))}|{_norm(w.get('role'))}"


def reinsert_cuts(
    current: dict,
    full: dict,
    cut: dict,
    *,
    skills_override: list[str] | None = None,
) -> tuple[dict, list[str] | None, list[str]]:
    """Put what the page ladder's cut removed back into the CURRENT CV (MD3-3).

    ``full`` is the data before the cut and ``cut`` the data the cut produced; the
    difference between them is exactly what "Kürzen" removed — the ladder's six
    kinds: top-level projects, skill chips, a role's industry line, certifications,
    education entries, an old role's bullets and nested projects (U-6). Everything else in ``current`` (a later edit) is kept as it
    is. Each list item goes back at its original position relative to its
    neighbours; an industry line goes back to its role when that role is still
    there and carries none. ``skills_override`` is the section editor's skills list
    when the person edited the skills section — the chips then go back into THAT
    list (the render reads the override, not ``tailored_data["skills"]``).

    Returns ``(data, skills_override, unrestorable)``: the re-inserted data, the
    updated override (None when none was given) and the items that could not go
    back (a role that no longer exists). Pure — a fact operation (ADR-062 cl. 1).
    """
    data = copy.deepcopy(current)
    unrestorable: list[str] = []

    projects, _ = _merge_back(
        full.get("projects") or [], data.get("projects") or [], cut.get("projects") or [],
        lambda p: _norm(p.get("name")),
    )
    if projects or "projects" in data:
        data["projects"] = projects

    full_skills = [s for s in full.get("skills") or [] if isinstance(s, str)]
    cut_skills = [s for s in cut.get("skills") or [] if isinstance(s, str)]
    if skills_override is not None:
        skills_override, _ = _merge_back(full_skills, list(skills_override), cut_skills, _norm)
    else:
        data["skills"], _ = _merge_back(full_skills, list(data.get("skills") or []), cut_skills, _norm)

    for section, key in (("certifications", lambda c: _norm(c.get("name"))), ("education", _edu_key)):
        merged, _ = _merge_back(
            full.get(section) or [], data.get(section) or [], cut.get(section) or [], key,
        )
        if merged or section in data:
            data[section] = merged

    cut_roles = cut.get("work_history") or []
    # U-6 rung 6: an old role's bullets/projects go back to that role when it is
    # still there and the person has not written new ones into it since.
    for i, w in enumerate(full.get("work_history") or []):
        if i >= len(cut_roles) or _role_key(cut_roles[i]) != _role_key(w):
            continue
        lost_b = [b for b in w.get("bullets") or [] if b not in (cut_roles[i].get("bullets") or [])]
        lost_p = [
            p for p in w.get("projects") or []
            if _norm(p.get("name")) not in {_norm(q.get("name")) for q in cut_roles[i].get("projects") or []}
        ]
        if not lost_b and not lost_p:
            continue
        target = next(
            (r for r in data.get("work_history") or [] if _role_key(r) == _role_key(w)), None,
        )
        if target is None:
            unrestorable.append(f"{w.get('company') or w.get('role') or ''}: {len(lost_b)} bullets")
            continue
        if (target.get("bullets") or []) == (cut_roles[i].get("bullets") or []):
            target["bullets"] = copy.deepcopy(w.get("bullets") or [])
        if [_norm(p.get("name")) for p in target.get("projects") or []] == [
            _norm(p.get("name")) for p in cut_roles[i].get("projects") or []
        ]:
            target["projects"] = copy.deepcopy(w.get("projects") or [])
    for i, w in enumerate(full.get("work_history") or []):
        line = w.get("industry_context")
        was_cut = (
            line and i < len(cut_roles) and not cut_roles[i].get("industry_context")
            and _role_key(cut_roles[i]) == _role_key(w)
        )
        if not was_cut:
            continue
        target = next(
            (r for r in data.get("work_history") or [] if _role_key(r) == _role_key(w)), None,
        )
        if target is None:
            unrestorable.append(f"{w.get('company') or w.get('role') or ''}: {line}")
        elif not target.get("industry_context"):
            target["industry_context"] = line
    return data, skills_override, unrestorable
