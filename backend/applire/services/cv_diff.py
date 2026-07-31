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
US147 / ADR-040 — deterministic pre-download diff between a generated CV
(`generated_cvs.tailored_data`) and the live Master Profile.

**Employer/date detection retired, deliberately (ADR-067 clause 9 / E049
49.7).** Since ADR-067's vault join, employer names, roles and dates are
transcription: `assemble_tailored_cv` copies them from the vault keyed by
`WorkEntry.id`, the writer's response schema cannot even express them, and an
unknown id fails closed. The JF-M-6.1 control this module used to *detect*
with is now **structurally impossible to violate**, which is the stronger
control — detection code kept alive against an impossible failure mode would
be a control that can never fire (the SF-WRITE.17 shape). The pre-download
surface now STATES the guarantee instead of re-checking it; see
`WhatChangedReview` on the frontend.

What remains is the **skills half**: skill tags are the one surface where the
writer's prose (translated/relabelled tags, ADR-067 clause 3) can drift from
what the vault evidences. A tag is flagged only when it is grounded NEITHER in
`skills[].name` NOR in the vault's own literal narrative text
(`profile_literal_corpus` + the shared `surface_present` predicate) — #395:
a narrative-backed skill (`Kostenrechnung` evidenced in a work achievement but
absent from the skills list) is grounded, and flagging it read as an
accusation on truthful content.

Reads only the two persisted artifacts — never the source upload
(retention-safe, ADR-005).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.profile import MasterProfile
from applire.schemas.cv import CVProfileDiffResponse
from applire.schemas.profile import FieldChange
from applire.services.ats_audit import surface_present
from applire.services.keyword_ledger import profile_literal_corpus


def compute_cv_profile_diff(tailored: dict, profile: dict) -> list[FieldChange]:
    """Return the structured divergences of the tailored CV from the Master
    Profile — skills only, since ADR-067 made every other fact vault-joined."""
    changes: list[FieldChange] = []

    prof_skills: set[str] = set()
    for s in profile.get("skills", []) or []:
        name = s.get("name") if isinstance(s, dict) else s
        if name:
            prof_skills.add(str(name).strip().lower())
    # #395: the vault's own narrative text also grounds a tag — a skill
    # evidenced in an achievement/summary is not "not listed", and the
    # pre-download notice must never accuse truthful content. Same corpus +
    # predicate the ATS/Oracle consistency guards use (one shared instrument).
    vault_text_norm = profile_literal_corpus(profile)

    for sk in tailored.get("skills", []) or []:
        if not sk:
            continue
        if str(sk).strip().lower() in prof_skills:
            continue
        if vault_text_norm and surface_present(str(sk), vault_text_norm):
            continue
        changes.append(FieldChange(
            section="skills", field="skills", action="added",
            new_value=sk,
            rationale="Not listed in your Master Profile — confirm you have this skill.",
        ))

    return changes


async def get_cv_profile_diff(cv_id, db: AsyncSession) -> CVProfileDiffResponse:
    """Load a generated CV and its Master Profile and return their deterministic diff.

    Reads only the persisted `tailored_data` and `profile_json` — never the source
    upload (retention-safe, ADR-005). Raises ValueError if the CV is unknown.
    """
    cv = await db.get(GeneratedCV, cv_id)
    if cv is None:
        raise ValueError("CV not found")
    profile = await db.get(MasterProfile, cv.profile_id)
    if profile is None:
        return CVProfileDiffResponse(items=[], grounded=True)
    items = compute_cv_profile_diff(cv.tailored_data or {}, profile.profile_json or {})
    return CVProfileDiffResponse(items=items, grounded=len(items) == 0)
