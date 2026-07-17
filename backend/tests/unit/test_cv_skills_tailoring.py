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

"""#192 — the tailored CV skills section must be a prioritised, JD-relevant subset of
the master profile, not a wholesale dump. Covers services.cv._tailor_skills_to_jd."""

from applire.constants import CV_MAX_SKILLS
from applire.schemas.cv import TailoredCVData, TailoredContact
from applire.services.cv import _tailor_skills_to_jd

# A CTO / AI-first SaaS role. The candidate came from regulated pharma, so the master
# profile mixes genuinely relevant tech skills with clearly-irrelevant GxP tags.
_PHARMA_TAGS = [
    "CFR Part11",
    "ALCOA+ principles",
    "CAPA management",
    "MES systems",
    "Chain of Custody (COC)",
    "IMP-Management",
    "Patient operations",
    "Deviation investigation",
    "GMP documentation",
    "Batch record review",
]

_TECH_TAGS = [
    "React",
    "Node.js",
    "JavaScript",
    "TypeScript",
    "Python",
    "AWS",
    "Team Leadership",
    "System Architecture",
]

_PROFILE = {"skills": _TECH_TAGS + _PHARMA_TAGS + [f"Filler skill {i}" for i in range(40)]}

_JOB = {
    "role_title": "Chief Technology Officer",
    "required_skills": ["React", "Node.js", "JavaScript", "TypeScript", "Team Leadership"],
    "nice_to_have_skills": ["AWS", "System Architecture"],
    "keywords": ["Python"],
}


def _tailored(skills: list[str]) -> TailoredCVData:
    return TailoredCVData(contact=TailoredContact(name="Test Candidate"), skills=skills)


def test_required_profile_skills_are_guaranteed_even_when_writer_dropped_them():
    """Defect #2: React / Node.js / JavaScript are JD-required AND in the profile, yet
    the writer dumped only the pharma tags. They must be re-added from the profile."""
    # The writer kept the whole pharma block but dropped the required tech skills.
    writer_output = _tailored(list(_PHARMA_TAGS))

    result = _tailor_skills_to_jd(writer_output, _PROFILE, _JOB, keyword_ledger=[])

    for required in ("React", "Node.js", "JavaScript", "TypeScript", "Team Leadership"):
        assert required in result.skills, f"{required!r} missing from tailored skills"


def test_irrelevant_tags_dropped_when_over_cap():
    """Defect #1: when the cap forces a choice, no-relevance pharma tags lose to the
    JD-relevant tech tags. All 8 tech tags map to the JD; the cap here == 8, so every
    tier-3 pharma tag must be squeezed out."""
    # Writer dumped the whole master profile (~58 tags, well over the cap).
    writer_output = _tailored(list(_PROFILE["skills"]))

    result = _tailor_skills_to_jd(
        writer_output, _PROFILE, _JOB, keyword_ledger=[], cap=len(_TECH_TAGS)
    )

    for irrelevant in _PHARMA_TAGS:
        assert irrelevant not in result.skills, f"{irrelevant!r} should have been dropped"
    # Every relevant tech tag survived the squeeze.
    for tech in _TECH_TAGS:
        assert tech in result.skills, f"{tech!r} should have been kept"


def test_count_is_bounded_by_cap():
    """The section must be a sensible-length subset, not the ~70-tag master dump."""
    writer_output = _tailored(list(_PROFILE["skills"]))  # the whole profile

    result = _tailor_skills_to_jd(writer_output, _PROFILE, _JOB, keyword_ledger=[])

    assert len(result.skills) <= CV_MAX_SKILLS


def test_no_fabrication_only_profile_skills():
    """Selection may only ever draw from skills already in the master profile."""
    writer_output = _tailored(_TECH_TAGS + _PHARMA_TAGS)

    result = _tailor_skills_to_jd(writer_output, _PROFILE, _JOB, keyword_ledger=[])

    allowed = set(_PROFILE["skills"]) | set(_TECH_TAGS + _PHARMA_TAGS)
    for skill in result.skills:
        assert skill in allowed, f"{skill!r} was fabricated"


def test_keyword_ledger_drives_relevance_when_job_fields_absent():
    """The Keyword Ledger (ADR-048) alone is enough to guarantee a required skill."""
    ledger = [
        {
            "concept": "React",
            "surface_forms": ["React.js", "ReactJS"],
            "sources": ["required"],
            "claimable": True,
        }
    ]
    job_no_fields = {"role_title": "CTO"}
    writer_output = _tailored(list(_PHARMA_TAGS))  # dropped React

    result = _tailor_skills_to_jd(writer_output, _PROFILE, job_no_fields, keyword_ledger=ledger)

    assert "React" in result.skills
