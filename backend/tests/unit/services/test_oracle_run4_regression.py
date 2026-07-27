# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#237 run-4 regression — the letter path on REALISTIC multi-role prose.

Ground truth (2026-07-24, founder self-audit run-4, dumped from the dev DB):
on a genuinely honest letter, the Oracle scored {unverifiable: 10, grounded:
4} — near-zero discriminating power — and the ONE real fabrication
("mentoring teams of 5+", a figure borrowed from an unrelated CURRENT-role
fact, "Lead a team of five tech leads") filed as merely ``unverifiable``,
camouflaged as checker conservatism rather than flagged as a problem.

Root causes, confirmed against the live data (see this module's fixtures,
sanitized: fully synthetic — real name, contact, address, employer names and
location all replaced; only the STRUCTURAL shapes that reproduce the bug
(one company across three internal roles, legal-form suffix in the vault
but dropped in prose, a second distinct employer) are preserved):

1. A long, ordinary tenure at ONE company held across several INTERNAL
   roles (three successive Nordvance positions) matched every one of them by
   company name — genuinely ambiguous by name alone — which starved the
   attribution matcher of the anchor it exists to feed on almost every
   Nordvance-mentioning sentence. Fixed by a same-company, current-role
   tie-break in ``extract._find_employer_anchor`` (#237 run-4 residual).
2. The vault stores the legal entity name ("Nordvance SE"); real letter
   prose drops the suffix ("At Nordvance"). The STRICT anchor's exact-name-
   only matching (deliberately preserved by #248) NEVER fired for this,
   the COMMON case — see ``extract._find_employer_anchor``'s updated
   docstring and ``test_oracle_extract.py``'s
   ``test_strict_anchor_now_tolerates_legal_form_suffix_via_current_role_
   tiebreak`` (supersedes the old #248-era pin).
3. A "5" quantifier written as a digit ("5+") never matched a vault fact
   spelled out as a word ("five") — :func:`applire.services.oracle.
   matchers.figures.extract_spelled_figures` bridges this on the VAULT
   side only (see that function's docstring).
4. Once bridged, the figure's only real vault source belongs to a
   DIFFERENT, unrelated position than the sentence's own context — the
   ownership check used to soften this to ``unverifiable``; it now
   escalates to a genuine negative verdict via owner-scoped content-overlap
   (``audit._owner_scoped_coverage`` / ``_UNATTRIBUTABLE_CONTENT_FLOOR``).
5. Narrative sentences bundling several facts about ONE role rarely clear
   the single-unit coverage floor — :func:`applire.services.oracle.
   matchers.grounding.ground_via_role_union` (an anchored claim's
   counterpart to the pre-existing skill-union fallback) and paragraph-
   scoped anchor continuation (:func:`applire.services.oracle.extract.
   extract_claims_from_letter`) fix this.
6. Pure courtesy/logistics boilerplate not covered by the existing #242
   filter piled into the unverifiable bucket — the formula-seed patterns
   are extended (still general phrasing families, not run-4's exact
   strings).

Together these make a genuinely honest, realistic multi-role letter
discriminate instead of defaulting almost everything to soft
"can't check" — and the one real fabrication reads as a real fabrication.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

# Sanitized: real name/email/phone/address removed. Company, role, and
# achievement text kept near-verbatim (coordinator-approved) — this is what
# actually exposed the bugs; a paraphrased fixture would risk re-hiding them.
PROFILE = {
    "personal_info": {"name": "Test Candidate", "location": "Frankfurt, Germany"},
    "professional_summary": {
        "en": (
            "I'm an engineering and architecture leader who builds — and "
            "leads teams that build — AI-driven systems people can "
            "actually trust. For several years I've led teams at Nordvance: "
            "currently as Associate Director for the end-to-end "
            "supply-chain systems behind individualized cancer therapies, "
            "and before that heading an architecture team in regulated "
            "GxP environments. My focus is increasingly on AI — and it's "
            "hands-on, not just strategic."
        )
    },
    "work_experience": [
        {
            "id": "w-e2e",
            "company": "Nordvance SE",
            "role": "Associate Director E2E Supply Chain Systems",
            "is_current": True,
            "start_date": "2024-12",
            "end_date": None,
            "responsibilities": [
                "Lead cross-functional teams establishing cross domain "
                "data flows for clinical orders.",
                "Lead a team of five tech leads and system owners "
                "responsible for the IT systems behind Nordvance's "
                "individualized cancer therapies",
            ],
            "achievements": [
                "Initiated and led an agentic GenAI system that automates "
                "the authoring and review of computer system validation "
                "(CSV) documentation, built with LangGraph/LangChain and "
                "RAG over gold-standard documents, targeting an estimated "
                "70% reduction in manual effort.",
            ],
        },
        {
            "id": "w-quality-architecture",
            "company": "Nordvance SE",
            "role": "Associate Director Digital Solutions Quality Systems "
            "Architecture",
            "is_current": False,
            "start_date": "2021-05",
            "end_date": "2024-11",
            "responsibilities": [
                "Provide strategic guidance and mentorship to the "
                "architecture team, cultivating a culture of innovation "
                "and collaboration.",
            ],
        },
        {
            "id": "w-applire",
            "company": "Vaultwright",
            "role": "Founder & Lead Developer",
            "is_current": True,
            "start_date": "2026-05",
            "end_date": None,
            "responsibilities": [
                "Build an open-source, AI-powered platform that automates "
                "the creation of tailored job-application documents for "
                "the DACH market.",
            ],
        },
    ],
}

# The letter's actual paragraph shapes (sentence/clause boundaries matter for
# reproducing the anchor-continuation and clause-decomposition mechanisms).
LETTER = {
    "body": {
        "paragraphs": [
            "I am excited to apply for the Lead AI Engineer role at "
            "Connect-AI.",
            "At Nordvance, I led a team to develop an agentic GenAI system "
            "using LangGraph/LangChain to automate computer system "
            "validation (CSV) documentation, targeting a 70% reduction in "
            "manual effort. This initiative demonstrates my hands-on "
            "experience in production AI and cross-functional "
            "collaboration. Additionally, as Founder of Vaultwright, I "
            "created an open-source AI platform for tailored "
            "job-application documents.",
            # The run-4 fabrication: a figure borrowed from the CURRENT
            # role's "team of five tech leads" fact, re-contextualized as
            # a "mentoring" claim about a different, unnamed context.
            "My track record in technical leadership, mentoring teams of "
            "5+, and owning end-to-end production systems positions me "
            "well for this role.",
            # Boilerplate the #242 filter did not originally cover.
            "I am available to discuss my notice period and would "
            "welcome the opportunity to explore how my skills can "
            "support your team.",
            "I look forward to the possibility of contributing to your "
            "organization.",
        ]
    }
}


@pytest.mark.asyncio
async def test_run4_letter_report_is_not_unverifiable_dominated():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    assert report.counts["unverifiable"] <= sum(
        v for k, v in report.counts.items() if k != "unverifiable"
    ), report.counts
    assert report.unverifiable_dominated is False, report.counts


@pytest.mark.asyncio
async def test_run4_borrowed_figure_gets_a_negative_verdict_not_unverifiable():
    """The exact run-4 fabrication: a "5" borrowed from an unrelated CURRENT
    role's fact, wearing a "mentoring" claim no vault evidence supports."""
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    hits = [r for r in report.claims if "5+" in r.claim.text]
    assert hits, "expected a decomposed claim carrying the '5+' figure"
    for r in hits:
        assert r.verdict.verdict != "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.verdict in ("misattributed", "unbacked"), (
            r.claim.text,
            r.verdict,
        )


@pytest.mark.asyncio
async def test_run4_honest_nordpharm_paragraph_grounds_at_clause_granularity():
    """Over-drop guard: the genuinely truthful Nordvance paragraph must not
    collapse into unverifiable just because the fabrication next to it is
    now caught — most of its clauses should ground."""
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    paragraph_results = [
        r for r in report.claims if r.claim.location.startswith("body.paragraphs[1]")
    ]
    assert paragraph_results
    grounded = [r for r in paragraph_results if r.verdict.verdict == "grounded"]
    assert len(grounded) >= len(paragraph_results) // 2, [
        (r.claim.text, r.verdict.verdict) for r in paragraph_results
    ]


@pytest.mark.asyncio
async def test_run4_pure_courtesy_closer_is_not_extracted_as_a_claim():
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    texts = [r.claim.text.lower() for r in report.claims]
    assert not any("contributing to your organization" in t for t in texts), texts
