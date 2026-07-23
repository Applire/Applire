# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Oracle v2 role attribution (#196, ADR-052 §6) — ``misattributed`` verdict.

A claim rendered under position B whose only backing evidence belongs to
position A is mechanically detectable: tailored work entries carry the source
``WorkEntry.id`` and vault units know which experience owns them. Deterministic
like every other red flag — entailment can never overrule it.
"""
import pytest

from applire.schemas.oracle import Claim, TruthfulnessReport
from applire.services.oracle import audit_document, verify_claim
from applire.services.oracle.extract import extract_claims_from_tailored
from applire.services.oracle.matchers import build_vault_index


# Two distinct positions: the FDA programme belongs to the OLD role — the #196
# bug class rendered it under the current one.
PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": "IT leader driving compliance automation in regulated industries."
    },
    "work_experience": [
        {
            "id": "w-old",
            "company": "Acme GmbH",
            "role": "QA Lead",
            "start_date": "2021-01",
            "end_date": "2024-06",
            "achievements": [
                "Led the FDA audit preparation programme for the Hamburg site.",
                "Migrated 200 users to the new document platform.",
            ],
        },
        {
            "id": "w-new",
            "company": "Beta AG",
            "role": "Head of IT",
            "start_date": "2024-07",
            "end_date": None,
            "achievements": ["Built the observability stack for the platform group."],
            "technologies": ["Python"],
        },
    ],
    "projects": [
        {
            "id": "p1",
            "name": "Side Tool",
            "role": "Maintainer",
            "achievements": ["Shipped the reporting side tool."],
        },
        # Associated with the current position by id — US187 nests its bullets
        # under w-new, so its evidence must clear claims rendered there.
        {
            "id": "p2",
            "name": "Observability Rollout",
            "role": "Lead",
            "associated_experience": "w-new",
            "achievements": ["Rolled out distributed tracing for the checkout services."],
        },
        # Associated by company NAME (the CV-extraction path spelling).
        {
            "id": "p3",
            "name": "Audit Tooling",
            "role": "Lead",
            "associated_experience": "Acme GmbH",
            "achievements": ["Automated the audit evidence collection tooling."],
        },
    ],
    "skills": [{"name": "Python"}],
}


class _EagerProvider:
    """Claims everything is grounded — must never flip a deterministic red flag."""

    def __init__(self):
        self.calls: list[str] = []

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"verdict": "grounded"}


# ── extraction stamps the rendered position ──────────────────────────────────

def test_extract_stamps_source_experience_id():
    tailored = {
        "summary": "IT leader driving compliance automation.",
        "work_history": [
            {
                "id": "w-new",
                "company": "Beta AG",
                "role": "Head of IT",
                "bullets": ["Led the FDA audit preparation programme."],
                "projects": [{"name": "P", "bullets": ["Shipped the nested tool."]}],
            },
            # Legacy/mock entries carry no id — must stamp None, never "".
            {"id": "", "company": "Old Corp", "role": "Dev", "bullets": ["Legacy bullet here."]},
        ],
        "projects": [{"name": "Q", "bullets": ["Standalone project bullet."]}],
        "skills": ["Python"],
    }
    claims = {c.location: c for c in extract_claims_from_tailored(tailored)}

    assert claims["work_history[0].bullets[0]"].source_experience_id == "w-new"
    assert claims["work_history[0].projects[0].bullets[0]"].source_experience_id == "w-new"
    assert claims["work_history[1].bullets[0]"].source_experience_id is None
    assert claims["summary[0]"].source_experience_id is None
    assert claims["projects[0].bullets[0]"].source_experience_id is None
    assert claims["skills[0]"].source_experience_id is None


# ── vault units know their owning experience ─────────────────────────────────

def test_vault_units_carry_owner_ids():
    index = build_vault_index(PROFILE)
    owners = {u.path: u.owner_ids for u in index.units}

    assert owners["work_experience[0].achievements[0]"] == frozenset({"w-old"})
    assert owners["work_experience[0].role"] == frozenset({"w-old"})
    assert owners["work_experience[1].achievements[0]"] == frozenset({"w-new"})
    # A standalone project owns only itself.
    assert owners["projects[0].achievements[0]"] == frozenset({"p1"})
    # An associated project ALSO belongs to its parent position — US187 nests
    # its bullets under the work entry, so the parent id must clear them.
    assert owners["projects[1].achievements[0]"] == frozenset({"p2", "w-new"})
    # Association by company name resolves like _nest_projects does.
    assert owners["projects[2].achievements[0]"] == frozenset({"p3", "w-old"})
    # Role-agnostic evidence carries no owner — it can ground any position.
    assert owners["professional_summary.en"] == frozenset()
    assert owners["skills[0]"] == frozenset()
    # All known experience ids, for source-id validation (fail open on garbage).
    assert {"w-old", "w-new", "p1", "p2", "p3"} <= set(index.experience_ids)


# ── the verdict ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_role_claim_is_misattributed():
    """The #196 bug: an old-role achievement rendered under the current role."""
    verdict = await verify_claim(
        Claim(
            text="Led the FDA audit preparation programme for the Hamburg site.",
            location="work_history[0].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"
    refs = {e.ref for e in verdict.evidence if e.kind == "profile_path"}
    assert "work_experience[0].achievements[0]" in refs


@pytest.mark.asyncio
async def test_same_role_claim_stays_grounded():
    verdict = await verify_claim(
        Claim(
            text="Led the FDA audit preparation programme for the Hamburg site.",
            location="work_history[0].bullets[0]",
            kind="bullet",
            source_experience_id="w-old",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_claim_without_source_id_is_never_misattributed():
    """Legacy tailored data (no ids) fails open to v1 behaviour."""
    verdict = await verify_claim(
        Claim(
            text="Led the FDA audit preparation programme for the Hamburg site.",
            location="work_history[0].bullets[0]",
            kind="bullet",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_owner_neutral_evidence_does_not_flag():
    """A claim grounded by role-agnostic evidence (summary) is fine anywhere."""
    verdict = await verify_claim(
        Claim(
            text="IT leader driving compliance automation.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_foreign_role_figure_is_misattributed_and_beats_entailment():
    """Figure evidence living only in another position is the same defect —
    and as a deterministic red flag it must return before entailment fires."""
    provider = _EagerProvider()
    verdict = await verify_claim(
        Claim(
            text="Migration of 200 users to the new document platform.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
        provider,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"
    assert provider.calls == []


# ── report shape ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_counts_include_misattributed():
    tailored = {
        "summary": "",
        "work_history": [
            {
                "id": "w-new",
                "company": "Beta AG",
                "role": "Head of IT",
                "bullets": [
                    "Led the FDA audit preparation programme for the Hamburg site.",
                    "Built the observability stack for the platform group.",
                ],
                "projects": [],
            }
        ],
        "projects": [],
        "skills": [],
    }
    report = await audit_document("cv", PROFILE, tailored_data=tailored)
    by_loc = {r.claim.location: r.verdict.verdict for r in report.claims}
    assert by_loc["work_history[0].bullets[0]"] == "misattributed"
    assert by_loc["work_history[0].bullets[1]"] == "grounded"
    assert report.counts["misattributed"] == 1
    assert set(report.counts) == {
        "grounded", "inflated", "unbacked", "unverifiable", "misattributed",
    }
    assert sum(report.counts.values()) == len(report.claims)


def test_empty_report_carries_all_five_count_keys():
    report = TruthfulnessReport.from_results("cv", [])
    assert report.counts["misattributed"] == 0
    assert len(report.counts) == 5


# ── adversarial review 2026-07-19 — the four confirmed defects ───────────────

@pytest.mark.asyncio
async def test_nested_project_bullet_under_parent_role_stays_grounded():
    """US187 nests an associated project's achievements under its parent work
    entry, stamped with the WORK id — project-owned evidence must clear it."""
    verdict = await verify_claim(
        Claim(
            text="Rolled out distributed tracing for the checkout services.",
            location="work_history[1].projects[0].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_same_role_backing_outside_top3_clears_the_claim():
    """The qualifying set is every unit clearing the floor, NOT the top-3
    entailment window — a same-role unit ranked 4th by tie-break must clear."""
    profile = {
        "personal_info": {"name": "Anna Bauer"},
        "work_experience": [
            {
                "id": "w-old",
                "company": "Acme GmbH",
                "role": "QA Lead",
                # Three identical foreign units out-rank (by vault order) ...
                "responsibilities": ["Managed the enterprise resource planning migration."],
                "achievements": [
                    "Managed the enterprise resource planning migration.",
                    "Managed the enterprise resource planning migration.",
                ],
            },
            {
                "id": "w-new",
                "company": "Beta AG",
                "role": "Head of IT",
                # ... the identical same-role unit sitting at rank 4.
                "achievements": ["Managed the enterprise resource planning migration."],
            },
        ],
    }
    verdict = await verify_claim(
        Claim(
            text="Managed the enterprise resource planning migration.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        profile,
    )
    assert verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_same_role_year_cannot_launder_a_foreign_figure():
    """Per-figure attribution: an ambient year matched by the rendered role's
    own date span must not clear a load-bearing figure that traces only to
    another position."""
    verdict = await verify_claim(
        Claim(
            text="Migration of 200 users completed in 2024.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"


@pytest.mark.asyncio
async def test_foreign_year_alone_does_not_flag():
    """Years are tenure-ambient (date spans, 'since 2021' phrasing) — a year
    figure whose only vault occurrence is another position's dates must not
    produce a misattribution red flag by itself."""
    verdict = await verify_claim(
        Claim(
            text="Supporting compliance systems at the company since 2021.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
    )
    assert verdict.verdict != "misattributed"


@pytest.mark.asyncio
async def test_entailment_grounded_on_exclusively_foreign_evidence_is_downgraded():
    """A paraphrased cross-role claim below the coverage floor reaches
    entailment; a 'grounded' answer backed exclusively by a foreign position's
    evidence is still misattribution — determinism outranks the LLM."""
    provider = _EagerProvider()
    verdict = await verify_claim(
        Claim(
            text="Led the FDA audit preparation effort for the Hamburg location.",
            location="work_history[1].bullets[0]",
            kind="bullet",
            source_experience_id="w-new",
        ),
        PROFILE,
        provider,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"


# ── #237 signature-story owner_ids (interview evidence gains an owner) ───────

def test_signature_story_units_carry_owner_ids_from_experience_refs():
    """A story anchored to a specific experience via ``experience_refs``
    (US172/ADR-055 provenance pattern) becomes a FOREIGN owner for claims
    rendered under any other position — the F14 fix (#237)."""
    profile = dict(PROFILE)
    profile["signature_stories"] = [
        {
            "title": "Testing culture",
            "challenge": "Flaky releases undermined trust.",
            "mechanism": "Introduced comprehensive test automation.",
            "outcome": "Established reliability practices with full observability.",
            "experience_refs": ["w-old"],
        }
    ]
    index = build_vault_index(profile)
    owners = {u.path: u.owner_ids for u in index.units}
    assert owners["signature_stories[0].outcome"] == frozenset({"w-old"})
    assert owners["signature_stories[0].mechanism"] == frozenset({"w-old"})


def test_signature_story_without_experience_refs_stays_owner_neutral():
    """A job-agnostic story (no experience_refs) still grounds any position —
    only stories anchored to a real experience gain an owner."""
    profile = dict(PROFILE)
    profile["signature_stories"] = [
        {
            "title": "General practice",
            "challenge": "N/A",
            "mechanism": "N/A",
            "outcome": "Consistently applies rigorous testing practices.",
        }
    ]
    index = build_vault_index(profile)
    owners = {u.path: u.owner_ids for u in index.units}
    assert owners["signature_stories[0].outcome"] == frozenset()


@pytest.mark.asyncio
async def test_story_backed_claim_under_foreign_role_is_misattributed():
    """End-to-end: a claim anchored to w-new whose ONLY backing evidence is a
    story owned by w-old is misattributed — exactly the F14 blend shape."""
    profile = dict(PROFILE)
    profile["signature_stories"] = [
        {
            "title": "Testing culture",
            "challenge": "Flaky releases undermined trust.",
            "mechanism": "Introduced comprehensive test automation.",
            "outcome": (
                "Established comprehensive testing, observability, and "
                "reliability practices."
            ),
            "experience_refs": ["w-old"],
        }
    ]
    verdict = await verify_claim(
        Claim(
            text=(
                "Established comprehensive testing, observability, and "
                "reliability practices."
            ),
            location="body.paragraphs[0][0].clauses[1]",
            kind="clause",
            source_experience_id="w-new",
        ),
        profile,
    )
    assert verdict.verdict == "misattributed"
    assert verdict.checker == "attribution"


@pytest.mark.asyncio
async def test_unknown_source_id_fails_open():
    """A stamped id the vault does not know (backfill heuristics, stale data)
    must disable the matcher, never flag."""
    verdict = await verify_claim(
        Claim(
            text="Led the FDA audit preparation programme for the Hamburg site.",
            location="work_history[0].bullets[0]",
            kind="bullet",
            source_experience_id="not-a-known-id",
        ),
        PROFILE,
    )
    assert verdict.verdict == "grounded"
