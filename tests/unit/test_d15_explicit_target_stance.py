# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-15 (Hazelnut build 5, delivery run 3): the Oracle raised "Steht als
erreichtes Ergebnis da" on a CV line that already states the aim — "… a 70%
reduction … remains a target, not an achieved result." The stance markers knew
no explicit-target wording ("remains a target", "aiming for", "target:"), and
"achieved" matched inside the negation. Synthetic data only."""

from __future__ import annotations

import asyncio

import pytest

from applire.services.oracle.selfaudit import build_self_audit_report
from applire.services.oracle.stance import classify_stance

VAULT = {
    "personal_info": {"name": "Jana Lehmann"},
    "work_experience": [{
        "id": "w1", "company": "Pharma Nord", "role": "Associate Director", "start_date": "2024-12",
        "responsibilities": ["Lead an agentic GenAI system for validation documents."],
        "achievements": ["The solution targets an estimated 70% reduction in manual effort for authoring and review."],
    }],
    "skills": [],
}


def _verdict(bullet: str) -> str | None:
    cv = {"summary": "Engineering lead.", "skills": [], "education": [], "certifications": [], "languages": [],
          "work_history": [{"id": "w1", "company": "Pharma Nord", "role": "Associate Director",
                            "start_date": "2024-12", "bullets": [bullet]}]}
    report = asyncio.run(build_self_audit_report(VAULT, tailored_data=cv, document_language="en"))
    for c in (report or {}).get("claims", []):
        if "70" in c["claim"]["text"]:
            return c["verdict"].get("detail_key")
    return None


@pytest.mark.parametrize("text", [
    "A 70% reduction in manual authoring and review effort remains a target, not an achieved result.",
    "Aiming for a 70% reduction in manual effort for authoring and review.",
    "Target: 70% less manual effort for authoring and review.",
    "Ziel: 70 % weniger manueller Aufwand bei Erstellung und Prüfung.",
])
def test_explicit_target_wording_reads_aspirational(text):
    assert classify_stance(text) == "aspirational"


def test_a_line_that_states_the_aim_gets_no_inflated_card():
    assert _verdict(
        "A 70% reduction in manual authoring and review effort remains a target, not an achieved result."
    ) != "inflatedAspirational"


def test_negative_control_a_real_inflated_claim_still_gets_the_card():
    assert classify_stance("Reduced manual effort for authoring and review by 70%.") == "achieved"
    assert _verdict("Reduced manual effort for authoring and review by 70%.") == "inflatedAspirational"
