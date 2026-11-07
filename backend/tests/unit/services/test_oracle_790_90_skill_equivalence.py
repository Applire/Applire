# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:90 (ux-audit-3, Marcus) — a SAME-language literal skill miss gets Seam A's
bounded equivalence judgement against the vault's skill units.

The German CV's skill list carried "Lieferkette" (the language pass had translated
the vault's loanword skill "Supply Chain"); Seam A's trigger reads only the vault's
dominant language, so the card said "„Lieferkette“ steht nirgends in Deinem Profil"
and, after the person answered "Ja, und zwar bei …", came back with three chores.
Grant-only: a "no", an uncertain answer, a fabricated citation or no provider keep
exactly today's verdict. Hermetic: targeted stubs, no network.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import audit_document

PROFILE = {
    "personal_info": {"name": "Jonas Beispiel"},
    "professional_summary": {"de": "Produktionsleiter mit zwölf Jahren Erfahrung in der Kunststofffertigung."},
    "skills": [
        {"name": "Supply Chain", "category": "technical"},
        {"name": "Shopfloor-Management", "category": "technical"},
        {"name": "SAP PP", "category": "technical"},
    ],
    "work_experience": [{
        "id": "w1", "company": "Beispiel Kunststoff GmbH", "role": "Produktionsleiter",
        "responsibilities": [
            "Arbeitet als Schnittstelle zu Einkauf, Qualitätssicherung und Supply Chain.",
            "Führt zwei Fertigungsbereiche mit 38 Mitarbeitenden.",
        ],
    }],
}
TAILORED = {"skills": ["Lieferkette", "Shopfloor-Management"]}


class _Judge:
    def __init__(self, answer):
        self.answer = answer
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        self.prompts.append(prompt)
        return {"items": [dict(self.answer, index=0)]}


def _skill(report, text):
    (r,) = [c for c in report.claims if c.claim.kind == "skill" and c.claim.text == text]
    return r


@pytest.mark.asyncio
async def test_a_translated_vault_skill_is_grounded_by_the_judge():
    judge = _Judge({"corresponds": True, "vault_quote": "Supply Chain"})
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=judge, document_language="de")
    r = _skill(report, "Lieferkette")
    assert r.verdict.verdict == "grounded", r.verdict
    assert r.verdict.detail_key == "skillEquivalent"
    assert r.verdict.detail_params == {"skill": "Lieferkette", "quote": "Supply Chain"}
    assert r.verdict.checker == "cross_language_judgement"
    assert len(judge.prompts) == 1 and "Supply Chain" in judge.prompts[0]
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"corresponds": False, "vault_quote": None},
    {"corresponds": "uncertain", "vault_quote": None},
    {"corresponds": True, "vault_quote": "Logistikplanung"},  # not a vault unit
])
async def test_a_no_or_a_fabricated_citation_keeps_todays_verdict(answer):
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=_Judge(answer), document_language="de")
    r = _skill(report, "Lieferkette")
    assert r.verdict.verdict == "unbacked" and r.verdict.detail_key == "skillNoEvidence"


@pytest.mark.asyncio
async def test_no_provider_keeps_todays_verdict():
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=None, document_language="de")
    r = _skill(report, "Lieferkette")
    assert r.verdict.verdict == "unbacked" and r.verdict.detail_key == "skillNoEvidence"


@pytest.mark.asyncio
async def test_a_literal_hit_never_asks_the_judge():
    class _Never:
        async def aparse_json(self, *a, **k):
            raise AssertionError("no judgement for a literal hit")

    report = await audit_document(
        "cv", PROFILE, tailored_data={"skills": ["Shopfloor-Management"]}, provider=_Never(), document_language="de",
    )
    assert _skill(report, "Shopfloor-Management").verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_a_skill_the_candidate_denied_is_never_judged():
    judge = _Judge({"corresponds": True, "vault_quote": "Supply Chain"})
    profile = dict(PROFILE, metadata={"denied_concepts": [
        {"concept": "Lieferkette", "statement": "Lieferkettenplanung habe ich nicht verantwortet.",
         "source": "interview", "date": "2026-11-07"},
    ]})
    report = await audit_document("cv", profile, tailored_data=TAILORED, provider=judge, document_language="de")
    assert _skill(report, "Lieferkette").verdict.verdict == "unbacked"
    assert not any("Lieferkette" in p for p in judge.prompts)


@pytest.mark.asyncio
async def test_the_judge_sees_the_matchers_units_skills_first_never_the_personal_block():
    """The pool mirrors what the deterministic skill matcher reads: "Supply Chain"
    sat in a BULLET of the round-3 vault, not in its skill list."""
    judge = _Judge({"corresponds": False, "vault_quote": None})
    await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=judge, document_language="de")
    (prompt,) = judge.prompts
    assert "Schnittstelle zu Einkauf" in prompt
    assert prompt.index("Shopfloor-Management") < prompt.index("Schnittstelle zu Einkauf")
    assert "Jonas Beispiel" not in prompt


@pytest.mark.asyncio
async def test_a_bullet_backs_the_translated_skill():
    profile = dict(PROFILE, skills=[{"name": "SAP PP"}])
    judge = _Judge({"corresponds": True, "vault_quote": "Schnittstelle zu Einkauf, Qualitätssicherung und Supply Chain"})
    report = await audit_document("cv", profile, tailored_data={"skills": ["Lieferkette"]}, provider=judge, document_language="de")
    assert _skill(report, "Lieferkette").verdict.verdict == "grounded"


@pytest.mark.asyncio
async def test_a_failed_judge_call_degrades_nothing():
    class _Boom:
        async def aparse_json(self, *a, **k):
            raise RuntimeError("provider down")

    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=_Boom(), document_language="de")
    assert _skill(report, "Lieferkette").verdict.detail_key == "skillNoEvidence"
    assert report.judgement_unavailable == 0


@pytest.mark.asyncio
async def test_a_quote_copied_with_the_prompts_evidence_index_still_cites():
    """luna probe 2026-11-07: 2 of 3 grants quoted "[12] Supply Chain" — our own
    "[j] text" rendering. Only the leading index is stripped."""
    judge = _Judge({"corresponds": True, "vault_quote": "[0] Supply Chain"})
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=judge, document_language="de")
    r = _skill(report, "Lieferkette")
    assert r.verdict.verdict == "grounded" and r.verdict.detail_params["quote"] == "Supply Chain"


@pytest.mark.asyncio
async def test_an_index_alone_is_no_citation():
    judge = _Judge({"corresponds": True, "vault_quote": "[0]"})
    report = await audit_document("cv", PROFILE, tailored_data=TAILORED, provider=judge, document_language="de")
    assert _skill(report, "Lieferkette").verdict.verdict == "unbacked"
