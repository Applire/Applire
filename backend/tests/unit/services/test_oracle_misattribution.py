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
        }
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

def test_vault_units_carry_owner_id():
    index = build_vault_index(PROFILE)
    owners = {u.path: u.owner_id for u in index.units}

    assert owners["work_experience[0].achievements[0]"] == "w-old"
    assert owners["work_experience[0].role"] == "w-old"
    assert owners["work_experience[1].achievements[0]"] == "w-new"
    assert owners["projects[0].achievements[0]"] == "p1"
    # Role-agnostic evidence carries no owner — it can ground any position.
    assert owners["professional_summary.en"] is None
    assert owners["skills[0]"] is None


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
