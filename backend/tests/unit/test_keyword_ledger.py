# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""Unit tests for the deterministic Keyword Ledger builder (ADR-048, E037/US198).

The ledger is the single source of truth for every JD expectation: each entry
carries a concept (drives fit) and its literal surface forms (drive coverage),
classified against the profile. Python is authoritative for sources/fit_weight;
the LLM only supplies status/evidence/surface forms.
"""

from applire.services.keyword_ledger import build_keyword_ledger


def _cls(concept, status, surface_forms=None, evidence=""):
    item = {"concept": concept, "status": status, "evidence": evidence}
    if surface_forms is not None:
        item["surface_forms"] = surface_forms
    return item


def _by_concept(ledger):
    return {e["concept"]: e for e in ledger}


def test_required_direct_entry_has_full_fit_weight_and_is_claimable():
    ledger = build_keyword_ledger(
        classifications=[_cls("Kubernetes", "direct", ["Kubernetes", "K8s"], "8y as DevOps Lead")],
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=["Kubernetes"],
    )
    e = _by_concept(ledger)["Kubernetes"]
    assert e["fit_weight"] == 1.0
    assert "required" in e["sources"]
    assert e["status"] == "direct"
    assert e["claimable"] is True
    assert e["surface_forms"] == ["Kubernetes", "K8s"]
    assert e["evidence"] == "8y as DevOps Lead"


def test_nice_to_have_partial_is_half_weight_and_claimable():
    ledger = build_keyword_ledger(
        classifications=[_cls("Terraform", "partial", evidence="used on one project")],
        required_skills=[],
        nice_to_have_skills=["Terraform"],
        keywords=[],
    )
    e = _by_concept(ledger)["Terraform"]
    assert e["fit_weight"] == 0.5
    assert e["sources"] == ["nice_to_have"]
    assert e["claimable"] is True


def test_keyword_only_term_is_fit_weight_zero():
    # "agile" is a pure ATS context term — affects coverage, never fit.
    ledger = build_keyword_ledger(
        classifications=[_cls("agile", "direct", evidence="Scrum at ACME")],
        required_skills=[],
        nice_to_have_skills=[],
        keywords=["agile"],
    )
    e = _by_concept(ledger)["agile"]
    assert e["fit_weight"] == 0.0
    assert e["sources"] == ["keyword"]
    assert e["claimable"] is True  # still claimable (truthful), just doesn't move fit


def test_unclassified_jd_expectation_defaults_to_gap_not_claimable():
    # The LLM forgot to classify "Rust"; it must default to gap (never silent credit).
    ledger = build_keyword_ledger(
        classifications=[_cls("Python", "direct")],
        required_skills=["Python", "Rust"],
        nice_to_have_skills=[],
        keywords=[],
    )
    rust = _by_concept(ledger)["Rust"]
    assert rust["status"] == "gap"
    assert rust["claimable"] is False
    assert rust["evidence"] == ""
    assert rust["fit_weight"] == 1.0  # it's a required skill — gap on it still weighs


def test_classification_matching_no_jd_list_is_dropped():
    # LLM hallucinated a concept that's in no JD list — drop it.
    ledger = build_keyword_ledger(
        classifications=[_cls("Python", "direct"), _cls("Cobol", "direct")],
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=[],
    )
    concepts = {e["concept"] for e in ledger}
    assert "Python" in concepts
    assert "Cobol" not in concepts


def test_concept_in_both_required_and_keyword_lists_required_weight_wins():
    ledger = build_keyword_ledger(
        classifications=[_cls("Docker", "direct", ["Docker"])],
        required_skills=["Docker"],
        nice_to_have_skills=[],
        keywords=["Docker"],
    )
    e = _by_concept(ledger)["Docker"]
    assert e["fit_weight"] == 1.0
    assert set(e["sources"]) == {"required", "keyword"}


def test_surface_forms_default_to_concept_when_llm_omits_them():
    ledger = build_keyword_ledger(
        classifications=[_cls("Python", "direct")],
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=[],
    )
    e = _by_concept(ledger)["Python"]
    assert e["surface_forms"] == ["Python"]


def test_empty_jd_yields_empty_ledger():
    assert build_keyword_ledger([], [], [], []) == []


def _mock_classifications():
    """Adapt the MockLLMProvider gap response into build_keyword_ledger input shape."""
    from applire.providers.llm.mock import _GAP_ANALYSIS_RESPONSE

    return [
        {
            "concept": c.get("requirement", ""),
            "status": c.get("status", "gap"),
            "evidence": c.get("reason", ""),
            "surface_forms": c.get("surface_forms"),
        }
        for c in _GAP_ANALYSIS_RESPONSE["classifications"]
    ]


def test_mock_classifies_keyword_terms_so_held_keyword_is_claimable():
    # "CI/CD" is a JD *keyword* the candidate demonstrably has (CI/CD pipelines).
    # The mock must classify keyword terms (mirrors the prompt change) so it lands
    # claimable, not as a synthesized gap.
    from applire.providers.llm.mock import _JOB_ANALYSIS_RESPONSE as JOB

    ledger = build_keyword_ledger(
        _mock_classifications(),
        JOB["required_skills"],
        JOB["nice_to_have_skills"],
        JOB["keywords"],
    )
    cicd = [
        e
        for e in ledger
        if e["concept"].casefold() == "ci/cd"
        or any(s.casefold() == "ci/cd" for s in e["surface_forms"])
    ]
    assert cicd, "CI/CD keyword must appear in the ledger"
    assert any(e["claimable"] for e in cicd), "a held keyword must be claimable, not a gap"


# ---------------------------------------------------------------------------
# E037 US202/US203: reviewer + ATS consumption helpers
# ---------------------------------------------------------------------------

_LEDGER = [
    {
        "concept": "Kubernetes",
        "surface_forms": ["Kubernetes", "K8s"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "8y as DevOps Lead",
        "claimable": True,
    },
    {
        "concept": "Terraform",
        "surface_forms": ["Terraform"],
        "sources": ["nice_to_have"],
        "fit_weight": 0.5,
        "status": "partial",
        "evidence": "one project",
        "claimable": True,
    },
    {
        "concept": "Rust",
        "surface_forms": ["Rust"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
]


def test_claimable_surface_forms_flattens_only_claimable_entries():
    from applire.services.keyword_ledger import claimable_surface_forms

    forms = claimable_surface_forms(_LEDGER)
    # every surface form of every claimable entry, none from the gap
    assert "Kubernetes" in forms and "K8s" in forms and "Terraform" in forms
    assert "Rust" not in forms


def test_claimable_surface_forms_is_none_safe():
    from applire.services.keyword_ledger import claimable_surface_forms

    assert claimable_surface_forms(None) == []
    assert claimable_surface_forms([]) == []


def test_render_ledger_reviewer_block_lists_claimable_and_forbidden():
    from applire.services.keyword_ledger import render_ledger_reviewer_block

    block = render_ledger_reviewer_block(_LEDGER)
    # claimable concepts + their surface forms are surfaced for the absent-check
    assert "Kubernetes" in block and "K8s" in block and "Terraform" in block
    # the honest-gap concept appears in the forbidden / do-not-claim section
    assert "Rust" in block
    # the block must instruct both new reviewer checks
    low = block.lower()
    assert "absent" in low or "missing" in low  # report claimable keywords not in the draft
    assert "claim" in low                        # never claim a forbidden concept


def test_render_ledger_reviewer_block_empty_for_empty_ledger():
    from applire.services.keyword_ledger import render_ledger_reviewer_block

    assert render_ledger_reviewer_block(None) == ""
    assert render_ledger_reviewer_block([]) == ""
