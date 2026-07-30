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

"""#303 -- a narrative-presence requirement for high-fit claimable concepts.

Ground truth (ADR-064 charter run, 2026-07-29): a Keyword Ledger concept with
``status == "direct"``, ``claimable is True`` and ``fit_weight == 1.0`` (a
hard-requirement the candidate genuinely has) shipped as a BARE skills-list
keyword three times over, with no work-history bullet or project entry ever
mentioning it. Two blind hiring reviewers independently scored this a risk;
the hiring manager said removing the keyword would have RAISED confidence.

Root cause (verified against ``services/keyword_ledger.py`` before writing
this test, per the brief): the CV chain has exactly two coverage predicates.
``verified_missing_claimable`` scans ``_draft_strings`` -- every string in the
WHOLE serialised document, skills array included -- so a bare skills tag
alone satisfies it. ``verified_missing_load_bearing`` DOES scope to the
narrative corpus, but is gated behind ``is_load_bearing``, which requires a
percent/currency figure in the entry's evidence -- a concept like
"Kubernetes" with no such figure never reaches it. No existing predicate ever
asks "does a high-fit claimable concept appear anywhere OTHER than the
skills array" -- confirmed by reading both functions; this is not a
disproof, the mechanism is exactly as briefed.

This module is the new SIBLING predicate that closes that gap, without
widening ``is_load_bearing`` (deliberately narrow -- shared with the letter
chain, and its own docstring pins a real case, "SAP, expert, 15 years", that
must stay NOT load-bearing).
"""

from applire.services.keyword_ledger import (
    narrative_presence_reviewer_prompt_fn,
    render_verified_narrative_block,
    verified_missing_narrative_presence,
)

# A high-fit, claimable, DIRECT concept with NO figure in its evidence --
# exactly the #303 shape (contrast with #315's "Budgetverantwortung", which
# carries a currency figure and is already caught by
# ``verified_missing_load_bearing``).
_LEDGER = [
    {
        "concept": "Kubernetes",
        "surface_forms": ["Kubernetes", "K8s"],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Explicitly listed as a skill (Kubernetes, expert, 6 years).",
    },
    # A nice-to-have (fit_weight 0.5) must NOT be covered by this predicate --
    # #303's own acceptance criterion is fit_weight == 1.0 only.
    {
        "concept": "Terraform",
        "surface_forms": ["Terraform"],
        "claimable": True,
        "status": "direct",
        "sources": ["nice_to_have"],
        "fit_weight": 0.5,
        "evidence": "Explicitly listed as a skill (Terraform, intermediate).",
    },
    # A `partial` entry, even at fit_weight 1.0, must not be covered --
    # #303's acceptance criterion is status == "direct" only.
    {
        "concept": "gRPC",
        "surface_forms": ["gRPC"],
        "claimable": True,
        "status": "partial",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Adjacent capability, below the JD's stated bar.",
    },
]

_DRAFT_BARE_TAG_ONLY = {
    "summary": "Platform engineer with deep infrastructure expertise.",
    "work_history": [
        {
            "company": "Acme", "role": "Platform Engineer",
            "bullets": ["Led the migration of the deployment pipeline."],
        },
    ],
    "skills": ["Kubernetes", "Terraform", "gRPC"],
}

_DRAFT_NARRATED = {
    "summary": "Platform engineer with deep infrastructure expertise.",
    "work_history": [
        {
            "company": "Acme", "role": "Platform Engineer",
            "bullets": [
                "Migrated the deployment pipeline onto Kubernetes across three regions."
            ],
        },
    ],
    "skills": ["Kubernetes", "Terraform", "gRPC"],
}


class TestVerifiedMissingNarrativePresence:
    def test_bare_skills_tag_is_reported_missing(self):
        """The #303 defect, reproduced directly: 'Kubernetes' is present in the
        skills array (satisfying the whole-document predicate) but nowhere in
        the narrative -- this predicate must catch it where the other cannot."""
        missing = verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, _LEDGER)
        assert [e["concept"] for e in missing] == ["Kubernetes"]

    def test_narrated_concept_is_not_reported(self):
        """Once the concept appears in a work-history bullet, it is no longer
        missing -- narrative presence, not merely document presence."""
        missing = verified_missing_narrative_presence(_DRAFT_NARRATED, _LEDGER)
        assert missing == []

    def test_nice_to_have_at_full_weight_is_never_covered(self):
        """fit_weight 0.5 (nice-to-have) is out of scope, however absent from
        the narrative -- #303's own acceptance criterion is fit_weight == 1.0."""
        missing = verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, _LEDGER)
        assert "Terraform" not in [e["concept"] for e in missing]

    def test_partial_status_is_never_covered(self):
        """A `partial` entry is out of scope even at fit_weight 1.0 -- #303's
        own acceptance criterion is status == 'direct'."""
        missing = verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, _LEDGER)
        assert "gRPC" not in [e["concept"] for e in missing]

    def test_no_ledger_returns_empty(self):
        assert verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, None) == []
        assert verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, []) == []

    def test_nested_project_bullets_count_as_narrative(self):
        """`_tailored_narrative_texts` scopes to work_history bullets AND nested
        project bullets -- a project entry naming the concept must also clear
        this predicate (the #303 'Kafka' case: a strong project bullet that
        never names the term stays missing; one that DOES name it clears)."""
        draft = {
            "summary": "Platform engineer.",
            "work_history": [
                {
                    "company": "Acme", "role": "Platform Engineer",
                    "bullets": ["Owned the platform roadmap."],
                    "projects": [
                        {"name": "Migration",
                         "bullets": ["Rebuilt the pipeline on Kubernetes."]},
                    ],
                },
            ],
            "skills": ["Kubernetes", "Terraform", "gRPC"],
        }
        missing = verified_missing_narrative_presence(draft, _LEDGER)
        assert "Kubernetes" not in [e["concept"] for e in missing]


class TestRenderVerifiedNarrativeBlock:
    def test_empty_for_no_misses(self):
        assert render_verified_narrative_block([]) == ""

    def test_block_names_term_and_waiver_rule(self):
        missing = verified_missing_narrative_presence(_DRAFT_BARE_TAG_ONLY, _LEDGER)
        block = render_verified_narrative_block(missing)
        low = block.lower()
        assert "kubernetes" in low
        assert "waive" in low
        assert "approved" in low or "reject" in low


class TestNarrativePresenceReviewerPromptFn:
    def test_appends_block_when_narrative_missing(self):
        base = lambda source, draft: f"BASE[{source}]"
        fn = narrative_presence_reviewer_prompt_fn(base, _LEDGER)
        prompt = fn("src", _DRAFT_BARE_TAG_ONLY)
        assert prompt.startswith("BASE[src]")
        assert "Kubernetes" in prompt

    def test_prompt_unchanged_once_narrated(self):
        base = lambda source, draft: f"BASE[{source}]"
        fn = narrative_presence_reviewer_prompt_fn(base, _LEDGER)
        assert fn("src", _DRAFT_NARRATED) == "BASE[src]"

    def test_prompt_unchanged_without_ledger(self):
        base = lambda source, draft: "BASE"
        fn = narrative_presence_reviewer_prompt_fn(base, None)
        assert fn("s", _DRAFT_BARE_TAG_ONLY) == "BASE"
