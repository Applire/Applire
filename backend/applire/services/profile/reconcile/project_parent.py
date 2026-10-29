# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-8 (Hazelnut build 5 founder replay): the "which position does this project
belong to?" ask.

The reconciler raises it itself (prompt rule 6), in its own words. In the
replay the question was English in a German interface, and each option carried
a prompt handle, "System Engineer (id_w4)". ``ShortIdMap.restore`` maps only a
string that IS a handle, so the handle reached the screen. The answer was then
bookkeeping only: the project's ``associated_experience`` kept the company
string the first import had written, not the position's id.

Two halves, both deterministic. The model judged the question; the candidate
answers it.

- ``bind_project_parent_ask`` (engine, right after the reply is parsed):
  rebuild the ask as confirmation family 6 when every option names exactly one
  held position through a handle. The question is in both languages, each
  option is the position's own label, and the keys are ``entry:<id>``.
- ``plan_project_parent`` (applier, when the ask is answered): the project and
  the chosen position's id, so the answer is written to
  ``associated_experience``.
"""
from __future__ import annotations

import re
from typing import Any

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.ops import RequestConfirmation

KIND = "project_parent"
_HANDLE = re.compile(r"\bid_[a-z]\d+\b")
_PROJECT_KEYS = ("project", "project_name", "name")
_PARENT_SECTIONS = ("work_experience", "volunteer_activities")


def _project_name(context: dict[str, Any]) -> str | None:
    for key in _PROJECT_KEYS:
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _positions(profile: MasterProfileData) -> dict[str, Any]:
    return {
        str(e.id): e
        for section in _PARENT_SECTIONS
        for e in getattr(profile, section)
        if getattr(e, "id", None)
    }


def position_label(entry: Any) -> dict[str, str]:
    """``{"de": …, "en": …}`` — "Role · Employer (2018-10 – 2021-05)"; an open
    end reads "heute" / "present"; a missing part is left out."""
    org = getattr(entry, "company", None) or getattr(entry, "organization", None)
    head = " · ".join(p for p in (getattr(entry, "role", None), org) if p) or str(getattr(entry, "id", ""))
    start, end = getattr(entry, "start_date", None), getattr(entry, "end_date", None)
    if not start and not end:
        return {"de": head, "en": head}
    return {
        lang: f"{head} ({start or '?'} \u2013 {end or now})"
        for lang, now in (("de", "heute"), ("en", "present"))
    }


def _held_project(profile: MasterProfileData, name: str) -> Any | None:
    hits = [p for p in profile.projects if (p.name or "").strip().casefold() == name.casefold()]
    return hits[0] if len(hits) == 1 else None


def bind_project_parent_ask(
    rc: RequestConfirmation, id_map: Any, profile: MasterProfileData
) -> RequestConfirmation | None:
    """``rc`` rebuilt as family 6; ``None`` when the ask is redundant; or ``rc``
    unchanged when it is not a project-parent ask the code can bind with
    certainty.

    - Redundant (D-16, Hazelnut build 5 delivery run 3): the ask names a HELD
      project whose ``associated_experience`` already resolves to a held
      position. The answer could change nothing, so the ask is not raised.
    - Rebuilt: the section is ``projects`` or absent, the ask parks no op of
      its own (``incoming`` is the V-5 engagement family), the project is
      named, and every option carries exactly one handle that maps to a held
      position.
    """
    from applire.services.profile.reconcile.confirmations import project_parent_confirmation

    context = dict(rc.context or {})
    if context.get("section") not in (None, "projects") or "incoming" in context or rc.option_keys:
        return rc
    project = _project_name(context)
    held = _held_project(profile, project) if project else None
    if held is not None and str(held.associated_experience or "") in _positions(profile):
        return None
    if project is None or len(rc.options) < 2:
        return rc
    positions = _positions(profile)
    candidates: list[tuple[str, dict[str, str]]] = []
    for option in rc.options:
        handles = set(_HANDLE.findall(option or ""))
        if len(handles) != 1:
            return rc
        entry_id = id_map.long(handles.pop())
        entry = positions.get(str(entry_id)) if entry_id else None
        if entry is None:
            return rc
        candidates.append((str(entry.id), position_label(entry)))
    if len({c[0] for c in candidates}) != len(candidates):
        return rc
    context.update({"kind": KIND, "project": project})
    return project_parent_confirmation(project=project, candidates=candidates, context=context)


def parked_ask_is_redundant(context: dict[str, Any] | None, profile: MasterProfileData) -> bool:
    """D-16 for an ask parked before the engine rule existed: a model-asked or
    family-6 project-parent ask whose project is now attached to a held
    position. The hub does not show it."""
    context = context or {}
    if "incoming" in context or context.get("kind") not in (None, KIND):
        return False
    if context.get("section") not in (None, "projects"):
        return False
    project = _project_name(context)
    held = _held_project(profile, project) if project else None
    return held is not None and str(held.associated_experience or "") in _positions(profile)


def plan_project_parent(
    context: dict[str, Any], chosen_key: str | None, profile: MasterProfileData
) -> tuple[Any, str] | None:
    """``(project entry, position id)`` the answer writes, or ``None`` when the
    project or the position cannot be found (the caller receipts that)."""
    if not chosen_key or not chosen_key.startswith("entry:"):
        return None
    entry_id = chosen_key.split(":", 1)[1]
    if entry_id not in _positions(profile):
        return None
    held = _held_project(profile, _project_name(context) or "")
    if held is None:
        return None
    return held, entry_id
