# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bug #797 / build-2 adversarial F-C (founder ruling R3-b) — German attributive
adjective declension in the shared presence fold.

One-click *Stimmt* returned 409 ``not_in_profile`` on the finding
"Industrieunternehmen" (matched form "industrieller Mittelstand") although the
candidate's own summary says "im industriellen Mittelstand" (adversarial CV
bbe2beff). The closed suffix-pair fold (ADR-048, #783 L35) folds only the FINAL
token; the attributive adjective in front of the noun declines by case and
gender (-e/-en/-er/-es/-em) and never changes what the phrase means. The fold
now reaches that ending, closed to stems with an adjective-forming suffix
(-ell, -isch, -lich, -ig, -al, -iv, -ar, -bar, -haft, -los, -sam) of at least
``_ADJ_FOLD_MIN_STEM`` letters.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import applire.services.review_actions as ra
import applire.services.review_state as rs
from applire.services.ats_audit import (
    _keyword_coverage,
    _norm,
    grounding_vault_index,
    surface_present,
    surface_present_whole_token,
)
from tests.support.profile_factory import set_profile_json
from tests.unit.test_review_endpoints import (  # noqa: F401 — `db` is a fixture
    FakeReaudit,
    _ats_report,
    _client,
    db,
    seed_cv,
)

_SUMMARY = ("Controllerin mit 9 Jahren Erfahrung im industriellen Mittelstand. Zuhause in "
            "Monats- und Jahresabschluss, Planung und Werkscontrolling.")
PROFILE = {
    "personal_info": {"name": "Ada Probe"},
    "professional_summary": {"de": _SUMMARY, "en": None},
    "work_experience": [{
        "id": "11111111-1111-1111-1111-111111111111", "company": "Schwarzwald Präzision GmbH",
        "title": "Financial Controller", "start_date": "2019-02", "end_date": None,
        "industry_context": "industrieller Mittelstand",
        "responsibilities": ["Ich habe den Monatsabschluss nach HGB verantwortet."],
    }],
    "skills": [{"name": "SAP CO", "status": "confirmed"}],
}
_TERM = "Industrieunternehmen"
_KEY = rs.finding_key("ats", _TERM)
_MATCHES = {_TERM: [{"form": "industrieller Mittelstand", "stem": False}]}
_LEDGER = [{"concept": _TERM, "status": "gap", "claimable": False, "sources": ["keyword"],
            "fit_weight": 0.0, "evidence": "",
            "surface_forms": ["Industrieunternehmen", "industrieller Mittelstand", "Industriecontrolling"]}]


@pytest.mark.parametrize("form,text", [
    ("industrieller Mittelstand", "im industriellen mittelstand"),
    ("industriellen Mittelstand", "ein industrieller mittelstand"),
    ("technischer Leiter", "als technischen leiter"),
    ("öffentliche Verwaltung", "in der öffentlichen verwaltung"),
    ("operatives Controlling", "im operativen controlling"),
])
def test_attributive_adjective_declension_folds(form, text):
    assert surface_present(form, text)
    assert surface_present_whole_token(form, text)


@pytest.mark.parametrize("form,text", [
    ("neuer Standort", "am neuen standort"),        # stem too short and no adjective suffix
    ("moderner Arbeitsplatz", "am modernen arbeitsplatz"),  # plain adjective: outside the closed set
    ("Quellen Analyse", "queller analyse"),          # stem "quell" under the minimum length
    ("industrieller Mittelstand", "industrieller konzern"),  # the noun still has to match
    ("Stammdaten manuellen", "stammdaten manueller"),  # the FINAL token keeps its own pairs only
])
def test_the_fold_stays_closed(form, text):
    assert not surface_present(form, text)


def test_support_in_profile_reads_the_declined_summary():
    receipt, unconfirmed = ra.support_in_profile(["industrieller Mittelstand"], PROFILE)
    assert receipt == [{"path": "professional_summary.de", "text": _SUMMARY}] and unconfirmed == []


def test_the_keyword_check_no_longer_flags_the_profiles_own_phrase():
    cov = _keyword_coverage(
        _norm("Neun Jahre Controlling im industrieller Mittelstand."), [_TERM], _LEDGER,
        vault_index=grounding_vault_index(PROFILE), document_language="de",
    )
    assert cov.present == [_TERM]
    assert cov.present_unsupported == []


@pytest.mark.asyncio
async def test_supported_closes_the_declined_finding_in_one_click(db):
    from applire.models.cv import GeneratedCV
    from applire.models.profile import MasterProfile

    cv_id = await seed_cv(db, introduction="Controlling im industriellen Mittelstand.",
                          ats_report=_ats_report("cv", [_TERM], _MATCHES))
    cv = await db.get(GeneratedCV, cv_id)
    profile = await db.get(MasterProfile, cv.profile_id)
    set_profile_json(profile, PROFILE)
    await db.commit()
    client = _client(db)
    with patch.object(ra, "reaudit", new=FakeReaudit([(_ats_report("cv", [], {}), None)])), \
         patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as testimony:
        response = client.post(f"/api/cv/{cv_id}/review/supported", json={"finding_key": _KEY})
    assert response.status_code == 200, response.text
    assert testimony.call_count == 0
    [decision] = response.json()["review_state"]["decisions"]
    assert decision["action"] == "supported"
    assert decision["receipt"][0]["path"] == "professional_summary.de"
