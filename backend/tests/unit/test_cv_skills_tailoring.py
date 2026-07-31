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


def _obj_profile(names: list[str]) -> dict:
    """Master-profile skills as the DB actually stores them: objects, not bare strings
    (the production shape #192's first fix missed — it filtered for `str` and saw none)."""
    return {
        "skills": [
            {
                "name": n,
                "source": "llm_estimated",
                "category": "technical",
                "proficiency": "intermediate",
                "years_experience": None,
                "experience_refs": [],
            }
            for n in names
        ]
    }


def test_object_shaped_profile_skills_still_guarantee_required():
    """#192 follow-up: with skills stored as objects ({"name": ...}) — the real DB shape —
    the required∩profile guarantee must STILL re-add JD-required skills the writer dropped.
    Regression for the edge finding where React/Node.js/JavaScript vanished from a live CV
    because the guarantee only inspected string-typed profile skills."""
    profile = _obj_profile(_TECH_TAGS + _PHARMA_TAGS)
    writer_output = _tailored(list(_PHARMA_TAGS))  # writer kept pharma, dropped the tech skills

    result = _tailor_skills_to_jd(writer_output, profile, _JOB, keyword_ledger=[])

    for required in ("React", "Node.js", "JavaScript", "TypeScript", "Team Leadership"):
        assert required in result.skills, f"{required!r} missing (object-shaped profile)"


def test_object_shaped_profile_never_fabricates():
    """Object-shaped selection may still only draw from the profile's own skill names."""
    profile = _obj_profile(_TECH_TAGS + _PHARMA_TAGS)
    writer_output = _tailored(_TECH_TAGS + _PHARMA_TAGS)

    result = _tailor_skills_to_jd(writer_output, profile, _JOB, keyword_ledger=[])

    allowed = set(_TECH_TAGS + _PHARMA_TAGS)
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


# ── #308 (E049/US271, ADR-066/ADR-067) — shared-parenthetical-abbreviation shape ──
# Ground truth (captured LLM log): the vault holds 'MES (Manufacturing Execution
# System)'; for a German CV the writer correctly emitted exactly ONE skill,
# 'Fertigungsleitsysteme (MES)' -- the German translation, canonical abbreviation
# preserved. Because skills_near_dupe scored that pair as NOT a near-dupe (token
# Jaccard 1/5 = 0.2), the #192 guarantee step below re-added the vault's English
# spelling as though the writer had dropped a required skill, producing a
# duplicate 'MES' entry under two spellings.

_MES_PROFILE = {
    "skills": [
        {"name": "MES (Manufacturing Execution System)", "category": "technical"},
        {"name": "Python", "category": "technical"},
    ]
}

_MES_JOB = {"role_title": "Produktionsleiter", "required_skills": ["MES"]}


def test_tailor_skills_to_jd_does_not_re_add_english_spelling_of_translated_mes_skill():
    """#308: the writer's German 'Fertigungsleitsysteme (MES)' already satisfies the
    JD's MES requirement -- the guarantee step must recognise it as the SAME skill
    as the vault's 'MES (Manufacturing Execution System)' and must not duplicate it
    under the vault's English spelling."""
    writer_output = _tailored(["Fertigungsleitsysteme (MES)", "Python"])

    result = _tailor_skills_to_jd(writer_output, _MES_PROFILE, _MES_JOB, keyword_ledger=[])

    assert "Fertigungsleitsysteme (MES)" in result.skills
    assert "MES (Manufacturing Execution System)" not in result.skills


# ── #250 (Tiramisu founder-acceptance blind-panel finding, run 3, 2026-07-24) ──
# Both blind reviewers (HR + hiring manager) independently flagged near-verbatim
# JD phrases minted as bare skill tags ("Fast-Moving Product-Led Environment
# Experience", "Commercial AI Product Development", "AI Reliability", "AI
# Observability", "Production Ownership") as keyword-stuffing / inflation. The
# ledger never flags them (they're claimable concepts) -- the defect is
# PLACEMENT: a JD concept surfaced as a bare skill tag with no deterministic
# vault tie reads as inflation to a human, even when it's truthful.

_RUN3_REQUIRED = [
    "Fast-Moving Product-Led Environment",
    "Commercial AI Product Development",
    "AI Reliability",
    "AI Observability",
    "Production Ownership",
    "Python",
    "Team Leadership",
]

_RUN3_PROFILE = {
    "skills": [
        {"name": "Python", "category": "technical", "experience_refs": ["w1"]},
        {
            "name": "Team Leadership and Mentorship",
            "category": "soft",
            "experience_refs": ["w1"],
        },
        {"name": "AI Observability", "category": "technical", "experience_refs": ["w1"]},
    ]
}

_RUN3_JOB = {"role_title": "CTO", "required_skills": _RUN3_REQUIRED}


def _run3_writer_output() -> list[str]:
    """The run-3 shape: 4 bare JD-echo tags with no vault tie, plus 3 genuine
    vault-tied skills (one exact, one JD-reworded, one untouched)."""
    return [
        "Fast-Moving Product-Led Environment Experience",
        "Commercial AI Product Development",
        "AI Reliability",
        "Production Ownership",
        "Python",
        "Team Leadership",  # writer trimmed the vault's own "...and Mentorship"
        "AI Observability",
    ]


class TestDropUngroundedJdEchoSkills:
    def test_jd_echo_tags_with_no_vault_tie_are_dropped(self):
        """#250 regression: the 4 near-verbatim JD phrases the blind panel flagged
        as keyword-stuffing must not survive -- none of them near-dupes any real
        vault skill."""
        from applire.services.cv import _drop_ungrounded_jd_echo_skills

        writer_output = _tailored(_run3_writer_output())

        result = _drop_ungrounded_jd_echo_skills(
            writer_output, _RUN3_PROFILE, _RUN3_JOB, keyword_ledger=[]
        )

        for jd_echo in (
            "Fast-Moving Product-Led Environment Experience",
            "Commercial AI Product Development",
            "AI Reliability",
            "Production Ownership",
        ):
            assert jd_echo not in result.skills, f"{jd_echo!r} should have been dropped"

    def test_vault_tied_entries_survive_in_the_writer_own_wording(self):
        """Genuine vault skills are never dropped. E049/ADR-067 RETIRES the former
        rename-toward-vault-phrasing step (inverts the old
        test_vault_tied_entries_survive_with_vault_grounded_naming): the label is
        PROSE, owned by the writer in the output language; the vault tie only
        decides survival, never spelling. An exact vault name (Python, AI
        Observability) is kept verbatim as before; a writer rewording that
        near-dupes a vault skill toward JD phrasing ("Team Leadership" for the
        vault's "Team Leadership and Mentorship") now survives in the WRITER'S
        OWN WORDING instead of being rewritten back to the vault's phrasing —
        renaming resurfaced the vault's mixed-language label onto a
        single-language page (#386)."""
        from applire.services.cv import _drop_ungrounded_jd_echo_skills

        writer_output = _tailored(_run3_writer_output())

        result = _drop_ungrounded_jd_echo_skills(
            writer_output, _RUN3_PROFILE, _RUN3_JOB, keyword_ledger=[]
        )

        assert "Python" in result.skills
        assert "AI Observability" in result.skills
        assert "Team Leadership" in result.skills
        assert "Team Leadership and Mentorship" not in result.skills

    def test_claimable_concept_with_genuine_vault_tie_is_kept(self):
        """A ledger-claimable concept present ONLY as a skill tag but WITH a real
        vault-skill tie (name + experience_refs) must NOT be over-dropped just
        because it also happens to match the JD's own phrasing."""
        from applire.services.cv import _drop_ungrounded_jd_echo_skills

        ledger = [
            {
                "concept": "AI Observability",
                "surface_forms": ["AI Observability"],
                "sources": ["required"],
                "claimable": True,
            }
        ]
        writer_output = _tailored(["AI Observability"])

        result = _drop_ungrounded_jd_echo_skills(
            writer_output, _RUN3_PROFILE, {"role_title": "CTO"}, keyword_ledger=ledger
        )

        assert "AI Observability" in result.skills

    def test_no_op_when_all_skills_are_vault_tied_and_not_jd_reworded(self):
        """Pure function, no-op guard: a skills list untouched by the pass returns
        the SAME object (mirrors the no-op contract of the sibling passes)."""
        from applire.services.cv import _drop_ungrounded_jd_echo_skills

        writer_output = _tailored(["Python", "AI Observability"])

        result = _drop_ungrounded_jd_echo_skills(
            writer_output, _RUN3_PROFILE, {"role_title": "CTO"}, keyword_ledger=[]
        )

        assert result is writer_output


class TestJdEchoCoverageInteraction:
    """#250: the deterministic drop must not silently hide a concept that's
    genuinely absent, nor falsely amber a concept that's genuinely present
    elsewhere in the document (US213/#122's shared presence predicate)."""

    def _doc(self, *, skills, extra_bullet=None) -> dict:
        work_history = [
            {
                "id": "w1",
                "company": "Acme",
                "role": "CTO",
                "start_date": "2020-01",
                "bullets": [extra_bullet] if extra_bullet else [],
            }
        ]
        return {
            "contact": {"name": "Max"},
            "summary": "Engineering leader.",
            "work_history": work_history,
            "skills": skills,
        }

    def test_concept_covered_in_bullets_stays_covered_after_drop(self):
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _drop_ungrounded_jd_echo_skills
        from applire.services.keyword_ledger import verified_missing_claimable

        ledger = [
            {
                "concept": "Commercial AI Product Development",
                "surface_forms": ["Commercial AI Product Development"],
                "sources": ["required"],
                "claimable": True,
            }
        ]
        tailored = TailoredCVData.model_validate(
            self._doc(
                skills=["Commercial AI Product Development", "Python"],
                extra_bullet="Led commercial AI product development for the platform.",
            )
        )

        result = _drop_ungrounded_jd_echo_skills(
            tailored, _RUN3_PROFILE, {"role_title": "CTO"}, keyword_ledger=ledger
        )

        assert "Commercial AI Product Development" not in result.skills
        missing = verified_missing_claimable(result.model_dump(mode="json"), ledger)
        assert missing == [], "concept is present in the bullet -- must not amber"

    def test_concept_covered_only_by_dropped_tag_reappears_as_missing(self):
        from applire.schemas.cv import TailoredCVData
        from applire.services.cv import _drop_ungrounded_jd_echo_skills
        from applire.services.keyword_ledger import verified_missing_claimable

        ledger = [
            {
                "concept": "Production Ownership",
                "surface_forms": ["Production Ownership"],
                "sources": ["required"],
                "claimable": True,
            }
        ]
        tailored = TailoredCVData.model_validate(
            self._doc(skills=["Production Ownership", "Python"])
        )

        # Before the drop, the bare tag satisfies the coverage scan (the exact
        # trivial-satisfaction path #250 is about).
        before = verified_missing_claimable(tailored.model_dump(mode="json"), ledger)
        assert before == []

        result = _drop_ungrounded_jd_echo_skills(
            tailored, _RUN3_PROFILE, {"role_title": "CTO"}, keyword_ledger=ledger
        )

        assert "Production Ownership" not in result.skills
        after = verified_missing_claimable(result.model_dump(mode="json"), ledger)
        assert len(after) == 1 and after[0]["concept"] == "Production Ownership"
