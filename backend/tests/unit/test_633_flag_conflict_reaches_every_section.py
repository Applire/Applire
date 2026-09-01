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

"""#633 — ``flag_conflict`` was blind on six of the nine id-bearing sections.

``apply_ops``' ``resolve()`` closure (the lookup ``_apply_flag_conflict`` used
for ``FlagConflict.target``) searched only ``work_experience`` / ``projects`` /
``volunteer_activities``. A ``flag_conflict`` naming an education /
certification / language / publication / skill / signature_story id therefore
resolved its ``target`` to ``None``.

**Ground-truthed here, correcting the work order's own repro line.** The work
order's ``flag_conflict(target=<education_id>, field="end_date",
value="2006")`` example uses ``value=``, which is not a real ``FlagConflict``
field (the schema carries ``existing``/``incoming``, not ``value``) — passed
through pydantic it is silently dropped as an unknown kwarg, leaving
``existing=None``, which trips ``_apply_flag_conflict``'s OWN absence guard
before ``resolve()`` is ever called, producing ``conflicts=0`` for a reason
that has nothing to do with this defect (and would still be 0 after the fix
below). Probed instead with the realistic shape a model actually emits —
BOTH ``existing`` and ``incoming`` populated and differing — the pre-fix
behaviour was NOT "nothing recorded": ``_apply_flag_conflict`` has no
``entity is None: return`` guard (unlike its ``_apply_set_field`` sibling), so
it unconditionally appended a ``Conflict`` with ``section=""`` and
``entity_id=None``. That is a WORSE failure than a silent drop: the dispute
becomes visible (Health hub, ``ProfileReviewDrawer``) but unaddressable, and
resolving it — ``build_resolve_field_op`` builds a ``ResolveField`` with
``section=""``, and ``_apply_resolve_field``'s ``dumped.get("")`` matches
neither its dict nor its list branch — silently writes NOTHING while still
REMOVING the parked conflict, as if the user's answer had been applied. That
exact "visible dead end" is reproduced in ``test_prefix_ground_truth_*`` below
against a pinned copy of the original code, before the fix that follows it.

**The fix** (``apply.py``): the ``FlagConflict`` dispatch arm now resolves
through ``resolve_any`` — the SAME every-id-bearing-section closure #619 built
for ``set_field`` — instead of the experience-only ``resolve``. Nothing else
needed to change: ``Conflict.entity_id``/``.section`` (via ``_section_for``,
already extended to all nine kinds by #632), ``_open_conflict_for``,
``_apply_resolve_field``, ``_entry_for_conflict`` and
``services.profile.resolution.build_resolve_field_op`` were all ALREADY
section-agnostic — they operate on ``op.section``/``op.target`` generically,
never special-casing ``work_experience``. This file proves the full path,
``flag_conflict`` -> ``Conflict`` -> ``build_resolve_field_op`` ->
``ResolveField`` -> ``apply_ops`` -> the value actually written back, for
every one of the six previously-blind sections.
"""
from __future__ import annotations

import pytest

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    MasterProfileData,
    ProfileMetadata,
    Publication,
    Skill,
    SignatureStory,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import FlagConflict
from applire.services.profile.resolution import build_resolve_field_op

SOURCE = "cv_upload"

# (section, entity factory, field, existing value, incoming value)
_SIX_SECTIONS = [
    pytest.param(
        "education",
        lambda: EducationEntry(institution="Acme Institute", degree="BSc", end_date="2005"),
        "end_date", "2005", "2006",
        id="education",
    ),
    pytest.param(
        "certifications",
        lambda: Certification(name="AWS SAA", credential_id="OLD-1"),
        "credential_id", "OLD-1", "NEW-2",
        id="certifications",
    ),
    pytest.param(
        "languages",
        lambda: Language(language="French", level="B2"),
        "level", "B2", "C1",
        id="languages",
    ),
    pytest.param(
        "publications",
        lambda: Publication(title="A Paper", venue="ICSE"),
        "venue", "ICSE", "FSE",
        id="publications",
    ),
    pytest.param(
        "skills",
        lambda: Skill(name="Python", years_experience=3),
        "years_experience", 3, 5,
        id="skills",
    ),
    pytest.param(
        "signature_stories",
        lambda: SignatureStory(
            title="Launch", challenge="tight deadline", mechanism="parallelised rollout",
            outcome="shipped v1",
        ),
        "outcome", "shipped v1", "shipped v2",
        id="signature_stories",
    ),
]


# ── 1. The conflict is now addressable on all six sections ─────────────────────


@pytest.mark.parametrize("section,make_entity,field,existing,incoming", _SIX_SECTIONS)
def test_flag_conflict_carries_section_and_entity_id(
    section, make_entity, field, existing, incoming
):
    profile = MasterProfileData(**{section: [make_entity()]})
    entity_id = getattr(profile, section)[0].id

    ops = [FlagConflict(target=entity_id, field=field, existing=existing, incoming=incoming)]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.section == section
    assert conflict.entity_id == entity_id
    assert conflict.field == field
    assert conflict.existing_value == existing
    assert conflict.incoming_value == incoming
    # The applier never decides the dispute — the entity itself is untouched.
    assert getattr(getattr(result.profile, section)[0], field) == existing


def test_flag_conflict_still_carries_section_and_entity_id_on_work_experience():
    """The pre-existing three sections must keep working unchanged — resolve_any
    is a strict superset of resolve, never a behaviour change for the sections
    resolve() already covered."""
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [FlagConflict(target=work.id, field="company", existing="Acme", incoming="Acme Corp")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.conflicts[0].section == "work_experience"
    assert result.conflicts[0].entity_id == work.id


# ── 2. The full resolution path: conflict -> ResolveField -> value written ─────


@pytest.mark.parametrize("section,make_entity,field,existing,incoming", _SIX_SECTIONS)
def test_resolving_the_conflict_writes_the_chosen_value_back(
    section, make_entity, field, existing, incoming
):
    """Follows a #633 conflict all the way to a resolved value — the part of
    the brief that is more than a one-line fix: surfacing a conflict the user
    cannot then resolve would trade a silent loss for a visible dead end.
    Exercises the REAL doors: services.profile.resolution.build_resolve_field_op
    (the adapter POST /api/profile/conflicts/{id}/resolve calls) and
    apply_ops' _apply_resolve_field (the write)."""
    profile = MasterProfileData(**{section: [make_entity()]})
    entity_id = getattr(profile, section)[0].id

    flagged = apply_ops(
        profile,
        [FlagConflict(target=entity_id, field=field, existing=existing, incoming=incoming)],
        SOURCE,
    )
    merged = flagged.profile
    merged.metadata = ProfileMetadata()
    merged.metadata.pending_conflicts.extend(flagged.conflicts)
    conflict = merged.metadata.pending_conflicts[0]

    resolve_op = build_resolve_field_op(conflict, resolution="incoming")
    # The adapter carries the dispute's OWN section/target — never invented.
    assert resolve_op.section == section
    assert resolve_op.target == entity_id

    resolved = apply_ops(merged, [resolve_op], "manual_edit")

    assert getattr(getattr(resolved.profile, section)[0], field) == incoming
    assert resolved.profile.metadata.pending_conflicts == []
    assert len(resolved.changes) == 1
    assert resolved.changes[0].section == section
    assert resolved.changes[0].field == field
    assert resolved.changes[0].action == "updated"


def test_resolving_with_existing_keeps_the_stored_value():
    """The 'existing' resolution (candidate rejects the incoming CV's value)
    on a previously-blind section — not just 'incoming' above.

    NOTE on mutation-testing this test specifically: the profile-level
    OUTCOME of a correct "existing" resolution (end_date stays "2005") is
    observationally identical to a silent no-op (the pre-#633 unaddressable-
    conflict shape also leaves end_date at "2005"), so that assertion ALONE
    does not distinguish a working resolution from a broken one — confirmed
    by mutation-testing this file (see the work report): under the reverted
    fix this test alone still passed. The `conflict.section`/`.entity_id`
    assertions and the `changes` receipt below are what actually distinguish
    them — `_apply_resolve_field` only appends a `FieldChange` when it found
    a real entry to write to (`wrote=True`), never for an unaddressable
    (`section=""`) conflict.
    """
    profile = MasterProfileData(
        education=[EducationEntry(institution="Acme Institute", degree="BSc", end_date="2005")]
    )
    entity_id = profile.education[0].id
    flagged = apply_ops(
        profile,
        [FlagConflict(target=entity_id, field="end_date", existing="2005", incoming="2006")],
        SOURCE,
    )
    merged = flagged.profile
    merged.metadata = ProfileMetadata()
    merged.metadata.pending_conflicts.extend(flagged.conflicts)
    conflict = merged.metadata.pending_conflicts[0]
    assert conflict.section == "education"
    assert conflict.entity_id == entity_id

    resolve_op = build_resolve_field_op(conflict, resolution="existing")
    assert resolve_op.section == "education"
    resolved = apply_ops(merged, [resolve_op], "manual_edit")
    assert resolved.profile.education[0].end_date == "2005"
    assert resolved.profile.metadata.pending_conflicts == []
    # A real resolution still receipts — even a "keep existing" answer is an
    # act, not a no-op (mirrors _apply_resolve_field's `wrote` gate).
    assert len(resolved.changes) == 1
    assert resolved.changes[0].section == "education"


# ── 3. Ground truth for the PRE-FIX behaviour (characterisation, pinned) ───────
# Not a regression test for the current code — a permanent record of what the
# defect actually did, reproduced against a frozen copy of the pre-#633
# _apply_flag_conflict body (identical to the pre-fix apply.py — verified by
# diff against `git show HEAD:...apply.py` while writing this file) so this
# stays true even as the module keeps evolving. Uses the realistic
# existing/incoming shape (see module docstring for why the work order's own
# `value=` repro line does not exercise this path at all).


def test_prefix_ground_truth_conflict_was_recorded_but_unaddressable():
    """Pins the ACTUAL pre-fix shape: not `conflicts == []`, but a conflict
    with `section=""` / `entity_id=None` — a dispute the resolution endpoint
    can never write back to. Runs the pre-fix `_apply_flag_conflict` body
    (frozen above) against `resolve` (experience-only) directly, rather than
    reconstructing all of apply_ops — the two-line change under test is
    entirely which closure gets passed in, and that is exactly what this
    isolates.
    """
    from applire.schemas.profile import Conflict
    from applire.services.profile.reconcile.apply import _is_empty, _norm, _section_for

    def pre_fix_apply_flag_conflict(op, resolve, source, conflicts):
        if _is_empty(op.existing) or _is_empty(op.incoming):
            return
        if _norm(op.existing) == _norm(op.incoming):
            return
        entity = resolve(op.target)
        section = _section_for(entity) if entity is not None else ""
        conflicts.append(
            Conflict(
                section=section,
                field=op.field,
                entity_id=getattr(entity, "id", None),
                existing_value=op.existing,
                incoming_value=op.incoming,
                source=source,
            )
        )

    def experience_only_resolve(handle):
        return None  # an education id is never in work/project/volunteer

    conflicts: list = []
    op = FlagConflict(target="some-education-id", field="end_date", existing="2005", incoming="2006")
    pre_fix_apply_flag_conflict(op, experience_only_resolve, SOURCE, conflicts)

    assert len(conflicts) == 1, "the pre-fix code recorded a conflict — just an unaddressable one"
    assert conflicts[0].section == ""
    assert conflicts[0].entity_id is None
