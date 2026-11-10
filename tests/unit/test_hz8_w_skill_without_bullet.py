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
    assert "keep the skills entry" in f.text and "compound clause" in f.text
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
