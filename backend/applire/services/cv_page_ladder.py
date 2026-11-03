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
2. skill chips with no relevance to THIS job (the #192 rank, tier 3), lowest
   ranked first and only as many as the page needs — never below
   ``AUTO_SKILL_FLOOR`` chips in the automatic ladder (D-23),
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
from typing import Any, Callable, Collection, Iterator

# The kinds a cut can have — stable ids the frontend and the ATS finding key on.
DUPLICATE_PROJECT = "duplicate_project"
OFF_JOB_SKILLS = "off_job_skills"
#: Legacy kind (persisted proposals before founder ruling P-1): the ladder no
#: longer proposes it — the industry line is never rendered, so cutting it saved
#: no space and the receipt named a cut that changed nothing on the page.
REPEATED_INDUSTRY = "repeated_industry"
OLDEST_CERTIFICATION = "oldest_certification"
OLDEST_EDUCATION = "oldest_education"
OLD_ROLE_BULLETS = "old_role_bullets"

#: U-6: a role that ended MORE than this many years ago is "old" (rung 6).
OLD_ROLE_YEARS = 15

_KEEP_EDUCATION = 2

#: D-23: the automatic ladder never cuts the skills section below this many
#: chips (ADR-099: an automatic cut must not empty a section).
AUTO_SKILL_FLOOR = 8
#: D-23: the skills rung cuts in at most this many growing slices (one re-render
#: each), so it stops near the fewest chips the page needs.
_SKILL_CUT_STEPS = 4
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


def ladder_steps(
    data: dict,
    skill_tier: Callable[[str], int] | None = None,
    *,
    pinned: Callable[[str], bool] | None = None,
    old_roles_before: int | None = None,
    pinned_bullets: Callable[[dict, list[str]], set[int]] | None = None,
    skill_floor: int = 0,
) -> Iterator[LadderStep]:
    """Yield the ladder's steps in order, each applied on top of the previous one.

    ``skill_tier`` is the #192 rank (``services.cv.skill_jd_tier_fn``); without it
    the skills rung is skipped (no rank, no cut). ``pinned`` names text a fact pin
    protects — a protected skill or certification is never cut.

    ``old_roles_before`` arms rung 6: a role whose end year is BEFORE it (the caller
    passes ``this year - OLD_ROLE_YEARS``) loses its bullets and nested projects.
    ``pinned_bullets(entry, texts)`` returns the indexes of ``texts`` a CV pin
    protects (``pin_reach.bullet_pin_carrier_indices``) — those stay.

    ``skill_floor``: the skills rung never leaves fewer chips than this (the
    automatic ``target + 1`` ladder passes ``AUTO_SKILL_FLOOR``; the person's
    proposal passes 0 — the card names every chip before anything is cut).
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

    # 2. skill chips with no relevance to this job — D-23 (build 5 run 5): only as
    # many as the page needs, never below ``skill_floor`` chips. The chips go from
    # the END of the list (the #192 pass ranks them, lowest last) in at most
    # ``_SKILL_CUT_STEPS`` growing slices; the caller re-renders after each slice
    # and stops at the limit, so a 4-page render that one line of chips tips over
    # loses that line, not the section (run 5: 27 chips → 3 delivered).
    if skill_tier is not None:
        skills = [s for s in current.get("skills") or [] if isinstance(s, str)]
        off = [s for s in reversed(skills) if skill_tier(s) >= 3 and not is_pinned(s)]
        off = off[: max(0, len(skills) - max(0, skill_floor))]
        if off:
            size = -(-len(off) // _SKILL_CUT_STEPS)  # ceil
            done = 0
            while done < len(off):
                batch = off[done : done + size]
                done += len(batch)
                gone = set(batch)
                current["skills"] = [s for s in current.get("skills") or [] if s not in gone]
                yield LadderStep(copy.deepcopy(current), LadderCut(OFF_JOB_SKILLS, tuple(batch)))

    # (3. — the repeated industry line — retired by founder ruling P-1: the line
    # is never rendered, so the cut saved no space; REPEATED_INDUSTRY stays only
    # as a persisted legacy kind that reinsert_cuts can still undo.)

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
        i, _role = min(old, key=lambda iw: (_year_key(iw[1].get("end_date")), -iw[0]))
        done.add(i)
        current, cut = shorten_roles(current, [i], pinned_bullets=pinned_bullets)
        if not cut.items:
            continue  # everything on it is pinned: nothing to cut
        yield LadderStep(copy.deepcopy(current), cut)


def _strip_plan(role: dict, pinned_bullets: Callable[[dict, list[str]], set[int]] | None):
    """What the U-6 strip leaves on one role: ``(bullets, projects, removed)`` —
    ``removed`` counts the bullets that go (a removed project's bullets included);
    None when nothing on the role can go (empty, or all of it pinned)."""
    pin_idx = pinned_bullets or (lambda _e, _t: set())
    bullets = [b for b in role.get("bullets") or [] if isinstance(b, str)]
    keep_b = pin_idx(role, bullets)
    projects = [p for p in role.get("projects") or [] if isinstance(p, dict)]
    kept_projects = [
        p for p in projects
        if pin_idx(role, [b for b in p.get("bullets") or [] if isinstance(b, str)])
    ]
    new_bullets = [b for j, b in enumerate(bullets) if j in keep_b]
    if len(new_bullets) == len(bullets) and len(kept_projects) == len(role.get("projects") or []):
        return None
    removed = len(bullets) - len(new_bullets) + sum(
        len([b for b in p.get("bullets") or [] if isinstance(b, str)])
        for p in projects if p not in kept_projects
    )
    return new_bullets, kept_projects, removed


def _role_label(role: dict) -> str:
    return " — ".join(
        x for x in (role.get("role"), role.get("company"), str(role.get("end_date") or "")) if x
    )


def shorten_roles(
    data: dict,
    indices: Collection[int],
    *,
    pinned_bullets: Callable[[dict, list[str]], set[int]] | None = None,
) -> tuple[dict, LadderCut]:
    """The U-6 strip, shared by ladder rung 6 and the person's own pick (R-1): each
    ``work_history`` role at ``indices`` keeps its title, company and dates and loses
    its bullets and nested projects, except what a CV pin protects. Returns the new
    data (the input is untouched) and ONE ``old_role_bullets`` cut naming each role
    that actually lost something as ``role — company — end_date``; ``items`` is empty
    when everything on the chosen roles is pinned or already bare."""
    out = copy.deepcopy(data)
    work = out.get("work_history") or []
    labels: list[str] = []
    for i in sorted(set(indices)):
        if not (0 <= i < len(work)) or not isinstance(work[i], dict):
            continue
        plan = _strip_plan(work[i], pinned_bullets)
        if plan is None:
            continue
        work[i]["bullets"], work[i]["projects"] = plan[0], plan[1]
        labels.append(_role_label(work[i]))
    return out, LadderCut(OLD_ROLE_BULLETS, tuple(labels))


def shortenable_roles(
    data: dict,
    *,
    pinned_bullets: Callable[[dict, list[str]], set[int]] | None = None,
) -> list[dict[str, Any]]:
    """The roles the person may pick to shorten (R-1): non-current ``work_history``
    entries that still have bullets or projects and are not entirely pinned. Each
    entry: ``index`` (into ``work_history``), ``title``, ``company``, ``dates`` (as the
    CV renders them) and ``bullets`` (how many would be removed)."""
    from applire.templates.filters import period

    out: list[dict[str, Any]] = []
    for i, w in enumerate(data.get("work_history") or []):
        if not isinstance(w, dict) or w.get("is_current"):
            continue
        if not w.get("end_date") and w.get("is_current") is not False:
            continue  # no end date and not stated as ended: the CV renders it as current
        plan = _strip_plan(w, pinned_bullets)
        if plan is None or plan[2] == 0:
            continue
        p = period(w, "")
        dates = f"{p['start']} – {p['end']}" if p["dash"] else (p["start"] or p["end"])
        out.append({
            "index": i, "title": str(w.get("role") or ""), "company": str(w.get("company") or ""),
            "dates": dates, "bullets": plan[2],
        })
    return out


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
