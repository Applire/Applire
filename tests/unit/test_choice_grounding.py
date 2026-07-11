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

"""#110 — deterministic grounding filter for interview starting-point chips.

An LLM-drafted chip may only ASSERT experience with a JD/cluster term when the
profile actually evidences that term. Honesty frames (chips that deny direct
experience) may name the term. The guarantee lives in code, not in the prompt.
"""

import pytest

from applire.providers.llm.base import LLMProvider
from applire.services.choice_grounding import filter_ungrounded_choices
from applire.services.interview_graph import question_generator_with_profile

CLUSTER = {
    "id": "cluster-cloud",
    "label": "Cloud environment qualification",
    "gaps": ["Cloud qualification", "Azure"],
    "jd_skills": ["Azure", "AWS"],
    "jd_context": "Qualify cloud-hosted GxP systems (Azure, AWS)",
}

# Profile evidences AWS (eQMS migration) but has NO Azure anywhere.
PROFILE = {
    "skills": [{"name": "AWS", "category": "Cloud"}, {"name": "Python"}],
    "work_experience": [
        {
            "company": "MedTech GmbH",
            "role": "QA Engineer",
            "technologies": ["AWS", "Python"],
            "responsibilities": ["Migrated the eQMS to AWS"],
            "achievements": [],
        }
    ],
}


class TestFilterUngroundedChoices:
    def test_affirmative_chip_with_evidenced_term_is_kept(self):
        chips = ["My AWS work included migrating our eQMS — happy to detail the validation."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "B") == chips

    def test_affirmative_chip_asserting_unevidenced_term_is_dropped(self):
        # The blind-PQ F5 case: the chip attributes an Azure-hosted system to a
        # user with zero Azure evidence.
        chips = ["I qualified an Azure-hosted MES including IQ/OQ documentation."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "C") is None

    def test_honesty_frame_may_name_the_unevidenced_term(self):
        chips = ["I haven't worked with Azure directly, but my AWS migration covered similar controls."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "C") == chips

    def test_german_honesty_frame_is_recognised(self):
        chips = ["Mit Azure habe ich bisher nicht direkt gearbeitet, aber meine AWS-Migration war vergleichbar."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "C") == chips

    def test_mixed_list_keeps_grounded_and_frames_drops_invented(self):
        chips = [
            "My AWS work included the eQMS migration at MedTech GmbH.",
            "I qualified an Azure-hosted MES.",
            "I haven't worked with Azure directly, but I know cloud validation from AWS.",
        ]
        out = filter_ungrounded_choices(chips, CLUSTER, PROFILE, "C")
        assert out == [chips[0], chips[2]]

    def test_all_dropped_returns_none(self):
        chips = ["I validated an Azure LIMS.", "My Azure experience spans five years."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "C") is None

    def test_morphological_fold_matches_evidence(self):
        # Evidence says "microservice architecture"; chip says "microservices".
        cluster = {"label": "Microservices", "gaps": ["microservices"], "jd_skills": ["microservices"]}
        profile = {
            "skills": [],
            "work_experience": [
                {"company": "X", "role": "Dev", "responsibilities": ["Built a microservice architecture"]}
            ],
        }
        chips = ["I designed microservices in production."]
        assert filter_ungrounded_choices(chips, cluster, profile, "B") == chips

    def test_chip_without_cluster_terms_passes(self):
        # Generic scaffold asserting nothing cluster-specific stays.
        chips = ["In my current role I own our quality tooling end to end."]
        assert filter_ungrounded_choices(chips, CLUSTER, PROFILE, "B") == chips

    def test_none_and_empty_pass_through(self):
        assert filter_ungrounded_choices(None, CLUSTER, PROFILE, "B") is None
        assert filter_ungrounded_choices([], CLUSTER, PROFILE, "B") is None


def _mode_a_state() -> dict:
    return {
        "mode": "targeted",
        "critical_gaps": ["cluster-cloud"],
        "current_gap_index": 0,
        "gap_clusters_by_id": {"cluster-cloud": CLUSTER},
        "messages": [],
    }


class _UngroundedChipsProvider(LLMProvider):
    """Drafts one grounded chip and one fabricated Azure claim (blind-PQ F5)."""

    async def acomplete(self, prompt, **kwargs):
        return ""

    async def aparse_json(self, prompt, **kwargs):
        if "language reviewer" in (kwargs.get("system") or "").lower():
            return {"approved": True, "issues": [], "feedback": ""}
        return {
            "question": "How does your cloud experience map to this role?",
            "choices": [
                "My AWS work included migrating our eQMS.",
                "I qualified an Azure-hosted MES including IQ/OQ documentation.",
            ],
        }


@pytest.mark.asyncio
async def test_mode_a_generator_drops_ungrounded_chips():
    out = await question_generator_with_profile(
        _mode_a_state(), PROFILE, _UngroundedChipsProvider(), gap_category="C", lang="en"
    )
    assert out["question"]
    assert out["choices"] == ["My AWS work included migrating our eQMS."]
