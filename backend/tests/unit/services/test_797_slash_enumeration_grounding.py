# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bug #797 (founder ruling UX-1, 2026-10-20) — "PP/MM: Nichts in deinem Profil
belegt das" on a CV whose vault holds SAP PP and SAP MM.

Synthetic reproduction of the UX-audit shape (Marcus, v0.44.0-beta): the
posting's keyword is the slash enumeration "PP/MM", the document says "SAP
PP/MM", "SAP PP", "SAP MM" and "SAP-PP-Rollout", the vault names the two
modules separately. ``present_unsupported`` clears only when EVERY matched form
grounds through ``ground_skill_claim`` (ADR-090 cl. 4); the slash form never did.
"""
from applire.services.ats_audit import _keyword_coverage, _norm, keyword_matches
from applire.services.oracle.matchers import build_vault_index, ground_skill_claim
from applire.services.oracle.matchers.grounding import slash_enumeration_parts


def _vault(bullets, skills=("SAP PP", "SAP MM", "SAP")):
    return {
        "personal_info": {"name": "Stefan Probe"},
        "work_experience": [{
            "id": "11111111-1111-1111-1111-111111111111", "company": "Weberit Kunststofftechnik GmbH",
            "title": "Produktionsleiter", "start_date": "2017-04", "end_date": None,
            "responsibilities": list(bullets),
        }],
        "skills": [{"name": s} for s in skills],
    }


MARCUS = _vault([
    "Rund 6 Mio. € Budget jährlich geplant; tägliche Arbeit mit SAP PP und SAP MM einschließlich Disposition.",
    "Als Key-User beim SAP-PP-Rollout in der Fertigung mitgewirkt.",
])
LEDGER = [{"concept": "PP/MM", "surface_forms": ["PP/MM", "SAP PP", "SAP MM", "SAP-PP"],
           "claimable": False, "status": "gap", "sources": ["keyword"], "fit_weight": 0.5, "evidence": ""}]
CV_TEXT = _norm(
    "Ergänzt wird das Profil durch Erfahrung in Arbeitssicherheit, ISO 45001, ISO 9001 und SAP PP/MM. "
    "Tägliche Arbeit mit SAP PP und SAP MM einschließlich Disposition. "
    "Als Key-User beim SAP-PP-Rollout in der Fertigung mitgewirkt. SAP PP · SAP MM"
)


def test_797_reproduction_the_slash_form_is_a_matched_form():
    forms = {m.form for m in keyword_matches("PP/MM", CV_TEXT, LEDGER)}
    assert "PP/MM" in forms


def test_797_a_vault_holding_both_modules_backs_the_enumeration():
    cov = _keyword_coverage(CV_TEXT, ["PP/MM"], LEDGER, vault_index=build_vault_index(MARCUS))
    assert cov.present_unsupported == []


def test_797_control_a_vault_holding_one_module_still_flags_it():
    only_pp = _vault(["Als Key-User beim SAP-PP-Rollout in der Fertigung mitgewirkt."], skills=("SAP PP",))
    cov = _keyword_coverage(CV_TEXT, ["PP/MM"], LEDGER, vault_index=build_vault_index(only_pp))
    assert cov.present_unsupported == ["PP/MM"]


def test_slash_enumeration_parts_distributes_the_head():
    assert slash_enumeration_parts("SAP PP/MM") == ["SAP PP", "SAP MM"]
    assert slash_enumeration_parts("PP/MM") == ["PP", "MM"]
    assert slash_enumeration_parts("SAP PP") is None
    assert slash_enumeration_parts("a / b") is None
    assert slash_enumeration_parts("A/B/C/D/E") is None


def test_every_part_must_ground():
    idx = build_vault_index(_vault(["IFS-Audits vorbereitet."], skills=("IFS",)))
    assert ground_skill_claim("IFS/BRC", idx) is None


def test_the_vault_side_enumeration_backs_one_named_part():
    idx = build_vault_index(_vault(["Erfahrung mit SAP PP/MM im Werk."], skills=()))
    unit = ground_skill_claim("SAP MM", idx)
    assert unit is not None and unit.path.startswith("work_experience[0]")
    assert ground_skill_claim("SAP QM", idx) is None
