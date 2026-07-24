# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial-pass residual (2026-07-23) — skill-union grounding fallback.

A truthful multi-skill enumeration clause ("My experience includes designing
and implementing RESTful APIs with Python, FastAPI") spans several
independent vault skill units — no SINGLE unit clears
``GROUNDED_MIN_COVERAGE``, so the pre-existing single-unit grounding path
leaves it unverifiable even though every named skill is individually
attested. ``ground_via_skill_union`` computes coverage over the UNION of
role-agnostic vault ``skills[]`` units instead.

Only skill units may aggregate this way (never work-experience narrative
units — that would let a cross-role blend slip past the #196 attribution
matcher), and the attribution red flag must run BEFORE the union fallback is
ever attempted — a misattribution verdict must never be rescued by it. See
``services/oracle/audit.py::verify_claim`` §3b for the wiring.
"""
from __future__ import annotations

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import verify_claim
from applire.services.oracle.matchers import build_vault_index, ground_via_skill_union
from applire.services.oracle.matchers.grounding import GROUNDED_MIN_COVERAGE


# ── the matcher in isolation ──────────────────────────────────────────────────

PROFILE_SKILLS_ONLY = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"en": "Backend engineer."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Contoso",
            "role": "Backend Engineer",
            "start_date": "2020-01",
            "end_date": None,
            "achievements": ["Led migration efforts at Contoso."],
        }
    ],
    "skills": [
        {"name": "Python"}, {"name": "FastAPI"}, {"name": "RESTful API design"},
        {"name": "PostgreSQL"}, {"name": "SQLAlchemy"}, {"name": "Docker"},
        {"name": "Git"}, {"name": "GitHub Actions"},
    ],
}


def test_enumeration_clause_fails_single_unit_but_clears_union():
    from applire.services.oracle.matchers import ground_text_claim

    index = build_vault_index(PROFILE_SKILLS_ONLY)
    text = (
        "My experience includes designing and implementing RESTful APIs "
        "with Python, FastAPI."
    )
    single = ground_text_claim(text, index)
    assert single.best_coverage < GROUNDED_MIN_COVERAGE, single.best_coverage

    union = ground_via_skill_union(text, index)
    assert union is not None
    assert union.best_coverage >= GROUNDED_MIN_COVERAGE
    paths = {u.path for u in union.qualifying_units}
    assert paths <= {f"skills[{i}]" for i in range(len(PROFILE_SKILLS_ONLY["skills"]))}


@pytest.mark.parametrize(
    "text",
    [
        "I have worked with PostgreSQL, SQLAlchemy, and Docker.",
        "I automated workflows using Git, GitHub Actions.",
    ],
)
def test_further_enumeration_clauses_clear_union(text):
    index = build_vault_index(PROFILE_SKILLS_ONLY)
    union = ground_via_skill_union(text, index)
    assert union is not None
    assert union.best_coverage >= GROUNDED_MIN_COVERAGE


def test_union_returns_none_when_vault_has_no_matching_skills():
    index = build_vault_index(PROFILE_SKILLS_ONLY)
    union = ground_via_skill_union(
        "My experience includes designing distributed ledger consensus protocols.",
        index,
    )
    assert union is None


def test_union_returns_none_for_empty_text():
    index = build_vault_index(PROFILE_SKILLS_ONLY)
    assert ground_via_skill_union("", index) is None


@pytest.mark.asyncio
async def test_enumeration_clause_grounds_end_to_end():
    verdict = await verify_claim(
        Claim(
            text=(
                "My experience includes designing and implementing RESTful "
                "APIs with Python, FastAPI."
            ),
            location="body.paragraphs[0][0]",
            kind="sentence",
        ),
        PROFILE_SKILLS_ONLY,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "grounding"


# ── the union pool NEVER includes work-experience narrative units ───────────

def test_union_pool_excludes_work_experience_units_even_when_they_match():
    """A work-experience achievement sharing the SAME vocabulary as a claim
    must never be pulled into the union pool — only top-level ``skills[]``
    units may aggregate (the #196 cross-role-blend guard)."""
    profile = {
        "personal_info": {"name": "Anna Bauer"},
        "professional_summary": {"en": "Engineer."},
        "work_experience": [
            {
                "id": "w1",
                "company": "Contoso",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": None,
                # Deliberately rich, matching vocabulary — but role-owned.
                "achievements": [
                    "Designed and implemented RESTful APIs with Python and FastAPI."
                ],
                "technologies": ["Python", "FastAPI"],
            }
        ],
        "skills": [{"name": "Docker"}],  # too little to clear coverage alone
    }
    index = build_vault_index(profile)
    union = ground_via_skill_union(
        "My experience includes designing and implementing RESTful APIs "
        "with Python, FastAPI.",
        index,
    )
    # Coverage cannot clear the floor from "Docker" alone — proves the rich
    # work_experience[0].achievements[0]/.technologies units were NOT unioned.
    assert union is None


# ── attribution check runs BEFORE the union fallback (ordering) ────────────
#
# Two positions: the ONLY real evidence for this enumeration clause's
# distinctive vocabulary ("Ansible playbooks", "deployment automation")
# belongs to the OLD role. The vault ALSO independently carries every named
# skill as role-agnostic top-level ``skills[]`` entries — enough, on their
# own, to clear the union-grounding floor. If the union fallback ran before
# (or instead of) the attribution check, this claim — rendered under the
# NEW role — would wrongly ground via the role-agnostic union. It must
# instead flag misattributed, using the single-unit grounding's own
# (exclusively foreign) best-effort evidence.

PROFILE_ORDERING = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"en": "Curious engineer with a strong sense of ownership."},
    "work_experience": [
        {
            "id": "w-old",
            "company": "OldCo",
            "role": "DevOps Engineer",
            "start_date": "2018-01",
            "end_date": "2022-01",
            "achievements": [
                "Automated deployment pipelines using Ansible playbooks for "
                "the infrastructure team."
            ],
            "technologies": ["Ansible", "Deployment Automation"],
        },
        {
            "id": "w-new",
            "company": "NewCo",
            "role": "Senior Platform Engineer",
            "start_date": "2022-02",
            "end_date": None,
            "achievements": ["Built the internal developer portal."],
        },
    ],
    "skills": [
        {"name": "Python"}, {"name": "Docker"}, {"name": "Ansible"},
        {"name": "Code"}, {"name": "Mentoring"}, {"name": "Deployment"},
        {"name": "Pipelines"}, {"name": "Playbooks"},
    ],
}

ORDERING_CLAIM_TEXT = (
    "My experience includes automating deployment pipelines using Ansible "
    "playbooks and managing infrastructure as code."
)


@pytest.mark.asyncio
async def test_union_would_ground_this_claim_in_isolation():
    """Pin the premise: taken alone, the union fallback DOES clear the floor
    for this text — proving the rescue risk is real, not vacuous."""
    index = build_vault_index(PROFILE_ORDERING)
    union = ground_via_skill_union(ORDERING_CLAIM_TEXT, index)
    assert union is not None
    assert union.best_coverage >= GROUNDED_MIN_COVERAGE


@pytest.mark.asyncio
async def test_anchored_foreign_claim_is_misattributed_not_rescued_by_union():
    verdict = await verify_claim(
        Claim(
            text=ORDERING_CLAIM_TEXT,
            location="body.paragraphs[0][0]",
            kind="clause",
            source_experience_id="w-new",
        ),
        PROFILE_ORDERING,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"


@pytest.mark.asyncio
async def test_same_role_anchor_still_grounds():
    """The ordering gate must not become a blanket block — a claim actually
    anchored to the role that owns the evidence still grounds fine."""
    verdict = await verify_claim(
        Claim(
            text=ORDERING_CLAIM_TEXT,
            location="body.paragraphs[0][0]",
            kind="clause",
            source_experience_id="w-old",
        ),
        PROFILE_ORDERING,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_unanchored_claim_still_grounds_via_union():
    """No source id at all (role-agnostic surface, or legacy data) — the
    union fallback applies normally, since there is no anchor to betray."""
    verdict = await verify_claim(
        Claim(text=ORDERING_CLAIM_TEXT, location="body.paragraphs[0][0]", kind="clause"),
        PROFILE_ORDERING,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "grounding"
