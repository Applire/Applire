# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243/US245 — Oracle aggregator: verdicts, the 2026-07-18 regression, and
the ADR-052 §2 rule that entailment can never overrule a deterministic red flag.
"""
import pytest

from applire.schemas.oracle import ORACLE_STATED_LIMIT, Claim
from applire.services.oracle import audit_document, verify_claim
from applire.services.oracle.audit import _EntailmentBudget

from .test_oracle_matchers import PROFILE


class _EagerProvider:
    """Claims everything is grounded — must never flip a deterministic red flag."""

    def __init__(self, verdict: str = "grounded"):
        self.calls: list[str] = []
        self._verdict = verdict

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"verdict": self._verdict}


# ── the 2026-07-18 bug class #1 regression (sanitized) ───────────────────────

@pytest.mark.asyncio
async def test_target_rendered_as_achieved_is_inflated():
    """Vault: "targets a ~70% reduction" → document: "reduced by 70%" = inflated."""
    verdict = await verify_claim(
        Claim(text="Reduced manual effort by 70% via a compliance workflow.", location="summary[0]"),
        PROFILE,
    )
    assert verdict.verdict == "inflated"
    assert verdict.checker == "stance"
    refs = {(e.kind, e.ref) for e in verdict.evidence}
    assert ("profile_path", "work_experience[0].achievements[0]") in refs
    assert ("enrichment_record", "rec-1") in refs  # ADR-046 receipt


@pytest.mark.asyncio
async def test_regression_fixture_with_verbatim_typographic_apostrophe():
    """Real-model text uses U+2019 (’) — normalization must not mask the flag."""
    verdict = await verify_claim(
        Claim(text="Reduced the team’s manual effort by 70%.", location="summary[0]"),
        PROFILE,
    )
    assert verdict.verdict == "inflated"


@pytest.mark.asyncio
async def test_achieved_evidence_keeps_achieved_claim_grounded():
    verdict = await verify_claim(
        Claim(text="Cut deployment time by 40%.", location="work_history[0].bullets[0]", kind="bullet"),
        PROFILE,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "numbers"


# ── adversarial review 2026-07-18 MAJOR-1: figure-free inflation ─────────────

@pytest.mark.asyncio
async def test_figure_free_achieved_claim_on_aspirational_evidence_is_inflated():
    """The writer dropped the numeral — the stance red flag must still fire on
    the grounding path (US245 has no figure restriction), instead of endorsing
    the inflation with the aspirational unit as evidence."""
    verdict = await verify_claim(
        Claim(
            text="Delivered the compliance workflow and its reduction in manual effort.",
            location="summary[0]",
        ),
        PROFILE,
    )
    assert verdict.verdict == "inflated"
    assert verdict.checker == "stance"
    refs = {e.ref for e in verdict.evidence if e.kind == "profile_path"}
    assert "work_experience[0].achievements[0]" in refs


@pytest.mark.asyncio
async def test_figure_free_achieved_claim_on_achieved_evidence_stays_grounded():
    """Control: achieved vault evidence keeps an achieved figure-free claim green."""
    verdict = await verify_claim(
        Claim(
            text="Delivered the CI automation and cut deployment time.",
            location="summary[0]",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"


# ── adversarial review 2026-07-18 MINOR-1: entailment evidence context ───────

@pytest.mark.asyncio
async def test_entailment_context_includes_grounding_units_not_just_figure_units():
    """A year-matched claim must hand the entailment the role/org evidence that
    decides it, not only the bare date-span unit that carried the figure."""
    provider = _EagerProvider("grounded")
    verdict = await verify_claim(
        Claim(text="Head of IT at Acme GmbH since 2019", location="summary[0]"),
        PROFILE,
        provider,
    )
    assert verdict.checker == "entailment"
    assert len(provider.calls) == 1
    prompt = provider.calls[0].casefold()
    assert "head of it" in prompt  # grounding unit, not a figure carrier


# ── number provenance red flag ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatched_figure_is_unbacked():
    verdict = await verify_claim(
        Claim(text="Improved customer satisfaction by 55%.", location="summary[0]"),
        PROFILE,
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"
    assert "55%" in (verdict.detail or "")


# ── ADR-052 §2: entailment can NEVER overrule a deterministic red flag ───────

@pytest.mark.asyncio
async def test_eager_entailment_cannot_overrule_unbacked_figure():
    provider = _EagerProvider("grounded")
    verdict = await verify_claim(
        Claim(text="Improved customer satisfaction by 55%.", location="summary[0]"),
        PROFILE,
        provider,
    )
    assert verdict.verdict == "unbacked"
    assert provider.calls == []  # structurally unreachable, not just outvoted


@pytest.mark.asyncio
async def test_eager_entailment_cannot_overrule_inflation():
    provider = _EagerProvider("grounded")
    verdict = await verify_claim(
        Claim(text="Reduced manual effort by 70%.", location="summary[0]"),
        PROFILE,
        provider,
    )
    assert verdict.verdict == "inflated"
    assert provider.calls == []


# ── entailment where deterministic checks cannot decide ─────────────────────

@pytest.mark.asyncio
async def test_undecided_claim_uses_entailment_when_provider_present():
    provider = _EagerProvider("grounded")
    verdict = await verify_claim(
        Claim(text="Owned vendor negotiations across three continents.", location="summary[0]"),
        PROFILE,
        provider,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "entailment"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_undecided_claim_without_provider_is_unverifiable():
    verdict = await verify_claim(
        Claim(text="Owned vendor negotiations across three continents.", location="summary[0]"),
        PROFILE,
    )
    assert verdict.verdict == "unverifiable"
    assert verdict.checker == "grounding"


@pytest.mark.asyncio
async def test_entailment_budget_is_hard_capped():
    provider = _EagerProvider("grounded")
    budget = _EntailmentBudget(limit=1)
    claim = Claim(text="Owned vendor negotiations across three continents.", location="summary[0]")
    first = await verify_claim(claim, PROFILE, provider, budget=budget)
    second = await verify_claim(claim, PROFILE, provider, budget=budget)
    assert first.checker == "entailment"
    assert second.checker == "grounding"  # deterministic fallback, no extra call
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_invalid_entailment_output_degrades_to_deterministic_fallback():
    class _Garbage:
        async def aparse_json(self, prompt, **kwargs):
            return {"verdict": "totally-fine-bro"}

    verdict = await verify_claim(
        Claim(text="Owned vendor negotiations across three continents.", location="summary[0]"),
        PROFILE,
        _Garbage(),
    )
    assert verdict.verdict == "unverifiable"
    assert verdict.checker == "grounding"


# ── skills ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skill_claims_grounded_and_unbacked():
    grounded = await verify_claim(
        Claim(text="Python", location="skills[0]", kind="skill"), PROFILE
    )
    unbacked = await verify_claim(
        Claim(text="React Native", location="skills[1]", kind="skill"), PROFILE
    )
    assert grounded.verdict == "grounded"
    assert unbacked.verdict == "unbacked"


# ── audit_document (report shape, US243/US246 contract) ─────────────────────

TAILORED = {
    "contact": {"name": "Anna Bauer"},
    "summary": "Reduced manual effort by 70% via a compliance workflow.",
    "work_history": [
        {
            "id": "w1",
            "company": "Acme GmbH",
            "role": "Head of IT",
            "bullets": ["Cut deployment time by 40%.", "Improved satisfaction by 55%."],
            "projects": [],
        }
    ],
    "projects": [],
    "skills": ["Python", "React Native"],
}


@pytest.mark.asyncio
async def test_audit_document_report_counts_and_stated_limit():
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED)
    assert report.document_kind == "cv"
    assert report.stated_limit == ORACLE_STATED_LIMIT
    by_loc = {r.claim.location: r.verdict.verdict for r in report.claims}
    assert by_loc["summary[0]"] == "inflated"
    assert by_loc["work_history[0].bullets[0]"] == "grounded"
    assert by_loc["work_history[0].bullets[1]"] == "unbacked"
    assert by_loc["skills[0]"] == "grounded"
    assert by_loc["skills[1]"] == "unbacked"
    assert report.counts["inflated"] == 1
    assert report.counts["unbacked"] == 2
    assert sum(report.counts.values()) == len(report.claims)
    # JSONB-safe serialization (model_dump(mode="json") rule)
    dumped = report.model_dump(mode="json")
    assert isinstance(dumped["generated_at"], str)


@pytest.mark.asyncio
async def test_audit_document_requires_exactly_one_source():
    with pytest.raises(ValueError):
        await audit_document("cv", PROFILE)
    with pytest.raises(ValueError):
        await audit_document("cv", PROFILE, tailored_data={}, text="x")


@pytest.mark.asyncio
async def test_audit_document_external_text_no_prior_generation():
    """US248 à-la-carte: raw external text audits against the vault alone."""
    text = "- Reduced manual effort by 70%\n- Cut deployment time by 40%\n"
    report = await audit_document("external", PROFILE, text=text)
    verdicts = [r.verdict.verdict for r in report.claims]
    assert verdicts == ["inflated", "grounded"]
