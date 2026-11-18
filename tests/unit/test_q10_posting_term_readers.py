# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — every reader of a posting term the person has not ticked
(ruling Q7-2; ADR-048 / ADR-052 / ADR-094 amended 2026-11-17).

Delivery 2b, Daniel (synthetic): the writer printed "Observability", "Security
engineering" and "Event-driven architecture" as skill chips for work he
described in his own words; the reviewer defended them as blocking keeps; the
coverage scan demanded such labels; the Oracle graded them grounded through a
sibling form or the ledger's backing quote. Each test below names one reader.
"""
from __future__ import annotations

import asyncio
import copy

from applire.schemas.cv import TailoredCVData
from applire.services import cv as cv_mod
from applire.services import keyword_ledger as kl
from applire.services import posting_terms as pt
from applire.services.cv_v2 import V2CheckContext, V2Terminal, _skill_findings
from applire.services.oracle.selfaudit import build_self_audit_report
from applire.services.reviewer import CheckFinding

WID = "11111111-1111-1111-1111-111111111111"
QUOTE = "Built Prometheus and Grafana dashboards with alert routing for the tracking services."
VAULT = {
    "personal_info": {"name": "Daniel Kovac"},
    "professional_summary": {"en": "Backend engineer with eight years of Python services.", "de": ""},
    "work_experience": [{
        "id": WID, "company": "Parcelo GmbH", "role": "Backend Engineer",
        "start_date": "2019-01", "end_date": None,
        "responsibilities": [
            QUOTE,
            "Designed and ran the shipment order-event pipeline on Apache Kafka.",
        ],
        "achievements": [],
    }],
    "skills": [{"name": "Prometheus", "status": "confirmed"}, {"name": "Grafana", "status": "confirmed"},
               {"name": "Kafka", "status": "confirmed"}, {"name": "Python", "status": "confirmed"}],
}
OBS = {"concept": "Observability", "surface_forms": ["Prometheus", "application observability"],
       "status": "direct", "claimable": True, "sources": ["required"], "evidence": "dashboards",
       "backing": {"quote": QUOTE, "unit_path": "work_experience[0].responsibilities[0]"}}
KAFKA = {"concept": "Apache Kafka", "surface_forms": ["Kafka"], "status": "direct",
         "claimable": True, "sources": ["required"], "evidence": "pipeline"}


def _ledger(stamped=True):
    rows = [copy.deepcopy(OBS), copy.deepcopy(KAFKA)]
    return pt.stamp_posting_terms_open(rows, VAULT, same_language=True) if stamped else rows


# ── the writers' and reviewer's blocks ───────────────────────────────────────

def test_the_writer_block_renders_the_term_in_the_persons_words_never_as_a_chip():
    block = kl.render_ledger_prompt_block(_ledger())
    line = next(ln for ln in block.splitlines() if ln.startswith("  - Observability"))
    assert "MET IN THE PROFILE'S WORDS" in line and "[forms:" not in line
    assert "never write Observability, application observability as a skills-list entry" in line
    assert '"Prometheus"' in line and QUOTE in line
    kafka = next(ln for ln in block.splitlines() if ln.startswith("  - Apache Kafka"))
    assert "MET IN THE PROFILE'S WORDS" not in kafka and "[forms: Kafka]" in kafka


def test_the_letter_writer_block_carries_the_same_line():
    block = kl.render_ledger_prompt_block(_ledger(), audience="letter")
    assert any(ln.startswith("  - Observability [MET IN THE PROFILE'S WORDS") for ln in block.splitlines())


def test_the_reviewer_is_told_a_chip_in_the_term_overstates_and_is_never_a_keep():
    block = kl.render_ledger_reviewer_block(_ledger())
    line = next(ln for ln in block.splitlines() if ln.startswith("  - Observability"))
    assert "overstates" in line and "never ask to keep or add it" in line
    assert "[forms:" not in line


def test_the_coverage_scan_and_the_underclaim_demand_never_list_the_term():
    draft = {"summary": "Backend engineer.", "work": [{"id": WID, "bullets": ["Python services."]}],
             "skills": []}
    missing = {e["concept"] for e in kl.verified_missing_claimable(draft, _ledger())}
    assert "Observability" not in missing and "Apache Kafka" in missing
    names = {e["concept"] for e in kl.underclaim_candidate_entries(_ledger())}
    assert "Observability" not in names and "Apache Kafka" in names
    # unstamped (the person ticked it, or another language) it is demanded as before
    missing = {e["concept"] for e in kl.verified_missing_claimable(draft, _ledger(stamped=False))}
    assert "Observability" in missing


# ── the deterministic skills chain (v2 findings) ─────────────────────────────

def _cv(skills, bullets=(QUOTE,)):
    return TailoredCVData.model_validate({
        "contact": {"name": "Daniel Kovac", "email": "d@example.com"},
        "summary": "Backend engineer.",
        "work_history": [{"id": WID, "company": "Parcelo GmbH", "role": "Backend Engineer",
                          "start_date": "2019-01", "end_date": None, "bullets": list(bullets)}],
        "skills": list(skills),
    })


def _ctx(ledger):
    job = {"required_skills": ["Observability", "Apache Kafka"], "nice_to_have_skills": [], "keywords": []}
    return V2CheckContext(profile_json=VAULT, keyword_ledger=ledger, job_dict=job, language="en")


def test_a_chip_in_an_unticked_term_is_its_own_finding_to_the_corrector():
    found = {f.check: f for f in _skill_findings(_cv(["Observability", "Kafka", "Prometheus"]), _ctx(_ledger()), cv_mod)}
    f = found["cv.skill_posting_term"]
    assert f.subjects == ("Observability",) and f.blocking
    assert "cv.skill_untied" not in found


def test_the_echo_drop_unties_a_term_the_ledger_bridge_would_tie():
    cv = _cv(["Observability", "Kafka"])
    job = {"required_skills": ["Observability"], "nice_to_have_skills": [], "keywords": []}
    assert cv_mod._drop_ungrounded_jd_echo_skills(cv, VAULT, job, _ledger(stamped=False)).skills == ["Observability", "Kafka"]
    assert cv_mod._drop_ungrounded_jd_echo_skills(cv, VAULT, job, _ledger()).skills == ["Kafka"]


def test_the_narrative_restore_never_puts_the_term_back_from_a_bullet():
    cv = _cv(["Kafka"], bullets=("Owned observability for all tracking services.",))
    # unstamped (the person ticked it), the bullet's word goes on the list — the
    # restore grounds it through the row's held sibling form "Prometheus"
    before = cv_mod._restore_narrative_named_skills(cv, VAULT, _ledger(stamped=False), document_language="en")
    assert "Observability" in before.skills
    out = cv_mod._restore_narrative_named_skills(cv, VAULT, _ledger(), document_language="en")
    assert not any("bservability" in s for s in out.skills)


def test_a_ticked_term_is_a_held_skill_the_chain_keeps():
    vault = copy.deepcopy(VAULT)
    vault["skills"].append({"name": "Observability", "status": "confirmed"})
    ledger = pt.stamp_posting_terms_open([copy.deepcopy(OBS)], vault, same_language=True)
    assert not pt.is_posting_term_open(ledger[0])
    job = {"required_skills": ["Observability"], "nice_to_have_skills": [], "keywords": []}
    assert cv_mod._drop_ungrounded_jd_echo_skills(_cv(["Observability"]), vault, job, ledger).skills == ["Observability"]


def test_a_second_name_ties_its_chip():
    vault = copy.deepcopy(VAULT)
    vault["skills"][0]["aliases"] = ["Monitoring stack"]
    job = {"required_skills": ["Monitoring stack"], "nice_to_have_skills": [], "keywords": []}
    out = cv_mod._drop_ungrounded_jd_echo_skills(_cv(["Monitoring stack"]), vault, job, [])
    assert out.skills == ["Monitoring stack"]


# ── precedence: a code-check cap outranks a reviewer coverage keep ───────────

def test_a_skill_the_cap_removed_earlier_is_carried_to_the_reviewer_as_a_fact():
    term = V2Terminal(ctx=_ctx([]))
    cap = CheckFinding(check="cv.skills_cap", subjects=("S3",), text="over cap")
    first = term._cap_precedence([cap], _cv(["S3", "Kafka"]))
    assert [f.check for f in first] == ["cv.skills_cap"]
    later = term._cap_precedence([], _cv(["Kafka"]))
    held = next(f for f in later if f.check == "cv.skills_cap_held")
    assert held.subjects == ("S3",) and not held.blocking and "do not ask" in held.text
    # back on the page: the cap finding fires again and the advisory is not doubled
    again = term._cap_precedence([cap], _cv(["S3", "Kafka"]))
    assert [f.check for f in again] == ["cv.skills_cap"]


# ── the Oracle ───────────────────────────────────────────────────────────────

def _audit(skills, vault=VAULT, ledger=None, language="en"):
    tailored = {
        "contact": {"name": "Daniel Kovac", "email": "d@example.com"},
        "summary": "Backend engineer with eight years of Python services.",
        "work_history": [{"id": WID, "company": "Parcelo GmbH", "role": "Backend Engineer",
                          "start_date": "2019-01", "end_date": None, "bullets": [QUOTE]}],
        "skills": list(skills),
    }
    return asyncio.run(build_self_audit_report(
        vault, tailored_data=tailored, document_language=language,
        keyword_ledger=_ledger(stamped=False) if ledger is None else ledger,
    ))


def _chip(report, text):
    return next(c["verdict"] for c in report["claims"] if c["claim"]["text"] == text)


def test_an_unticked_term_printed_as_a_chip_is_left_open_never_blocking():
    report = _audit(["Observability", "Kafka"])
    v = _chip(report, "Observability")
    assert (v["verdict"], v["detail_key"], v["detail_params"]) == (
        "unverifiable", "postingSkillNotHeld", {"term": "Observability"})
    assert _chip(report, "Kafka")["verdict"] == "grounded"


def test_a_ticked_term_grounds_normally():
    vault = copy.deepcopy(VAULT)
    vault["skills"].append({"name": "Observability", "status": "confirmed"})
    v = _chip(_audit(["Observability"], vault=vault), "Observability")
    assert v["verdict"] == "grounded" and v.get("detail_key") != "postingSkillNotHeld"


def test_a_second_name_grounds_its_chip():
    vault = copy.deepcopy(VAULT)
    vault["skills"][0]["aliases"] = ["Observability"]
    v = _chip(_audit(["Observability"], vault=vault), "Observability")
    assert v["verdict"] == "grounded" and v.get("detail_key") != "postingSkillNotHeld"


def test_a_second_name_is_vault_evidence_on_its_own():
    """No ledger row ties it: only the alias itself can ground the chip."""
    vault = copy.deepcopy(VAULT)
    vault["skills"][0]["aliases"] = ["Monitoring stack"]
    assert _chip(_audit(["Monitoring stack"], ledger=[]), "Monitoring stack")["verdict"] != "grounded"
    assert _chip(_audit(["Monitoring stack"], vault=vault, ledger=[]), "Monitoring stack")["verdict"] == "grounded"


def test_across_languages_the_chip_is_not_read():
    vault = copy.deepcopy(VAULT)
    vault["professional_summary"] = {"de": "Backend-Entwickler mit acht Jahren Python-Diensten.", "en": ""}
    report = _audit(["Observability"], vault=vault, language="de")
    assert _chip(report, "Observability").get("detail_key") != "postingSkillNotHeld"


def test_the_new_keys_are_registered():
    from applire.services.finding_registry import REGISTRY

    assert REGISTRY["oracle:postingSkillNotHeld"].resolver == "ask_true"
    assert "terminal:cv.skill_posting_term" in REGISTRY and "terminal:cv.skills_cap_held" in REGISTRY
