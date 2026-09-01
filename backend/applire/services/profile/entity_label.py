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

"""#604 — the one place that turns a ``Conflict.entity_id`` into a human label.

#626 gave the Health hub's conflict card an entry name ("Senior Engineer @ Acme
— End date") instead of the raw ``work_experience.end_date: 'x' vs 'y'``. It
lived in ``health.py``, so it reached exactly one of the two surfaces that show
a conflict: the live enrichment interview's own ``ConflictCard`` kept the raw
shape, because it is fed by a different mechanism (``schemas.session``'s
``ConflictSummary``, built in ``reconcile/interview_bridge.py``).

Extracted here so both mechanisms resolve the entity the same way. Adding a
third conflict surface means importing this module — not re-deriving the
ladder, which is how the two drifted apart in the first place.
"""

from __future__ import annotations

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    ExperienceBase,
    Language,
    MasterProfileData,
    Publication,
    SignatureStory,
    Skill,
)
from applire.services.profile.completeness import _entry_label


def resolve_entity(profile: MasterProfileData | None, entity_id: str | None) -> object | None:
    """The id-bearing profile entity ``entity_id`` names, or ``None`` (#626).

    Searches every section :func:`applire.services.profile.reconcile.apply.
    resolve_any` can target — work/project/volunteer plus the six sections
    #619 added it for — rather than trusting a ``Conflict.section`` string
    (defensive: cheap, and correct the day ``_apply_flag_conflict`` widens
    from its current experience-only ``resolve()`` to ``resolve_any``, the way
    ``_apply_set_field`` already did).

    ``None`` covers two legitimate cases the caller must not crash on: a
    profile-level conflict (``entity_id`` was never set — #218's own docstring:
    ``professional_summary`` / ``personal_info`` disputes have no entity) and a
    STALE id (the entity existed when the conflict was flagged but was since
    edited or removed — nothing sweeps ``metadata.pending_conflicts`` when its
    target entity disappears).

    ``profile`` is nullable for the same reason: a caller with no conflicts to
    label never builds one (#604), and "no profile" can only mean "no label".
    """
    if profile is None or not entity_id:
        return None
    for entry in (
        *profile.work_experience,
        *profile.projects,
        *profile.volunteer_activities,
        *profile.education,
        *profile.certifications,
        *profile.languages,
        *profile.publications,
        *profile.skills,
        *profile.signature_stories,
    ):
        if getattr(entry, "id", None) == entity_id:
            return entry
    return None


def entity_label(entity: object | None) -> str | None:
    """Human label for a resolved id-bearing entity, or ``None`` (#626).

    Mirrors the isinstance ladder ``_section_for`` (reconcile/apply.py) uses
    for the reverse mapping (entity → section name). The "X @ Y" shape matches
    ``health._unit_issues`` (``completeness._entry_label``) exactly, so every
    surface speaks one convention for every entry label it shows — for the
    three ``ExperienceBase`` kinds via the polymorphic ``org_label()``
    (company / project name / organization), and by the equivalent "specific
    @ broader" pairing for the rest (degree @ institution, cert name @ issuing
    org). A single-value entity (language, publication title, skill name,
    story title) has no "@" counterpart and is shown bare.
    """
    if entity is None:
        return None
    if isinstance(entity, ExperienceBase):
        return _entry_label({"company": entity.org_label(), "role": entity.role})
    if isinstance(entity, EducationEntry):
        return _entry_label({"company": entity.institution, "role": entity.degree})
    if isinstance(entity, Certification):
        return _entry_label({"company": entity.issuing_organization, "role": entity.name})
    if isinstance(entity, Language):
        return entity.language
    if isinstance(entity, Publication):
        return entity.title
    if isinstance(entity, Skill):
        return entity.name
    if isinstance(entity, SignatureStory):
        return entity.title
    return None


def label_for(profile: MasterProfileData | None, entity_id: str | None) -> str | None:
    """The human label of the entity ``entity_id`` names, or ``None``.

    The convenience both conflict surfaces call. ``None`` is a legitimate,
    non-exceptional answer — see :func:`resolve_entity` for the two cases.
    """
    return entity_label(resolve_entity(profile, entity_id))
