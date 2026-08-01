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
   ADR-068 amendment (2026-08-01): the coverage number alone no longer
   decides ``unbacked`` — below the floor is now a RESTATEMENT-JUDGEMENT
   candidate (Seam B); the escalation still happens, but only behind a
   citation-verified model judgement, and degrades to ``unverifiable``
   (counted, logged) when no provider is available — see this file's
   ``test_run4_borrowed_figure_without_a_provider_is_the_adr068_failsafe``.
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

import re

import pytest

from applire.services.oracle import audit_document

# ADR-068 (2026-08-01) amendment: ``_owner_scoped_coverage <
# _UNATTRIBUTABLE_CONTENT_FLOOR`` (root cause 4 below) no longer decides
# ``unbacked`` on the coverage number alone — it defers to the bounded
# RESTATEMENT-JUDGEMENT seam, batched once per document (Seam B,
# ``services/oracle/audit.py``). This stub answers that seam deterministically
# (never the pre-existing narrow entailment call, which carries no "ITEM n
# (mode: ...)" structure and is left to its own pre-existing fallback): it
# denies restatement, citing the FIRST vault-evidence span verbatim, so the
# run-4 fabrication still escalates to a real negative verdict — proving the
# citation-gated judgement path, not just the old coverage heuristic, catches
# it.
_ITEM_RE = re.compile(
    r"ITEM (\d+) \(mode: \w+\):.*?VAULT EVIDENCE:\n(.*?)(?=\n\nITEM \d+|\Z)", re.DOTALL
)
_EVIDENCE_LINE_RE = re.compile(r"^\s*\[\d+\] (.+)$", re.MULTILINE)


class _DenyRestatementProvider:
    async def aparse_json(self, prompt, *, system=None, **kwargs):
        items = []
        for idx_str, evidence_block in _ITEM_RE.findall(prompt):
            lines = _EVIDENCE_LINE_RE.findall(evidence_block)
            items.append(
                {"index": int(idx_str), "corresponds": False, "vault_quote": lines[0] if lines else ""}
            )
        return {"items": items}

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
            "supply-chain systems behind specialty biologics, "
            "and before that heading an architecture team in regulated "
            "GxP environments. My focus is increasingly on AI — and it's "
            "hands-on, not just strategic."
        )
    },
    "work_experience": [
        {
            "id": "w-e2e",
            "company": "Nordvance SE",
            "role": "Associate Director Platform Systems",
            "is_current": True,
            "start_date": "2024-12",
            "end_date": None,
            "responsibilities": [
                "Lead cross-functional teams establishing cross domain "
                "data flows for clinical orders.",
                "Lead a team of five tech leads and system owners "
                "responsible for the IT systems behind Nordvance's "
                "specialty biologics",
            ],
            "achievements": [
                "Initiated and led an agentic GenAI system that automates "
                "the authoring and review of regulatory submission "
                "documentation, built with LangGraph/LangChain and "
                "RAG over reference documents, targeting an estimated "
                "70% reduction in manual effort.",
            ],
        },
        {
            "id": "w-quality-architecture",
            "company": "Nordvance SE",
            "role": "Associate Director Digital Quality Systems "
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
            "using LangGraph/LangChain to automate regulatory submission "
            "documentation, targeting a 70% reduction in "
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
    """With the judgement layer available (a provider present), the report
    still discriminates — the ADR-068 amendment does not resurrect the
    original F14 camouflage."""
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_DenyRestatementProvider()
    )
    assert report.counts["unverifiable"] <= sum(
        v for k, v in report.counts.items() if k != "unverifiable"
    ), report.counts
    assert report.unverifiable_dominated is False, report.counts


@pytest.mark.asyncio
async def test_run4_borrowed_figure_gets_a_negative_verdict_not_unverifiable():
    """The exact run-4 fabrication: a "5" borrowed from an unrelated CURRENT
    role's fact, wearing a "mentoring" claim no vault evidence supports.

    ADR-068 amendment (2026-08-01): the coverage-floor branch that used to
    decide ``unbacked`` directly now defers to the restatement-judgement seam
    (Seam B) — a citation-verified ``corresponds=false`` answer is what
    escalates it, not the coverage number alone. This test proves that path
    still catches the fabrication when a provider is available."""
    report = await audit_document(
        "cover_letter", PROFILE, letter_data=LETTER, provider=_DenyRestatementProvider()
    )
    hits = [r for r in report.claims if "5+" in r.claim.text]
    assert hits, "expected a decomposed claim carrying the '5+' figure"
    for r in hits:
        assert r.verdict.verdict != "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.verdict in ("misattributed", "unbacked"), (
            r.claim.text,
            r.verdict,
        )


@pytest.mark.asyncio
async def test_run4_borrowed_figure_without_a_provider_is_the_adr068_failsafe():
    """ADR-068 clause 3 polarity, pinned against this exact fixture: WITHOUT a
    judgement provider (the generation-time self-audit's common case, or any
    caller that opts out), the same fabrication can no longer be accused on
    the coverage number alone — it degrades to ``unverifiable``
    (``restatement_judgement``), counted in ``judgement_unavailable``, never
    silently reverting to the pre-ADR-068 ``unbacked``. This is a deliberate,
    documented behaviour change from the run-4 fix's original shape (see this
    module's docstring root cause 4) — not a regression."""
    report = await audit_document("cover_letter", PROFILE, letter_data=LETTER)
    hits = [r for r in report.claims if "5+" in r.claim.text]
    assert hits, "expected a decomposed claim carrying the '5+' figure"
    for r in hits:
        assert r.verdict.verdict == "unverifiable", (r.claim.text, r.verdict)
        assert r.verdict.checker == "restatement_judgement", (r.claim.text, r.verdict)
    assert report.judgement_unavailable >= len(hits), report.judgement_unavailable


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
