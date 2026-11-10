# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:289 / u6 (Hazelnut build 8, WP-W): ``cv.skill_without_bullet`` — a skills
entry whose claimable concept the owning role's own lines record, and no bullet of
the CV states. Synthetic shapes (invented names); the luna case it is built from is
ux-audit-3 rec 432 ("Mentoring" chip, the mentoring line dropped from its role)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_v2 import ComposeNotes, V2CheckContext, compose_v2, v2_checks  # noqa: E402

W1 = "11111111-1111-4111-8111-111111111111"
W2 = "22222222-2222-4222-8222-222222222222"
COACH = "Coached three junior developers and ran the weekly code-review session."


@pytest.fixture(autouse=True)
def _no_shared_context(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "off")


def _vault() -> dict:
    return {
        "personal_info": {"name": "Jona Petrak", "email": "jona@example.com"},
        "work_experience": [
            {"id": W1, "company": "Torvik Systems GmbH", "role": "Senior Engineer",
             "start_date": "2021-01", "end_date": None,
             "responsibilities": ["Operate the routing backend for 30k daily users.", COACH]},
            {"id": W2, "company": "Halden AG", "role": "Engineer",
             "start_date": "2017-01", "end_date": "2020-12",
             "responsibilities": ["Built REST APIs for an invoicing product."]},
        ],
        "skills": [{"name": "Coaching", "status": "confirmed"}, {"name": "REST APIs", "status": "confirmed"}],
    }


def _ledger() -> list[dict]:
    return [{
        "concept": "Coaching", "surface_forms": ["Coaching", "coaching developers"],
        "claimable": True, "status": "direct", "sources": ["preferred"], "fit_weight": 0.5,
        "evidence": "Coaches three junior developers.",
    }]


def _prose(*, chip: bool = True, bullet: bool = False) -> dict:
    w1 = ["Operated the routing backend for 30k daily users."]
    if bullet:
        w1.append("Coached three junior developers and ran the weekly code-review session.")
    return {
        "summary": "Senior Engineer with a focus on routing backends.",
        "work": [
            {"id": W1, "role": "Senior Engineer", "company": "Torvik Systems GmbH",
             "industry_context": None, "bullets": w1, "projects": []},
            {"id": W2, "role": "Engineer", "company": "Halden AG",
             "industry_context": None, "bullets": ["Built REST APIs for an invoicing product."], "projects": []},
        ],
        "projects": [],
        "skills": (["Coaching"] if chip else []) + ["REST APIs"],
        "education": [], "languages": [],
    }


def _findings(prose, vault=None, ledger=None):
    vault = vault or _vault()
    notes = ComposeNotes()
    composed = compose_v2(prose, vault, notes=notes)
    ctx = V2CheckContext(profile_json=vault, keyword_ledger=_ledger() if ledger is None else ledger,
                         job_dict={}, language="en")
    return {f.check: f for f in v2_checks(prose, composed, notes, ctx)}


def test_a_chip_whose_role_line_no_bullet_states_is_one_blocking_finding():
    f = _findings(_prose())["cv.skill_without_bullet"]
    assert f.blocking
    assert f.subjects == ("Coaching",)
    assert COACH in f.text and W1 in f.text and "Torvik Systems GmbH" in f.text
    assert "keep the skills entry" in f.text and "the skills list names “Coaching”" in f.text and "compound clause" in f.text
    # whole-document presence (the chip) keeps cv.claim_missing silent — the gap this closes
    assert "cv.claim_missing" not in _findings(_prose())


def test_silent_when_a_bullet_states_the_concept():
    assert "cv.skill_without_bullet" not in _findings(_prose(bullet=True))


def test_silent_when_no_chip_names_it_then_claim_missing_owns_it():
    found = _findings(_prose(chip=False))
    assert "cv.skill_without_bullet" not in found
    assert "cv.claim_missing" in found


def test_silent_when_the_vault_records_it_in_no_role_line():
    vault = _vault()
    vault["work_experience"][0]["responsibilities"] = vault["work_experience"][0]["responsibilities"][:1]
    assert "cv.skill_without_bullet" not in _findings(_prose(), vault=vault)


def test_silent_for_a_non_claimable_row():
    ledger = [dict(_ledger()[0], claimable=False, status="gap")]
    assert "cv.skill_without_bullet" not in _findings(_prose(), ledger=ledger)


def test_a_nice_to_have_concept_counts_like_a_required_one():
    """Rule 2 of the v2 writer: "nice-to-have concepts exactly like required ones"."""
    assert _ledger()[0]["fit_weight"] < 1.0
    assert "cv.skill_without_bullet" in _findings(_prose())


def test_the_code_is_registered_and_has_both_catalog_lines():
    import json

    from applire.services.finding_registry import REGISTRY

    assert "terminal:cv.skill_without_bullet" in REGISTRY
    root = Path(__file__).parent.parent.parent / "frontend" / "messages"
    for lang in ("de", "en"):
        code = json.load(open(root / f"{lang}.json"))["ats"]["terminalReview"]["code"]
        assert "{term}" in code["cv-skill_without_bullet"]


def test_a_summary_phrase_with_no_bullet_is_the_same_finding_786_291():
    prose = _prose(chip=False)
    prose["summary"] = "Senior Engineer with a focus on routing backends and coaching developers."
    found = _findings(prose)
    assert "cv.claim_missing" not in found  # whole-document presence: the summary
    f = found["cv.skill_without_bullet"]
    assert "the summary names" in f.text and COACH in f.text


def test_silent_when_only_another_section_names_it():
    """Neither a chip nor the summary: a mention in a project heading is not this fact."""
    prose = _prose(chip=False)
    prose["projects"] = [{"name": "Coaching rota", "bullets": []}]
    assert "cv.skill_without_bullet" not in _findings(prose)


def test_silent_when_the_role_restates_the_vault_sentence_in_other_words():
    """The first version raised 7 false omissions on the captured luna drafts: the
    ledger's form was simply not the bullet's word ("Invasive monitoring" against
    "Manage arterial and central venous catheter monitoring, vasoactive infusions and
    CRRT"). A paraphrase that keeps most content tokens is restated."""
    vault = _vault()
    vault["work_experience"][0]["responsibilities"].append(
        "Manages invasive monitoring (arterial/CVC), vasoactive infusions, and CRRT.")
    ledger = _ledger() + [{
        "concept": "Invasive monitoring", "surface_forms": ["Invasive monitoring"], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }]
    prose = _prose(bullet=True)
    prose["skills"].append("Invasive monitoring")
    assert _findings(prose, vault=vault, ledger=ledger)["cv.skill_without_bullet"].subjects == ("Invasive monitoring",)
    prose["work"][0]["bullets"].append(
        "Manage arterial and central venous catheter monitoring, vasoactive infusions and CRRT.")
    assert "cv.skill_without_bullet" not in _findings(prose, vault=vault, ledger=ledger)


@pytest.mark.parametrize("vault,bullet,restated", [
    # captured shapes (synthetic wording): translation keeps every figure
    ("Improved completion rate from 87% to 96% through a funnel program (40+ A/B tests).",
     "Steigerte die Abschlussrate durch Funnel-Optimierung mit mehr als 40 A/B-Tests von 87 % auf 96 %.", True),
    ("Budget responsibility approx. 6 Mio. € (staff, maintenance).",
     "Verantwortet ein Budget von ca. 6 Mio. € für Personal und Instandhaltung.", True),
    ("Steered the BfArM re-certification of both products (DiGAV evidence, ISO 27001 processes).",
     "Steuerte die BfArM-Rezertifizierung beider Produkte (DiGAV-Evidenz, ISO-27001-Prozesse).", True),
    ("Manages invasive monitoring (arterial/CVC), vasoactive infusions, and CRRT.",
     "Manage arterial and central venous catheter monitoring, vasoactive infusions and CRRT.", True),
    # half the content tokens, figure spelled differently (300k / 300.000): restated at 0.4
    ("Product owner for the member portal and mobile app (300k registered users), with backlog and roadmap responsibility.",
     "Verantwortete als Product Owner das Mitgliederportal und die mobile App mit 300.000 registrierten Nutzenden sowie Backlog und Roadmap.", True),
    # the true omissions: nothing of the sentence in the bullet
    ("Mentor two mid-level engineers; run the weekly design-review session.",
     "Designed and operated an event pipeline consumed by billing and notification services.", False),
    ("Introduced contract tests between the tracking API and three consumer teams.",
     "Designed and operated a tracking event pipeline consumed by billing services.", False),
    ("Seit zwei Jahren fachliche Führung der beiden Werkstudierenden.",
     "Verantwortet Monatsabschluss und HGB-Reporting der GmbH.", False),
    # a figure alone in a different sentence is not the sentence (all figures needed)
    ("Cut deploy time from 45 to 8 minutes.", "Ran 45 deployments a month.", False),
])
def test_vault_sentence_restated(vault, bullet, restated):
    from applire.services.cv_v2 import vault_sentence_restated

    assert vault_sentence_restated(vault, [bullet]) is restated


# ── 790:61 (Lead O's hunk): the identifier check reads the claimable-only vault ──

def _cert_vault(status: str) -> dict:
    vault = _vault()
    vault["certifications"] = [{"name": "ISO 27001 Lead Auditor", "status": status}]
    return vault


def test_an_unconfirmed_certification_backs_no_identifier():
    """O's probe: an UNCONFIRMED "ISO 27001 Lead Auditor" made the check return
    ([], []) while the Oracle (claimable-only index) reports the sentence unbacked."""
    from applire.services.cv_v2 import unmentioned_identifiers

    named, stated = unmentioned_identifiers(["Audits nach ISO 27001 geleitet."], _cert_vault("unconfirmed"))
    assert named == ["ISO 27001"]
    assert all("27001" not in s for s in stated), "an unconfirmed entry is never a remedy candidate"
    named, _ = unmentioned_identifiers(["Audits nach ISO 27001 geleitet."], _cert_vault("denied"))
    assert named == ["ISO 27001"]


def test_a_confirmed_certification_still_backs_its_identifier():
    """The other direction: nothing confirmed is lost by the filter."""
    from applire.services.cv_v2 import unmentioned_identifiers

    assert unmentioned_identifiers(["Audits nach ISO 27001 geleitet."], _cert_vault("confirmed")) == ([], [])


def test_SEAM_v2_checks_raise_the_identifier_an_unconfirmed_cert_carries():
    prose = _prose()
    prose["work"][0]["bullets"].append("Led audits under ISO 27001 for the routing backend.")
    assert "cv.identifier_not_in_profile" in _findings(prose, vault=_cert_vault("unconfirmed"))
    assert "cv.identifier_not_in_profile" not in _findings(prose, vault=_cert_vault("confirmed"))
