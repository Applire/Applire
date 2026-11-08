# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 — 790:90 skill-equivalence seam pool.

The new same-language seam hands the judge EVERY claimable non-personal vault unit.
A bullet the candidate answered "Nein" to on "Ist das erreicht?" (RULING B-1,
`WorkEntry.not_yet_reached`) stays in `responsibilities` and so in that pool. The
ledger's own backing check refuses such a span (D-12, `_verified_backing_path`
`exclude=aims`: "no span of a not_yet_reached line backs anything"); this seam does
not, so one "yes" from the judge, quoting the AIM verbatim, grounds a CV skill the
candidate has said they have not reached. Scripted judge, no network.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

AIM = "Ziel: Aufbau eines Lieferkettenmanagements für beide Werke bis 2027."
PROFILE = {
    "personal_info": {"name": "Jonas Beispiel"},
    "skills": [{"name": "SAP PP", "category": "technical"}],
    "work_experience": [{
        "id": "w1", "company": "Beispiel Kunststoff GmbH", "role": "Produktionsleiter",
        "responsibilities": ["Führt zwei Fertigungsbereiche mit 38 Mitarbeitenden.", AIM],
        "not_yet_reached": [AIM],
    }],
}


class _YesJudge:
    def __init__(self):
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.prompts.append(prompt)
        return {"items": [{"index": 0, "corresponds": True, "vault_quote": "Aufbau eines Lieferkettenmanagements"}]}


@pytest.mark.asyncio
async def test_an_aim_never_grounds_a_cv_skill_through_the_equivalence_seam():
    judge = _YesJudge()
    report = await audit_document(
        "cv", PROFILE, tailored_data={"skills": ["Supply-Chain-Management"]},
        provider=judge, document_language="de",
    )
    (r,) = [c for c in report.claims if c.claim.kind == "skill" and c.claim.text == "Supply-Chain-Management"]
    assert not any(AIM in p for p in judge.prompts), "the not-yet-reached bullet was offered as evidence"
    assert r.verdict.verdict != "grounded", r.verdict
