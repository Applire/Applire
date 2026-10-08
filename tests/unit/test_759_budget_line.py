# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""#759, founder ruling E5-2 (2026-10-08): a role's ``budget_managed`` line
("Budget: up to €2M" in the German CV 1680cd28) rides the #724 language preseed
like the industry line — the FIRST figure field that passes through an LLM
translation. The control is deterministic: the multiset of figures before and
after translation must be identical (and a unit still stated), otherwise the
vault original renders and the refusal is receipted (``LANGUAGE_PRESEED_FIGURE_KEPT``).
The Oracle grades the line like a bullet of its role.

Synthetic strings only, checked against the detectors before use.
"""
from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

WORK = "dc114c0a-4da5-405f-8caf-ea279f6978fd"
EN_BUDGET = "up to €2M"
DE_BUDGET = "bis zu 2 Mio. €"
EN_BULLET = "Led creative work on digital campaigns for electronics and fashion clients."
DE_BULLET = "Leitete die kreative Arbeit an digitalen Kampagnen für Elektronik- und Modekunden."


def _profile(*, english=True, budget=EN_BUDGET) -> dict:
    return {
        "contact": {"full_name": "Testperson", "email": "kontakt@applire.de"},
        "work_experience": [{
            "id": WORK, "company": "TWENTYONE Digital", "role": "Art Director",
            "start_date": "2018-08", "end_date": "2022-04",
            "budget_managed": budget,
            "responsibilities": [EN_BULLET if english else DE_BULLET],
            "achievements": [],
        }],
        "projects": [], "education": [], "languages": [], "skills": [], "certifications": [],
    }


def _prose() -> dict:
    return {"summary": "Art Director mit Agenturerfahrung.", "skills": [],
            "work": [{"id": WORK, "bullets": [DE_BULLET], "projects": []}], "projects": []}


def _plan(profile, language="de"):
    from applire.services.cv import _plan_language_preseed

    return _plan_language_preseed(
        _prose(), profile, keyword_ledger=[], budget=None, job_dict={},
        document_language=language,
    )


def _translated(draft: dict, budget_text: str) -> dict:
    out = copy.deepcopy(draft)
    for e in out["work"]:
        if "budget_managed" in e:
            e["budget_managed"] = budget_text
    return out


# --- the figure check: a fact comparison -------------------------------------

@pytest.mark.parametrize("original,translated,ok", [
    ("up to €2M", "bis zu 2 Mio. €", True),
    ("€1.5M annually", "1,5 Mio. € jährlich", True),
    ("€500,000", "500.000 €", True),
    ("up to €2M", "bis zu 3 Mio. €", False),
    ("up to €2M", "bis zu 2.000.000 €", False),
    ("€2M across 3 brands", "2 Mio. € über drei Marken", False),
    ("€2M across 3 brands", "2 Mio. € über 3 Marken", True),
])
def test_figures_preserved_is_a_multiset_comparison(original, translated, ok):
    from applire.services.cv import _figures_preserved

    assert _figures_preserved(original, translated) is ok


# --- the preseed places the line, gated like the industry line ----------------

def test_cross_language_budget_line_is_placed_in_front_of_the_language_pass():
    draft, plan = _plan(_profile())
    assert plan.budget_managed == {WORK: EN_BUDGET}
    assert draft["work"][0]["budget_managed"] == EN_BUDGET


def test_same_language_and_wordless_budget_lines_are_not_placed():
    _d, plan = _plan(_profile(english=False, budget="bis zu 2 Mio. €"))
    assert plan.budget_managed == {}
    _d, plan = _plan(_profile(budget="€2M"))
    assert plan.budget_managed == {}, "a bare figure has no words to translate"
    _d, plan = _plan(_profile(budget="2000000"))
    assert plan.budget_managed == {}, "a unitless value never renders (#382)"


# --- the settle guard: accept only a figure-preserving translation ------------

def test_a_figure_preserving_translation_is_accepted():
    from applire.services.cv import _settle_language_preseed

    draft, plan = _plan(_profile())
    settled = _settle_language_preseed(_translated(draft, DE_BUDGET), plan)
    assert plan.budget_managed[WORK] == DE_BUDGET
    assert "budget_managed" not in settled["work"][0], "furniture never stays in the prose"


@pytest.mark.parametrize("bad", ["bis zu 3 Mio. €", "bis zu 2.000.000 €", "bis zu zwei Millionen"])
def test_a_translation_that_changes_a_figure_keeps_the_vault_original_receipted(bad, caplog):
    from applire.services.cv import _settle_language_preseed

    caplog.set_level(logging.WARNING, logger="applire.services.cv")
    draft, plan = _plan(_profile())
    _settle_language_preseed(_translated(draft, bad), plan)
    assert plan.budget_managed[WORK] == EN_BUDGET
    receipts = [r.getMessage() for r in caplog.records if "LANGUAGE_PRESEED_FIGURE_KEPT" in r.getMessage()]
    assert len(receipts) == 1 and EN_BUDGET in receipts[0] and bad in receipts[0]


def test_a_translation_that_loses_the_unit_keeps_the_vault_original(caplog):
    from applire.services.cv import _settle_language_preseed

    caplog.set_level(logging.WARNING, logger="applire.services.cv")
    draft, plan = _plan(_profile())
    _settle_language_preseed(_translated(draft, "bis zu 2"), plan)
    assert plan.budget_managed[WORK] == EN_BUDGET
    assert any("LANGUAGE_PRESEED_FIGURE_KEPT" in r.getMessage() for r in caplog.records)


# --- the delivered document (seam: _apply_role_facts via _compose_document) ---

def _compose(profile, plan):
    from applire.services.cv import _compose_document

    return _compose_document(
        _prose(), profile, raw_profile_json=profile, keyword_ledger=[], budget=None,
        job_dict={}, language="de", preseed=plan,
    )


def test_the_delivered_role_line_carries_the_accepted_translation():
    from applire.services.cv import _settle_language_preseed

    profile = _profile()
    draft, plan = _plan(profile)
    _settle_language_preseed(_translated(draft, DE_BUDGET), plan)
    doc = _compose(profile, plan)
    assert doc.work_history[0].budget_managed == DE_BUDGET


def test_the_delivered_role_line_carries_the_vault_original_after_a_refusal():
    from applire.services.cv import _settle_language_preseed

    profile = _profile()
    draft, plan = _plan(profile)
    _settle_language_preseed(_translated(draft, "bis zu 3 Mio. €"), plan)
    assert _compose(profile, plan).work_history[0].budget_managed == EN_BUDGET


def test_no_preseed_renders_the_vault_value_unchanged():
    assert _compose(_profile(), None).work_history[0].budget_managed == EN_BUDGET


# --- the Oracle grades the line like a bullet -----------------------------------

def test_the_budget_line_is_an_oracle_claim_of_its_role():
    from applire.services.oracle.extract import extract_claims_from_tailored

    claims = extract_claims_from_tailored({"work_history": [
        {"id": WORK, "bullets": [DE_BULLET], "budget_managed": DE_BUDGET},
    ]})
    budget_claims = [c for c in claims if c.location == "work_history[0].budget_managed"]
    assert len(budget_claims) == 1
    assert budget_claims[0].text == DE_BUDGET and budget_claims[0].kind == "bullet"
    assert budget_claims[0].source_experience_id == WORK


@pytest.mark.asyncio
async def test_a_verbatim_budget_line_grounds_against_the_vault():
    from applire.services.oracle.selfaudit import build_self_audit_report

    report = await build_self_audit_report(
        _profile(), tailored_data={"contact": {"name": "T"}, "work_history": [{
            "id": WORK, "company": "TWENTYONE Digital", "role": "Art Director",
            "bullets": [EN_BULLET], "budget_managed": EN_BUDGET,
        }]}, document_language="en",
    )
    row = [c for c in report["claims"] if c["claim"]["location"] == "work_history[0].budget_managed"]
    assert len(row) == 1
    assert row[0]["verdict"]["verdict"] == "grounded", row[0]["verdict"]
