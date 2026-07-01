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


def test_keyword_only_honest_gaps_returns_only_fit_zero_unclaimable_concepts():
    # US204: honest gaps that are pure ATS keywords (fit_weight 0, not claimable)
    # are invisible to match_score's category_c, so they must be routed to the
    # interview explicitly. Required/nice_to_have gaps already flow via category_c
    # and must NOT be duplicated here.
    from applire.services.keyword_ledger import keyword_only_honest_gaps

    ledger = [
        {"concept": "microservices", "fit_weight": 0.0, "claimable": False},  # keyword honest gap → yes
        {"concept": "agile", "fit_weight": 0.0, "claimable": True},            # keyword, but held → no
        {"concept": "Rust", "fit_weight": 1.0, "claimable": False},            # required gap (in category_c) → no
        {"concept": "Kubernetes", "fit_weight": 1.0, "claimable": True},       # held required → no
    ]
    assert keyword_only_honest_gaps(ledger) == ["microservices"]


def test_keyword_only_honest_gaps_is_none_safe():
    from applire.services.keyword_ledger import keyword_only_honest_gaps

    assert keyword_only_honest_gaps(None) == []
    assert keyword_only_honest_gaps([]) == []


# ---------------------------------------------------------------------------
# E037 polish (F2): collapse near-duplicate concepts the LLM emits as both a
# short keyword and the JD's full requirement phrase (e.g. "Kubernetes" AND
# "Kubernetes (production at scale)"). Left unmerged they clutter the gap list
# AND double-count the gap slot in the fit score. Merge only a *token prefix*
# duplicate with matching status — never a sub-token (Java/JavaScript) nor a
# mid-phrase shared token (SaaS ⊂ Multi-tenant SaaS…) nor across statuses.
# ---------------------------------------------------------------------------


def test_prefix_duplicate_concepts_with_same_status_are_merged():
    # LLM emitted both the short keyword and the full JD phrase; same gap status.
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Kubernetes", "gap", ["Kubernetes"]),
            _cls("Kubernetes (production at scale)", "gap", ["Kubernetes at scale"]),
        ],
        required_skills=["Kubernetes (production at scale)"],
        nice_to_have_skills=[],
        keywords=["Kubernetes"],
    )
    kube = [e for e in ledger if "kubernetes" in e["concept"].casefold()]
    assert len(kube) == 1, "the two Kubernetes concepts must collapse into one entry"
    e = kube[0]
    assert e["concept"] == "Kubernetes", "the shorter concept is the canonical label"
    assert set(e["sources"]) == {"required", "keyword"}, "merged entry unions both sources"
    assert e["fit_weight"] == 1.0, "required weight survives the merge"
    # every surface form from both entries is preserved for ATS coverage
    assert "Kubernetes" in e["surface_forms"] and "Kubernetes at scale" in e["surface_forms"]


def test_prefix_merge_is_order_independent():
    # Same two concepts in the opposite order still collapse to the short label.
    ledger = build_keyword_ledger(
        classifications=[
            _cls("SRE practice (SLOs, error budgets)", "gap"),
            _cls("SRE", "gap"),
        ],
        required_skills=["SRE practice (SLOs, error budgets)"],
        nice_to_have_skills=[],
        keywords=["SRE"],
    )
    sre = [e for e in ledger if "sre" in e["concept"].casefold()]
    assert len(sre) == 1
    assert sre[0]["concept"] == "SRE"


def test_subtoken_prefix_is_not_merged():
    # "Java" is a sub-token of "JavaScript", NOT a token prefix — they are
    # distinct requirements and must both survive (the substring pitfall).
    ledger = build_keyword_ledger(
        classifications=[_cls("Java", "gap"), _cls("JavaScript", "gap")],
        required_skills=["Java", "JavaScript"],
        nice_to_have_skills=[],
        keywords=[],
    )
    concepts = {e["concept"] for e in ledger}
    assert {"Java", "JavaScript"} <= concepts


def test_mid_phrase_shared_token_is_not_merged():
    # "SaaS" appears in the MIDDLE of "Multi-tenant SaaS platform scaling", not
    # as a leading token — keep them separate (conservative, no false merge).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("SaaS", "gap"),
            _cls("Multi-tenant SaaS platform scaling", "gap"),
        ],
        required_skills=["Multi-tenant SaaS platform scaling"],
        nice_to_have_skills=["SaaS"],
        keywords=[],
    )
    concepts = {e["concept"] for e in ledger}
    assert "SaaS" in concepts and "Multi-tenant SaaS platform scaling" in concepts


def test_prefix_duplicate_across_different_status_is_not_merged():
    # A claimable form must never absorb a gap form (or vice versa): merging
    # across status would corrupt truthfulness or the score. Keep them apart.
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Docker", "direct", evidence="shipped containers at ACME"),
            _cls("Docker Swarm", "gap"),
        ],
        required_skills=["Docker Swarm"],
        nice_to_have_skills=[],
        keywords=["Docker"],
    )
    concepts = {e["concept"] for e in ledger}
    assert "Docker" in concepts and "Docker Swarm" in concepts


def test_prefix_merge_deduplicates_the_fit_score_slot():
    # The real payoff: two required gaps for the same skill must weigh ONE slot,
    # not two — otherwise the denominator is inflated and the score deflated.
    from applire.services.match_score import compute_match_score_from_ledger

    ledger = build_keyword_ledger(
        classifications=[
            _cls("SRE", "gap"),
            _cls("SRE practice (SLOs, error budgets, incident response)", "gap"),
        ],
        required_skills=["SRE practice (SLOs, error budgets, incident response)"],
        nice_to_have_skills=[],
        keywords=["SRE"],
    )
    weighted = [e for e in ledger if e["fit_weight"] > 0]
    assert len(weighted) == 1, "the duplicated required gap must occupy one score slot"
    result = compute_match_score_from_ledger(ledger)
    # one required gap, nothing earned → score 0.0 over a single slot (not 0.0/2).
    assert result["category_c"] == ["SRE"]


# ---------------------------------------------------------------------------
# Mirror surface-form duplicates (E037 follow-up — the AI ↔ Artificial
# Intelligence case). The LLM sometimes emits the SAME concept twice under an
# acronym and its expansion (or two synonyms), each listing the other as a
# surface form. Token-prefix can't catch this (no shared leading token), so the
# gap list showed both "AI" and "Artificial Intelligence" and the fit slot was
# double-counted. Merge when the two concepts are MUTUAL surface forms of each
# other and share a status — conservative enough never to fuse Java/JavaScript.
# ---------------------------------------------------------------------------


def test_mirror_surface_form_duplicate_concepts_are_merged():
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Artificial Intelligence", "partial", ["Artificial Intelligence", "AI"], evidence="ran an AI project"),
            _cls("AI", "partial", ["AI", "Artificial Intelligence"]),
        ],
        required_skills=["Artificial Intelligence"],
        nice_to_have_skills=[],
        keywords=["AI"],
    )
    ai = [e for e in ledger if e["concept"].casefold() in {"ai", "artificial intelligence"}]
    assert len(ai) == 1, "acronym + expansion must collapse to a single concept"
    assert "AI" in ai[0]["surface_forms"] and "Artificial Intelligence" in ai[0]["surface_forms"]


def test_mirror_surface_form_merge_deduplicates_the_fit_slot():
    from applire.services.match_score import compute_match_score_from_ledger

    ledger = build_keyword_ledger(
        classifications=[
            _cls("Machine Learning", "gap", ["Machine Learning", "ML"]),
            _cls("ML", "gap", ["ML", "Machine Learning"]),
        ],
        required_skills=["Machine Learning"],
        nice_to_have_skills=[],
        keywords=["ML"],
    )
    weighted = [e for e in ledger if e["fit_weight"] > 0]
    assert len(weighted) == 1, "the mirrored required gap must occupy one score slot"
    result = compute_match_score_from_ledger(ledger)
    assert len(result["category_c"]) == 1, "the gap list shows one entry, not two"


def test_mirror_merge_across_different_status_is_not_merged():
    # A claimable form must never absorb a gap form even when they mirror each
    # other's surface forms — status still gates the merge (truthfulness/score).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Artificial Intelligence", "direct", ["Artificial Intelligence", "AI"], evidence="AI lead"),
            _cls("AI", "gap", ["AI", "Artificial Intelligence"]),
        ],
        required_skills=["Artificial Intelligence"],
        nice_to_have_skills=[],
        keywords=["AI"],
    )
    ai = [e for e in ledger if e["concept"].casefold() in {"ai", "artificial intelligence"}]
    assert len(ai) == 2, "different statuses must stay separate"


def test_equal_surface_form_sets_merge_even_when_concept_name_differs():
    # Real UAT shape: the LLM named one entry "Artificial Intelligence (AI)"
    # (parenthetical) and the other "AI", but both list the SAME surface forms.
    # Neither concept name is a surface form of the other, yet identical form
    # sets prove they are one concept.
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Artificial Intelligence (AI)", "partial", ["AI", "Artificial Intelligence", "KI"], evidence="AI work"),
            _cls("AI", "partial", ["AI", "Artificial Intelligence", "KI"]),
        ],
        required_skills=["Artificial Intelligence (AI)"],
        nice_to_have_skills=[],
        keywords=["AI"],
    )
    ai = [e for e in ledger if "ai" in e["concept"].casefold() or "intelligence" in e["concept"].casefold()]
    assert len(ai) == 1, "entries with identical surface-form sets must collapse to one"


def test_distinct_surface_form_sets_are_not_merged():
    # "Algorithm design" and "Algorithm development" share NO surface form (each
    # only carries its own label + German gloss) — distinct requirements, kept.
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Algorithm design", "partial", ["Algorithm design", "Algorithmenentwurf"]),
            _cls("Algorithm development", "partial", ["Algorithm development", "Algorithmenentwicklung"]),
        ],
        required_skills=["Algorithm design", "Algorithm development"],
        nice_to_have_skills=[],
        keywords=[],
    )
    concepts = {e["concept"] for e in ledger}
    assert "Algorithm design" in concepts and "Algorithm development" in concepts


def test_one_directional_surface_form_is_not_merged():
    # "Collaborative Research" lists "Research" as a surface form, but "Research"
    # does NOT list "Collaborative Research" — membership is not mutual, so they
    # are distinct requirements and both survive (no over-merge).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Research", "gap", ["Research"]),
            _cls("Collaborative Research", "gap", ["Collaborative Research", "Research"]),
        ],
        required_skills=["Research", "Collaborative Research"],
        nice_to_have_skills=[],
        keywords=[],
    )
    concepts = {e["concept"] for e in ledger}
    assert "Research" in concepts and "Collaborative Research" in concepts


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
