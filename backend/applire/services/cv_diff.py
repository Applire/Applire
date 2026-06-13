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

This is the *detection* half of the JF-M-6.1 control. It runs with no LLM and no
network, comparing only discrete, verifiable facts where a deterministic check is
trustworthy: fabricated employers, mutated dates, changed/inflated titles, and
ungrounded skills. Semantic bullet grounding is deliberately NOT attempted here —
that is the ADR-021 LLM reviewer's job (prevention); a substring check would raise
false positives on legitimately rephrased bullets.

Reads only the two persisted artifacts — never the source upload (retention-safe,
ADR-005).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.profile import MasterProfile
from applire.schemas.cv import CVProfileDiffResponse
from applire.schemas.profile import FieldChange
from applire.services.profile.merge import company_names_match


def _norm_month(value: str | None) -> str | None:
    if not value:
        return None
    return (str(value) + "-01")[:7]


def compute_cv_profile_diff(tailored: dict, profile: dict) -> list[FieldChange]:
    """Return the structured divergences of the tailored CV from the Master Profile."""
    changes: list[FieldChange] = []
    prof_wx: list[dict] = profile.get("work_experience", []) or []

    for cv_e in tailored.get("work_history", []) or []:
        company = (cv_e.get("company") or "").strip()
        if not company:
            continue
        match = next(
            (p for p in prof_wx if company_names_match(p.get("company", ""), company)),
            None,
        )
        if match is None:
            role = (cv_e.get("role") or "").strip()
            changes.append(FieldChange(
                section="work_experience", field="company", action="added",
                new_value=f"{role} @ {company}".strip(" @"),
                rationale=f"“{company}” isn't in your Master Profile — make sure this position is real.",
            ))
            continue

        # Title — flag when the CV title is neither the stored role nor a known alias.
        prof_titles = {(match.get("role") or "").strip().lower()}
        prof_titles |= {(a or "").strip().lower() for a in (match.get("role_aliases") or [])}
        cv_role = (cv_e.get("role") or "").strip()
        if cv_role and cv_role.lower() not in prof_titles:
            changes.append(FieldChange(
                section="work_experience", field="role", action="updated",
                old_value=match.get("role"), new_value=cv_role,
                rationale=f"Title for {company} differs from your profile — check it isn't overstated.",
            ))

        # Dates — flag a clear month-level difference.
        for fld, label in (("start_date", "Start date"), ("end_date", "End date")):
            cv_d = _norm_month(cv_e.get(fld))
            prof_d = _norm_month(match.get(fld))
            if cv_d and prof_d and cv_d != prof_d:
                changes.append(FieldChange(
                    section="work_experience", field=fld, action="updated",
                    old_value=match.get(fld), new_value=cv_e.get(fld),
                    rationale=f"{label} for {company} differs from your Master Profile.",
                ))

    # Skills — discrete tokens; flag any CV skill not present in the profile.
    prof_skills: set[str] = set()
    for s in profile.get("skills", []) or []:
        name = s.get("name") if isinstance(s, dict) else s
        if name:
            prof_skills.add(str(name).strip().lower())
    for sk in tailored.get("skills", []) or []:
        if sk and str(sk).strip().lower() not in prof_skills:
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
