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

"""F4 (blind PQ 2026-07-02) — stance guidance in the gap-analysis prompts.

The gap classifier receives the WHOLE profile JSON — including
``metadata.enrichment_history``, where interview-sourced records carry the
candidate's prior gap-interview answers. A written denial ("I have no hands-on
Azure experience") therefore reaches the LLM as unlabeled profile text: a
token-matching model reads "Azure" as a signal FOR the skill. These tests pin:

1. the SYSTEM prompt carries explicit stance rules (denial = evidence AGAINST →
   "gap"; compound requirements never alias unsupported technologies into a
   claimable concept's surface forms);
2. the user prompt renders interview-sourced content ONLY inside a labeled
   CANDIDATE INTERVIEW STATEMENTS section that states the denial rule, and the
   CANDIDATE PROFILE block no longer smuggles the enrichment trail;
3. the MockLLMProvider still recognises the gap chain after the prompt edit
   (the "three-category gap analysis" fingerprint — the MOCK RULE).
"""

import asyncio

from applire.prompts.gap_analysis import SYSTEM_PROMPT, build_user_prompt
from applire.services.gap_inference import pre_classify

_DENIAL = "My cloud experience is AWS, not Azure - I have no hands-on Azure experience."

_JOB = {
    "role_title": "IT Quality Manager",
    "required_skills": ["Cloud environment qualification (AWS, Azure)"],
    "nice_to_have_skills": [],
    "keywords": ["AWS", "Azure"],
    "seniority_level": "senior",
    "company_culture_signals": [],
    "language_requirement": "DE",
}


def _profile_with_interview_denial() -> dict:
    return {
        "personal_info": {"first_name": "Max", "last_name": "Muster"},
        "work_experience": [
            {"company": "Rheinpharm", "role": "IT Quality Lead", "start_date": "2018-01"}
        ],
        "skills": [{"name": "AWS", "category": "technical", "proficiency": "advanced"}],
        "education": [],
        "languages": [],
        "metadata": {
            "completeness_score": 0.8,
            "enrichment_history": [
                {
                    "source": "cv_upload",
                    "changes": [
                        {"section": "*", "field": "*", "action": "added", "new_value": "initial import"}
                    ],
                },
                {
                    "source": "interview",
                    "changes": [
                        {
                            "section": "work_experience",
                            "field": "achievements",
                            "action": "merged",
                            "new_value": ["Qualified the company's first GxP cloud environment (AWS)"],
                            "rationale": f'Candidate answered: "{_DENIAL}"',
                        }
                    ],
                },
            ],
        },
    }


def _build(profile: dict) -> str:
    return build_user_prompt(_JOB, profile, pre_classify(_JOB, profile))


# ── SYSTEM prompt stance rules ────────────────────────────────────────────────


def test_system_prompt_states_denial_is_evidence_against():
    assert "evidence AGAINST" in SYSTEM_PROMPT
    assert 'never "direct" or "partial"' in SYSTEM_PROMPT


def test_system_prompt_forbids_unsupported_tokens_in_claimable_surface_forms():
    low = " ".join(SYSTEM_PROMPT.lower().split())  # collapse line-wrapping
    assert "compound" in low
    assert "must not appear among a claimable concept's surface forms" in low


def test_system_prompt_keeps_the_mock_fingerprint():
    # MOCK RULE: MockLLMProvider keys the gap chain off this exact substring.
    assert "three-category gap analysis" in SYSTEM_PROMPT.lower()


def test_mock_provider_still_recognises_the_gap_chain():
    from applire.providers.llm.mock import MockLLMProvider

    profile = _profile_with_interview_denial()
    data = asyncio.run(
        MockLLMProvider().aparse_json(_build(profile), system=SYSTEM_PROMPT)
    )
    assert isinstance(data, dict) and "classifications" in data, (
        "the mock must still route the edited system prompt to the gap-analysis chain"
    )


# ── User prompt: labeled candidate-statements section ─────────────────────────


def test_denial_appears_only_inside_labeled_statements_section():
    prompt = _build(_profile_with_interview_denial())

    assert "CANDIDATE INTERVIEW STATEMENTS" in prompt, (
        "interview-sourced content must be surfaced under an explicit label"
    )
    i_profile = prompt.index("CANDIDATE PROFILE:")
    i_stmts = prompt.index("CANDIDATE INTERVIEW STATEMENTS")
    i_pre = prompt.index("PRE-CLASSIFICATION:")
    assert i_profile < i_stmts < i_pre, "statements section sits between profile and pre-classification"

    profile_block = prompt[i_profile:i_stmts]
    assert _DENIAL not in profile_block, (
        "the denial must NOT sit unlabeled inside the CANDIDATE PROFILE dump"
    )
    stmts_block = prompt[i_stmts:i_pre]
    assert _DENIAL in stmts_block, "the denial must be present in the labeled section"
    # The label itself carries the stance rule.
    assert "DENY" in stmts_block and '"gap"' in stmts_block


def test_profile_block_no_longer_contains_the_metadata_trail():
    prompt = _build(_profile_with_interview_denial())
    assert "enrichment_history" not in prompt
    assert "completeness_score" not in prompt
    # Non-interview enrichment records are not rendered anywhere.
    assert "initial import" not in prompt


def test_no_statements_section_without_interview_records():
    profile = _profile_with_interview_denial()
    profile["metadata"]["enrichment_history"] = [
        r for r in profile["metadata"]["enrichment_history"] if r["source"] != "interview"
    ]
    prompt = _build(profile)
    assert "CANDIDATE INTERVIEW STATEMENTS" not in prompt
    assert "CANDIDATE PROFILE:" in prompt and "PRE-CLASSIFICATION:" in prompt


def test_prompt_tolerates_profile_without_metadata():
    profile = _profile_with_interview_denial()
    del profile["metadata"]
    prompt = _build(profile)
    assert "CANDIDATE INTERVIEW STATEMENTS" not in prompt
    assert "CANDIDATE PROFILE:" in prompt


def test_build_user_prompt_does_not_mutate_the_profile_dict():
    profile = _profile_with_interview_denial()
    _build(profile)
    assert "metadata" in profile, "the caller's profile dict must stay intact"
    assert profile["metadata"]["enrichment_history"], "enrichment trail must stay intact"
