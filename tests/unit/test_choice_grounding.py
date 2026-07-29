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

    def test_typographic_apostrophe_honesty_frame_is_recognised(self):
        # Blind agent probe 2026-07-11: real models emit "haven’t" (U+2019);
        # the ASCII-only marker missed it and over-dropped truthful frames.
        chips = ["I haven’t worked directly with Azure, but I’ve used Docker in CI/CD pipelines."]
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


# ── ADR-064 Task 3c — "no experience" / "keine Erfahrung" denial coverage ────
# The filter was built when every choice was an assertion. A live probe run
# while investigating Task 3 found that "I have no experience with X." and
# "Ich habe keine Erfahrung mit X." — both entirely ordinary denial phrasings,
# and neither a hedge — were NOT recognised as honesty frames by the
# pre-existing _HONESTY_MARKERS list (only "no direct"/"keine direkte" were
# covered, not the plain "no experience"/"keine Erfahrung" form). Because they
# name the unevidenced concept, they fell through to the ordinary assertion
# check and were DROPPED — the honest option silently removed from the
# candidate's choices. Fixed by extending _HONESTY_MARKERS; pinned below.
TOGAF_CLUSTER = {
    "id": "cluster-ea",
    "label": "Enterprise architecture frameworks",
    "gaps": ["TOGAF"],
    "jd_skills": ["TOGAF"],
    "jd_context": "Own the enterprise architecture governance model (TOGAF).",
}

EA_PROFILE = {
    "skills": [{"name": "Enterprise Architecture", "category": "domain"}],
    "work_experience": [
        {
            "company": "Acme GmbH",
            "role": "Architect",
            "technologies": ["ArchiMate"],
            "responsibilities": ["Designed the EA governance model"],
            "achievements": [],
        }
    ],
}


class TestNoExperienceDenialCoverage:
    def test_plain_no_experience_denial_survives(self):
        # Before the fix this was silently dropped — reproduce it directly.
        chips = ["I have no experience with TOGAF."]
        assert filter_ungrounded_choices(chips, TOGAF_CLUSTER, EA_PROFILE, "C") == chips

    def test_german_keine_erfahrung_denial_survives(self):
        chips = ["Ich habe keine Erfahrung mit TOGAF."]
        assert filter_ungrounded_choices(chips, TOGAF_CLUSTER, EA_PROFILE, "C") == chips

    def test_german_bisher_keine_denial_survives(self):
        chips = ["Mit TOGAF habe ich bisher keine Berührungspunkte."]
        assert filter_ungrounded_choices(chips, TOGAF_CLUSTER, EA_PROFILE, "C") == chips

    def test_overclaiming_choice_for_the_same_absent_concept_still_dropped(self):
        # The fix must not turn the filter into a rubber stamp — an
        # affirmative claim for the same unevidenced concept is still cut.
        chips = ["I led our TOGAF adoption end to end."]
        assert filter_ungrounded_choices(chips, TOGAF_CLUSTER, EA_PROFILE, "C") is None

    def test_mixed_list_keeps_the_denial_and_drops_the_overclaim(self):
        chips = [
            "I have no experience with TOGAF.",
            "I led our TOGAF adoption end to end.",
        ]
        out = filter_ungrounded_choices(chips, TOGAF_CLUSTER, EA_PROFILE, "C")
        assert out == [chips[0]]


# ── #236 — employer-scoped attribution guard ─────────────────────────────────
# Fixture mirrors the REAL vault entry from the live trace (backend/logs/llm/
# 2026-07-23.jsonl, the interview_question call that produced the F5 chip):
# NordPharm legitimately carries LangGraph/LangChain/RAG — an agentic GenAI
# system automating regulatory submission documentation on Databricks. The
# Q4 chip's fabrication was NOT the tech (NordPharm genuinely has
# LangGraph/RAG) but the invented narrative context ("clinical data
# workflows under tight timelines"), conflated from an unrelated bullet
# ("clinical orders") plus invented urgency.
AI_CLUSTER = {
    "id": "cluster-ai-product",
    "label": "AI Product Development",
    "gaps": ["Commercial AI product development", "Fast-moving product-led environment experience"],
    "jd_skills": ["Commercial AI product development", "Fast-moving product-led environment experience"],
    "jd_context": "I must drive the development and scaling of AI products in a dynamic, product-led environment.",
}

NORDPHARM_WORK_ENTRY = {
    "id": "845079fb-7564-4c77-a035-9be3ce618def",
    "company": "NordPharm SE",
    "role": "Associate Director Platform Systems",
    "industry_context": "Biotechnology, Pharmaceuticals, GxP (GCLP/GMP)",
    "responsibilities": [
        "Lead cross-functional teams establishing cross domain data flows for clinical "
        "orders, chain of Identity (COI) and chain of Custody (COC) compliance.",
        "Establish critical cross domain data flows between GCLP and GCP areas, integrating "
        "data from clinic facing systems like IRT and EDC systems into the Vantum process.",
        "IT business partner for CTSM, working closely with IMP-Management and patient operations.",
        "Manage AI automation project using Databricks and large language models to "
        "streamline validation document generation, aiming for over 80% efficiency improvement.",
    ],
    "achievements": [
        "Initiated and lead — as architect and product owner — an agentic GenAI system that "
        "automates the authoring and review of regulatory submission documentation. "
        "Built with LangGraph/LangChain and retrieval-augmented generation (RAG) over "
        "reference documents and the SOPs relevant to each workflow, running on Databricks. "
        "The system performs internal reviews and approvals, so documents typically need human "
        "review only once.",
        "Recruited a cross-team group of volunteers to build the prototype, which was selected "
        "for full implementation; the solution targets an estimated 70% reduction in manual "
        "effort for both authoring and reviewing validation documents.",
    ],
    "technologies": [
        "Databricks", "Large Language Models", "IRT", "EDC", "Vantum",
        "LangGraph", "LangChain", "Retrieval-Augmented Generation (RAG)",
    ],
}

APPLIRE_WORK_ENTRY = {
    "id": "74280b30-4619-4f13-bb67-3eae9a79d84f",
    "company": "Applire",
    "role": "Founder & Lead Developer",
    "responsibilities": ["Deployed our microservices on Kubernetes clusters."],
    "achievements": [],
    "technologies": ["Python", "FastAPI", "Kubernetes", "Docker", "MCP"],
}

NORDPHARM_PROFILE = {
    "skills": [
        {
            "name": "LangGraph",
            "category": "technical",
            "experience_refs": [NORDPHARM_WORK_ENTRY["id"]],
        },
        {
            "name": "Retrieval-Augmented Generation (RAG)",
            "category": "technical",
            "experience_refs": [NORDPHARM_WORK_ENTRY["id"]],
        },
        {
            "name": "Kubernetes",
            "category": "technical",
            "experience_refs": [APPLIRE_WORK_ENTRY["id"]],
        },
    ],
    "work_experience": [NORDPHARM_WORK_ENTRY, APPLIRE_WORK_ENTRY],
}


class TestEmployerScopedAttributionGuard:
    def test_verbatim_fabricated_context_chip_is_dropped(self):
        # The exact F5 chip from the live trace — LangGraph/RAG are real NordPharm
        # tech, but "clinical data workflows under tight timelines" is an invented
        # narrative never stated in the NordPharm entry's own bullets.
        chip = (
            "At NordPharm, I contributed to scaling AI-driven solutions like LangGraph "
            "and RAG for clinical data workflows under tight timelines."
        )
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") is None

    def test_faithful_paraphrase_of_the_real_bullet_is_kept(self):
        chip = (
            "At NordPharm, I led an agentic GenAI system automating validation "
            "documentation with LangGraph and RAG."
        )
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") == [chip]

    def test_tech_evidenced_only_at_other_employer_is_dropped(self):
        # Kubernetes is real — but only at Applire, never at NordPharm.
        cluster = {"label": "Container Orchestration", "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"]}
        chip = "At NordPharm, I deployed our services on Kubernetes clusters."
        assert filter_ungrounded_choices([chip], cluster, NORDPHARM_PROFILE, "C") is None

    def test_same_tech_naming_its_real_employer_is_kept(self):
        cluster = {"label": "Container Orchestration", "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"]}
        chip = "At Applire, I deployed our services on Kubernetes clusters."
        assert filter_ungrounded_choices([chip], cluster, NORDPHARM_PROFILE, "C") == [chip]

    def test_honesty_frame_naming_an_employer_to_deny_is_kept(self):
        chip = (
            "I haven’t worked with Kubernetes at NordPharm, but I’ve run it in "
            "production at Applire."
        )
        cluster = {"label": "Container Orchestration", "gaps": ["Kubernetes"], "jd_skills": ["Kubernetes"]}
        assert filter_ungrounded_choices([chip], cluster, NORDPHARM_PROFILE, "C") == [chip]

    def test_employer_free_scaffold_chip_still_passes(self):
        # No employer named — today's cluster-term-only behaviour is unchanged.
        chip = "In my current role I own our quality tooling end to end."
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") == [chip]

    def test_partial_legal_form_variant_matches_full_company_name(self):
        # Profile carries "NordPharm SE"; the chip only says "NordPharm".
        chip = (
            "At NordPharm, I led an agentic GenAI system automating validation "
            "documentation with LangGraph and RAG."
        )
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") == [chip]

    def test_german_fabricated_chip_is_dropped(self):
        chip = (
            "Bei NordPharm habe ich KI-Lösungen wie LangGraph und RAG für klinische "
            "Datenworkflows unter engen Zeitvorgaben skaliert."
        )
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") is None

    def test_german_faithful_paraphrase_is_kept(self):
        chip = (
            "Bei NordPharm habe ich ein agentisches GenAI-System mit LangGraph und RAG "
            "zur Validierungsdokumentation automatisiert."
        )
        assert filter_ungrounded_choices([chip], AI_CLUSTER, NORDPHARM_PROFILE, "B") == [chip]


# ── honesty-frame clause scoping (adversarial pass, 2026-07-23) ──────────────
# Live-reproduced bypass: "I haven't used Tailwind CSS directly, but I've
# worked with React and Next.js to create responsive applications at
# StartupXYZ" — Next.js/React exist ONLY at TechCorp GmbH in the vault, never
# at StartupXYZ. The whole-chip honesty exemption let the fabricated,
# employer-misattributed AFFIRMATIVE clause ride along with the legitimate
# Tailwind denial, bypassing the #236 employer-scoped guard entirely.
FRONTEND_CLUSTER = {
    "id": "cluster-frontend",
    "label": "Frontend framework experience",
    "gaps": ["Next.js"],
    "jd_skills": ["Next.js", "React"],
    "jd_context": "Own our customer-facing web app (React, Next.js).",
}

TECHCORP_WORK_ENTRY = {
    "id": "techcorp-1",
    "company": "TechCorp GmbH",
    "role": "Frontend Engineer",
    "responsibilities": [
        "Built responsive customer-facing applications using React and Next.js."
    ],
    "achievements": [],
    "technologies": ["React", "Next.js", "TypeScript"],
}

STARTUPXYZ_WORK_ENTRY = {
    "id": "startupxyz-1",
    "company": "StartupXYZ",
    "role": "Backend Engineer",
    "responsibilities": ["Built REST APIs with Node.js and PostgreSQL."],
    "achievements": [],
    "technologies": ["Node.js", "PostgreSQL", "Docker"],
}

FRONTEND_PROFILE = {
    "skills": [],
    "work_experience": [TECHCORP_WORK_ENTRY, STARTUPXYZ_WORK_ENTRY],
}


class TestHonestyFrameClauseScoping:
    def test_verbatim_startupxyz_chip_is_dropped(self):
        # The live-reproduced bug: Next.js/React misattributed to StartupXYZ.
        chip = (
            "I haven't used Tailwind CSS directly, but I've worked with React "
            "and Next.js to create responsive applications at StartupXYZ"
        )
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") is None

    def test_same_chip_with_employer_corrected_is_kept(self):
        # Identical claim, correct employer — the affirmative clause is truthful.
        chip = (
            "I haven't used Tailwind CSS directly, but I've worked with React "
            "and Next.js to create responsive applications at TechCorp"
        )
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_truthful_affirmative_clause_with_correct_employer_is_kept(self):
        # Over-drop discipline (#207 lesson): a truthful honesty-frame
        # affirmation naming the RIGHT employer must survive.
        chip = (
            "I haven't used Tailwind directly, but I've worked with React and "
            "Next.js at TechCorp."
        )
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_pure_denial_with_no_pivot_keeps_full_exemption(self):
        chip = "I haven't used Tailwind CSS directly."
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_pure_denial_typographic_apostrophe_no_pivot_keeps_full_exemption(self):
        chip = "I haven’t used Tailwind CSS directly."
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_pure_denial_german_no_pivot_keeps_full_exemption(self):
        chip = "Mit Tailwind CSS habe ich bisher nicht direkt gearbeitet."
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_german_pivot_misattributed_affirmative_is_dropped(self):
        chip = (
            "Mit Tailwind CSS habe ich bisher nicht direkt gearbeitet, aber ich "
            "habe React und Next.js bei StartupXYZ eingesetzt."
        )
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") is None

    def test_german_pivot_correct_employer_affirmative_is_kept(self):
        chip = (
            "Mit Tailwind CSS habe ich bisher nicht direkt gearbeitet, aber ich "
            "habe React und Next.js bei TechCorp eingesetzt."
        )
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]

    def test_affirmative_naming_no_employer_with_evidenced_term_is_kept(self):
        # No employer named in the affirmative — today's whole-profile
        # cluster-term check still applies (unchanged).
        chip = "I haven't used GraphQL directly, but I've built with Next.js."
        assert filter_ungrounded_choices([chip], FRONTEND_CLUSTER, FRONTEND_PROFILE, "C") == [chip]


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


def _nordpharm_mode_a_state() -> dict:
    return {
        "mode": "targeted",
        "critical_gaps": ["cluster-ai-product"],
        "current_gap_index": 0,
        "gap_clusters_by_id": {"cluster-ai-product": AI_CLUSTER},
        "messages": [],
    }


class _FabricatedContextChipProvider(LLMProvider):
    """Drafts the VERBATIM #236 chip: real tech (LangGraph/RAG), invented
    narrative context ("clinical data workflows under tight timelines")."""

    async def acomplete(self, prompt, **kwargs):
        return ""

    async def aparse_json(self, prompt, **kwargs):
        if "language reviewer" in (kwargs.get("system") or "").lower():
            return {"approved": True, "issues": [], "feedback": ""}
        return {
            "question": (
                "Can you share a specific example where you drove the development "
                "or scaling of an AI product?"
            ),
            "choices": [
                "At NordPharm, I contributed to scaling AI-driven solutions like "
                "LangGraph and RAG for clinical data workflows under tight timelines.",
                "At NordPharm, I led an agentic GenAI system automating validation "
                "documentation with LangGraph and RAG.",
            ],
        }


@pytest.mark.asyncio
async def test_mode_a_generator_drops_fabricated_context_chip_for_real_employer():
    out = await question_generator_with_profile(
        _nordpharm_mode_a_state(),
        NORDPHARM_PROFILE,
        _FabricatedContextChipProvider(),
        gap_category="B",
        lang="en",
    )
    assert out["question"]
    assert out["choices"] == [
        "At NordPharm, I led an agentic GenAI system automating validation "
        "documentation with LangGraph and RAG."
    ]
