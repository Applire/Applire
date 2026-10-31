# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FIG-3 Oracle side (Hazelnut build 5): a figure in a CV bullet under entry X that
the vault holds only under ANOTHER entry is misattributed under X — even at the
same company. The same-employer escape stays for a letter sentence, which names
an employer, not a tenure. Held in the bullet's own entry (or its nested project),
or in both entries, the figure is grounded. A fact check: digits and units plus
entry ownership, no model call. Synthetic data only."""

from __future__ import annotations

import asyncio
import copy

from applire.services.oracle.selfaudit import build_self_audit_report

LIMS = ("Delivered the global QC LIMS roll-out across 3 sites in 7 months, supporting quality "
        "control for Vaxor production and contributing to Nordpharm's €4 billion revenue in 2021.")
JOINED = ("Led the global QC LIMS roll-out across three sites in seven months, supporting Vaxor "
          "quality control and contributing to Nordpharm's €4 billion revenue in 2021.")

BASE = {
    "personal_info": {"name": "Jana Lehmann"},
    "work_experience": [
        {"id": "w-now", "company": "Nordpharm SE", "role": "Associate Director", "start_date": "2024-12",
         "is_current": True, "responsibilities": ["Lead an agentic GenAI system for validation documents."],
         "achievements": []},
        {"id": "w-old", "company": "Nordpharm SE", "role": "System Engineer", "start_date": "2018-10",
         "end_date": "2021-05", "responsibilities": ["Operated laboratory systems."],
         "achievements": [LIMS]},
    ],
    "projects": [],
    "skills": [],
}
NO_FIGURE = LIMS.replace(" and contributing to Nordpharm's €4 billion revenue in 2021.", ".")


def _vault(*, old_has_figure=True, now_has_figure=False, project_has_figure=False):
    v = copy.deepcopy(BASE)
    old, now = v["work_experience"][1], v["work_experience"][0]
    old["achievements"] = [LIMS if old_has_figure else NO_FIGURE]
    if now_has_figure:
        now["achievements"].append("Nordpharm reported €4 billion in revenue in 2021.")
    if project_has_figure:
        old["achievements"] = [NO_FIGURE]
        v["projects"].append({"id": "p-lims", "name": "Global QC LIMS", "associated_experience": "w-old",
                              "achievements": ["The roll-out contributed to Nordpharm's €4 billion revenue in 2021."]})
    return v


def _verdict(vault, entry_id="w-old", bullet=JOINED) -> str:
    cv = {"summary": "", "skills": [], "work_history": [
        {"id": entry_id, "company": "Nordpharm SE", "role": "System Engineer", "bullets": [bullet]}]}
    report = asyncio.run(build_self_audit_report(vault, tailored_data=cv, document_language="en"))
    (claim,) = [c for c in report["claims"] if "€4 billion" in c["claim"]["text"]]
    return claim["verdict"]["verdict"]


def test_a_figure_held_only_by_another_entry_of_the_same_company_is_misattributed():
    assert _verdict(_vault(old_has_figure=False, now_has_figure=True)) == "misattributed"


def test_the_joined_sentence_under_the_entry_holding_the_figure_is_grounded():
    assert _verdict(_vault()) == "grounded"


def test_a_figure_held_by_both_entries_is_grounded_under_either():
    v = _vault(old_has_figure=True, now_has_figure=True)
    assert _verdict(v, "w-old") == "grounded"
    assert _verdict(v, "w-now", "Nordpharm reported €4 billion in revenue in 2021, while I led the GenAI system.") \
        in {"grounded", "unverifiable"}


def test_a_figure_held_by_the_entrys_nested_project_is_its_own():
    assert _verdict(_vault(project_has_figure=True)) == "grounded"


def test_a_letter_sentence_keeps_the_same_employer_escape():
    """"At Nordpharm SE" anchors to the current role (tie-break) while the figure sits
    on the earlier tenure: a letter cannot say which tenure, so no accusation."""
    v = _vault()
    letter = {"recipient": {"company": "Agilent"}, "body": {"paragraphs": [
        "At Nordpharm SE, I led the QC LIMS roll-out across three sites, contributing to Nordpharm's "
        "€4 billion revenue in 2021."]}}
    report = asyncio.run(build_self_audit_report(v, letter_data=letter, document_language="en"))
    verdicts = [c["verdict"]["verdict"] for c in report["claims"] if "€4 billion" in c["claim"]["text"]]
    assert verdicts and "misattributed" not in verdicts
