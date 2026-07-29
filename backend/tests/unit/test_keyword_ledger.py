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


# ---------------------------------------------------------------------------
# F4 (blind PQ 2026-07-02, trust-critical): the ledger's own honest-gap verdict
# must win over a claimable entry's surface-form aliasing. The gap LLM classified
# the JD's compound requirement "Cloud environment qualification (AWS, Azure)" as
# partial (AWS evidence only) but echoed BOTH tokens — including "Azure" — as its
# surface forms, while the SAME ledger held a separate "Azure" entry with status
# gap. claimable_surface_forms() then exported "Azure" as claimable, and the ATS
# audit told the user "Azure — supported by your profile" although they had
# denied any Azure experience in writing. Invariant: a concept the ledger itself
# classifies "gap" must never ride along as a claimable surface form.
# ---------------------------------------------------------------------------


def test_gap_concept_is_stripped_from_claimable_surface_forms():
    # Exact blind-PQ F4 shape (gap_analyses row 804a6a89…, 2026-07-02).
    from applire.services.keyword_ledger import claimable_surface_forms

    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "Cloud environment qualification (AWS, Azure)",
                "partial",
                ["Cloud environment qualification", "Cloud qualification", "AWS", "Azure"],
                evidence="Qualified first GxP cloud environment (AWS). Azure not explicitly mentioned.",
            ),
            _cls("Azure", "gap", ["Azure"]),
        ],
        required_skills=["Cloud environment qualification (AWS, Azure)"],
        nice_to_have_skills=[],
        keywords=["AWS", "Azure"],
    )

    forms = {f.casefold() for f in claimable_surface_forms(ledger)}
    assert "azure" not in forms, (
        "an honest-gap concept must never be exported as a claimable surface form"
    )
    assert "aws" in forms, "the genuinely supported token must survive the strip"

    cloud = _by_concept(ledger)["Cloud environment qualification (AWS, Azure)"]
    assert all(f.casefold() != "azure" for f in cloud["surface_forms"])
    azure = _by_concept(ledger)["Azure"]
    assert azure["status"] == "gap" and azure["claimable"] is False


def test_same_concept_claimable_and_gap_resolves_to_gap():
    # The LLM contradicting itself (same concept once direct, once gap) must
    # resolve to the honest side — never-claim outranks claim (ADR-040/ADR-048).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Azure", "direct", ["Azure"], evidence="cloud experience"),
            _cls("Azure", "gap", ["Azure"]),
        ],
        required_skills=["Azure"],
        nice_to_have_skills=[],
        keywords=[],
    )
    azure_entries = [e for e in ledger if e["concept"].casefold() == "azure"]
    assert len(azure_entries) == 1, "a contradicted concept must occupy one entry (one score slot)"
    assert azure_entries[0]["status"] == "gap"
    assert azure_entries[0]["claimable"] is False


def test_gap_with_denial_evidence_is_never_claimable():
    # Regression shape for the F4 invariant: a concept the LLM classifies "gap"
    # on denial evidence has claimable == False and carries no evidence text
    # downstream (evidence is only kept for claimable entries).
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "Azure",
                "gap",
                ["Azure"],
                evidence='Candidate explicitly denied it: "I have no hands-on Azure experience."',
            )
        ],
        required_skills=["Azure"],
        nice_to_have_skills=[],
        keywords=["Azure"],
    )
    e = _by_concept(ledger)["Azure"]
    assert e["claimable"] is False
    assert e["evidence"] == ""


def test_stance_strip_does_not_touch_distinct_claimable_forms():
    # Conservative strip: only a form norm-EQUAL to a gap entry's concept is
    # removed. "Docker" (claimable) is untouched by the gap "Docker Swarm".
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Docker", "direct", ["Docker", "Containers"], evidence="shipped containers"),
            _cls("Docker Swarm", "gap", ["Docker Swarm"]),
        ],
        required_skills=["Docker", "Docker Swarm"],
        nice_to_have_skills=[],
        keywords=[],
    )
    docker = _by_concept(ledger)["Docker"]
    assert docker["surface_forms"] == ["Docker", "Containers"]


def test_stance_strip_falls_back_to_concept_when_all_forms_were_gaps():
    # Degenerate LLM output: every surface form of a claimable entry is an
    # honest-gap concept. The entry keeps its own concept as the surface form
    # (builder invariant: surface_forms is never empty).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Cloud platforms", "partial", ["Azure", "GCP"], evidence="cloud work"),
            _cls("Azure", "gap", ["Azure"]),
            _cls("GCP", "gap", ["GCP"]),
        ],
        required_skills=["Cloud platforms", "Azure", "GCP"],
        nice_to_have_skills=[],
        keywords=[],
    )
    cloud = _by_concept(ledger)["Cloud platforms"]
    assert cloud["surface_forms"] == ["Cloud platforms"]


# ── #231 — persisted denial stance is a hard floor over adjacency inference ──
#
# F8 (founder-acceptance run, 2026-07-23): a candidate denied hands-on
# LegalTech/embedding/vector-store/reranker work in testimony; the denial was
# never persisted (fixed separately, ProfileMetadata.denied_concepts), so the
# NEXT analyze_gaps run upgraded the denied concept via adjacency ("RAG
# experience typically involves embeddings") — Embeddings went
# {gap, claimable: False} -> {partial, claimable: True}. These tests pin the
# deterministic floor once the denial IS persisted and threaded in as
# ``denied_concepts`` (build_keyword_ledger's own contract — services/gap.py
# is responsible for extracting the token list from the profile).


def test_denied_concept_overrides_claimable_classification_to_gap():
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "Embeddings", "partial", ["Embeddings"],
                evidence="RAG experience typically involves embeddings",
            ),
        ],
        required_skills=["Embeddings"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["embeddings"],
    )
    e = _by_concept(ledger)["Embeddings"]
    assert e["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert e["claimable"] is False
    assert "explicit" in e["evidence"].lower() or "limit" in e["evidence"].lower()
    assert "typically involves embeddings" not in e["evidence"]  # adjacency rationale gone


def test_denied_concept_matches_via_alias_group():
    # Denial recorded as "kubernetes"; ledger concept is the canonical form.
    # Reuses the SAME alias groups as enforce_stance (stance.py _ALIAS_GROUPS).
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Kubernetes", "direct", ["Kubernetes", "K8s"], evidence="ran K8s clusters"),
        ],
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["k8s"],
    )
    e = _by_concept(ledger)["Kubernetes"]
    assert e["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert e["claimable"] is False


def test_denial_is_concept_scoped_not_topic_radius():
    """#207 over-drop lesson: denying "embeddings" must NOT suppress an
    unrelated but topically-adjacent RAG claim — matching is token/alias
    scoped, never a semantic-radius guess."""
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Embeddings", "gap", ["Embeddings"]),
            _cls("RAG", "direct", ["RAG"], evidence="built a production RAG pipeline"),
        ],
        required_skills=["Embeddings", "RAG"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["embeddings"],
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["status"] == "direct"
    assert rag["claimable"] is True
    assert rag["evidence"] == "built a production RAG pipeline"


def test_denied_concepts_none_or_empty_is_a_noop():
    ledger_no_arg = build_keyword_ledger(
        classifications=[_cls("Python", "direct", ["Python"], evidence="5 years")],
        required_skills=["Python"], nice_to_have_skills=[], keywords=[],
    )
    ledger_empty = build_keyword_ledger(
        classifications=[_cls("Python", "direct", ["Python"], evidence="5 years")],
        required_skills=["Python"], nice_to_have_skills=[], keywords=[],
        denied_concepts=[],
    )
    assert ledger_no_arg == ledger_empty
    assert _by_concept(ledger_empty)["Python"]["claimable"] is True


def test_denial_with_unicode_apostrophe_statement_still_matches_on_concept():
    """The persisted denial's verbatim STATEMENT (kept only for the receipt,
    never for matching) may carry typographic punctuation a real model emits
    ("I didn't personally configure…", U+2019) — services/gap.py extracts
    only the concept TOKEN ("embeddings") from ProfileMetadata.denied_concepts
    before calling build_keyword_ledger, so the override must not depend on,
    or be defeated by, the statement's punctuation."""
    statement = (
        "I didn’t personally configure the embedding models, the vector "
        "store or any reranking."
    )
    # Round-trips through the real persisted shape (DeniedConcept) exactly as
    # services/gap.py would read it off profile.profile_json["metadata"].
    from applire.schemas.profile import DeniedConcept

    denied = DeniedConcept(
        concept="embeddings", statement=statement, source="agent_interview",
        date="2026-07-23",
    ).model_dump(mode="json")
    ledger = build_keyword_ledger(
        classifications=[
            _cls("Embeddings", "partial", ["Embeddings"], evidence="adjacency guess"),
        ],
        required_skills=["Embeddings"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=[denied["concept"]],
    )
    e = _by_concept(ledger)["Embeddings"]
    assert e["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert e["claimable"] is False


def test_word_boundary_regression_ai_ml_survives_ml_training_denial():
    """Founder-acceptance adversarial pass (2026-07-23): a candidate denied
    "machine learning model training" while explicitly reaffirming AI/ML
    integration work in the same statement. Before the word-boundary fix,
    _enforce_denial_stance force-killed the unrelated JD-required concept
    "AI/ML" (fit_weight 1.0) because its short surface forms ("AI"/"ML")
    collided as bare substrings inside "tr-ai-ning" — the exact class #207
    deliberately excludes ml/ai from _ALIAS_GROUPS for. "Machine learning"
    itself — the concept the candidate actually named as a whole word — must
    still be suppressed."""
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "AI/ML", "direct", ["AI/ML", "AI", "ML"],
                evidence="hands-on AI/ML integration experience",
            ),
            _cls(
                "Machine learning", "partial", ["Machine learning"],
                evidence="adjacency guess",
            ),
        ],
        required_skills=["AI/ML", "Machine learning"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["machine learning model training"],
    )
    by_concept = _by_concept(ledger)
    ai_ml = by_concept["AI/ML"]
    assert ai_ml["status"] == "direct"
    assert ai_ml["claimable"] is True

    ml = by_concept["Machine learning"]
    assert ml["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert ml["claimable"] is False


def test_f8_legaltech_denial_excluded_from_ats_claimable_surface_forms():
    """F8's exact surface: LegalTech, denied via interview testimony, must not
    reach the ATS panel's "supported by your profile" claimable list — the
    SAME persisted GapAnalysis.keyword_ledger row both the ATS audit
    (claimable_surface_forms) and CV/letter generation read."""
    from applire.services.keyword_ledger import claimable_surface_forms

    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "LegalTech", "partial", ["LegalTech"],
                evidence="adjacent to contract-management tooling experience",
            ),
        ],
        required_skills=["LegalTech"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["LegalTech"],
    )
    e = _by_concept(ledger)["LegalTech"]
    assert e["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert e["claimable"] is False
    assert "LegalTech" not in claimable_surface_forms(ledger)


# ── ADR-064 — denial_level mirrored onto the forced ledger entry ────────────


def test_enforce_denial_stance_mirrors_denial_level_from_dict_denied_concepts():
    """_enforce_denial_stance accepts the raw DeniedConcept dict shape (the
    #231 persisted shape) and mirrors its denial_level onto the ledger entry
    it forces to "denied"."""
    from applire.services.keyword_ledger import _enforce_denial_stance

    ledger = [
        {
            "concept": "BaFin supervision", "surface_forms": ["BaFin supervision"],
            "sources": ["required"], "fit_weight": 1.0, "status": "direct",
            "evidence": "", "claimable": True,
        }
    ]
    out = _enforce_denial_stance(
        ledger,
        [{"concept": "BaFin supervision", "denial_level": "partial"}],
    )
    assert out[0]["status"] == "denied"
    assert out[0]["claimable"] is False
    assert out[0]["denial_level"] == "partial"


def test_enforce_denial_stance_bare_strings_still_work_as_direct():
    """Back-compat: a plain list[str] of denied concepts (every existing
    caller/test) keeps working and mirrors denial_level "direct"."""
    from applire.services.keyword_ledger import _enforce_denial_stance

    ledger = [
        {
            "concept": "BaFin supervision", "surface_forms": ["BaFin supervision"],
            "sources": ["required"], "fit_weight": 1.0, "status": "direct",
            "evidence": "", "claimable": True,
        }
    ]
    out = _enforce_denial_stance(ledger, ["BaFin supervision"])
    assert out[0]["status"] == "denied"
    assert out[0]["denial_level"] == "direct"


def test_enforce_denial_stance_dict_without_denial_level_key_defaults_direct():
    """A DeniedConcept dict missing the denial_level key entirely (a row
    persisted before ADR-064, model_dump()'d without the new field) mirrors
    "direct" — the same back-compat default the schema itself uses."""
    from applire.services.keyword_ledger import _enforce_denial_stance

    ledger = [
        {
            "concept": "Kubernetes", "surface_forms": ["Kubernetes"],
            "sources": ["required"], "fit_weight": 1.0, "status": "direct",
            "evidence": "", "claimable": True,
        }
    ]
    out = _enforce_denial_stance(ledger, [{"concept": "Kubernetes"}])
    assert out[0]["status"] == "denied"
    assert out[0]["denial_level"] == "direct"


# ── #249 run-4 — a narrow denial must not tar a broader, independently ──────
# evidenced concept (root cause of the ATS/Oracle contradiction, 2026-07-24).
#
# Run-4 (generated_cvs b9764181-411b-473d-8bf7-e37954fdc32e): the candidate
# denied hands-on RAG PIPELINE INTERNALS configuration ("RAG pipeline",
# "embedding models", "vector store", "reranking", "retrieval pipeline" —
# metadata.denied_concepts) while the profile independently carries a
# LITERAL "Retrieval-Augmented Generation (RAG)" work_experience[0].
# technologies[2] entry. _enforce_denial_stance's compound-containment rule
# ("RAG" is a whole word strictly inside the denied "RAG pipeline") fired
# unconditionally — is_denied_concept always called ledger-build time with
# corpus=None, so the #207 CSS/Tailwind-CSS fail-closed default applied even
# though the vault independently attests the broader term. Result: the ATS
# panel's keywords.present_unsupported carried "RAG" while the SAME document's
# truthfulness report verdicted the SAME string "grounded" from that exact
# technologies[] entry — one fact, two contradictory verdicts.
#
# Fix: build_keyword_ledger now accepts `profile_json` and threads a literal
# vault corpus through to is_denied_concept, so the containment rule's
# fail-closed default only applies when the broad term truly has no
# independent literal vault evidence outside the denied compound.


def test_narrow_denial_does_not_tar_broad_independently_evidenced_concept():
    """The pinned run-4 shape: RAG stays claimable because the vault literally
    carries it in technologies[], outside every denied compound."""
    profile_json = {
        "work_experience": [
            {
                "role": "ML Engineer",
                "technologies": ["Python", "LangChain", "Retrieval-Augmented Generation (RAG)"],
            }
        ]
    }
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "RAG", "direct", ["RAG", "Retrieval-Augmented Generation (RAG)"],
                evidence="built a production RAG pipeline",
            ),
        ],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=[
            "RAG pipeline", "embedding models", "vector store",
            "reranking", "retrieval pipeline",
        ],
        profile_json=profile_json,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["status"] == "direct"
    assert rag["claimable"] is True


def test_narrow_denial_still_tars_broad_term_with_no_independent_evidence():
    """Contrast case: the floor is narrowed, not weakened. Without ANY
    independent literal vault evidence for the broad term, the #207
    CSS/Tailwind-CSS fail-closed containment rule still applies."""
    profile_json = {"work_experience": [{"role": "Engineer", "technologies": ["Python"]}]}
    ledger = build_keyword_ledger(
        classifications=[
            _cls("RAG", "partial", ["RAG"], evidence="adjacency guess"),
        ],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["RAG pipeline"],
        profile_json=profile_json,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert rag["claimable"] is False


def test_profile_json_none_or_absent_is_a_noop_back_compat():
    """No profile_json (legacy callers) → corpus=None → unchanged fail-closed
    behaviour, exactly as before this fix."""
    ledger_no_arg = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="built RAG")],
        required_skills=["RAG"], nice_to_have_skills=[], keywords=[],
        denied_concepts=["RAG pipeline"],
    )
    ledger_none = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="built RAG")],
        required_skills=["RAG"], nice_to_have_skills=[], keywords=[],
        denied_concepts=["RAG pipeline"], profile_json=None,
    )
    assert ledger_no_arg == ledger_none
    rag = _by_concept(ledger_none)["RAG"]
    assert rag["claimable"] is False


# ── Wave-6 regression — the denial's OWN receipt defeats the denial floor ───
#
# #249 run-4 added `profile_literal_corpus` so the containment rule's
# independent-affirmation check could see real vault evidence instead of
# always fail-closing. Ground truth (live vault, 2026-07-25): the candidate's
# testimony that DENIES a narrow compound ("hands-on embedding model
# configuration") is itself persisted verbatim (`metadata.denied_concepts[]
# .statement`, plus the durable enrichment-history "Noted limit: …" receipt)
# — and you cannot deny embeddings without writing the word "embeddings".
# `profile_literal_corpus` fed that verbatim denial text straight into the
# affirmation corpus, so the denial's own receipt "independently affirmed"
# the very concept it denied. Fix: the corpus must be built from POSITIVE
# vault content only — `metadata.denied_concepts` (concept + statement) and
# denial-receipt enrichment-history changes are excluded.

_DENIAL_STATEMENT = (
    "I have not personally set up or worked hands-on with embeddings tuning, "
    "ranking systems, Prometheus, Grafana, or ELK stacks, and production "
    "logging was not something I owned. Those are honest gaps for me."
)


def _denial_only_profile_json(extra_work_experience=None):
    return {
        "metadata": {
            "denied_concepts": [
                {
                    "concept": "hands-on embedding model configuration",
                    "statement": _DENIAL_STATEMENT,
                    "source": "interview",
                    "date": "2026-07-20",
                },
                {
                    "concept": "hands-on ranking work",
                    "statement": _DENIAL_STATEMENT,
                    "source": "interview",
                    "date": "2026-07-20",
                },
            ],
            "enrichment_history": [
                {
                    "id": "e1",
                    "timestamp": "2026-07-20T00:00:00",
                    "source": "interview",
                    "changes": [
                        {
                            "section": "metadata",
                            "field": "denied_concepts",
                            "action": "added",
                            "old_value": None,
                            "new_value": "hands-on embedding model configuration",
                            "rationale": (
                                "Noted limit: no hands-on hands-on embedding "
                                "model configuration (candidate's own testimony)"
                            ),
                        },
                        {
                            "section": "metadata",
                            "field": "denied_concepts",
                            "action": "added",
                            "old_value": None,
                            "new_value": "hands-on ranking work",
                            "rationale": (
                                "Noted limit: no hands-on hands-on ranking "
                                "work (candidate's own testimony)"
                            ),
                        },
                        # A non-denial change in the SAME turn — must survive
                        # the filter untouched (only denied_concepts changes
                        # are stripped, nothing else in enrichment_history).
                        {
                            "section": "work_experience",
                            "field": "technologies",
                            "action": "merged",
                            "old_value": None,
                            "new_value": "Docker",
                            "rationale": "Merged from interview testimony",
                        },
                    ],
                }
            ],
        },
        "work_experience": extra_work_experience or [
            {"role": "ML Engineer", "technologies": ["Python"]}
        ],
    }


def test_profile_literal_corpus_excludes_denial_concept_and_statement_text():
    """Regression: with no OTHER positive mention, the denial's own receipt
    text must never independently affirm the concept it denies."""
    from applire.services.keyword_ledger import profile_literal_corpus
    from applire.services.profile.reconcile.stance import is_denied_concept

    profile_json = _denial_only_profile_json()
    corpus = profile_literal_corpus(profile_json)
    denials = ["hands-on embedding model configuration", "hands-on ranking work"]

    # Before the fix these were both False (the escape) — must be True now.
    assert is_denied_concept("Embeddings", denials, corpus) is True
    assert is_denied_concept("Ranking", denials, corpus) is True
    # The receipt's own words must be gone from the corpus entirely.
    assert "embedding" not in corpus
    assert "docker" in corpus  # the untouched non-denial change survives


def test_profile_literal_corpus_still_affirms_broad_concept_with_real_evidence():
    """#249 must keep working: genuine POSITIVE vault evidence for the broad
    concept, sitting alongside the exact same denial receipt, still
    independently affirms it."""
    from applire.services.keyword_ledger import profile_literal_corpus
    from applire.services.profile.reconcile.stance import is_denied_concept

    profile_json = _denial_only_profile_json(
        extra_work_experience=[
            {
                "role": "ML Engineer",
                "technologies": ["Python", "LangChain", "Retrieval-Augmented Generation (RAG)"],
            }
        ]
    )
    corpus = profile_literal_corpus(profile_json)
    denials = ["hands-on embedding model configuration", "hands-on ranking work"]

    # RAG is not itself a denied compound and has independent literal evidence.
    assert is_denied_concept("RAG", denials, corpus) is False


def test_profile_literal_corpus_prometheus_style_token_only_in_denial_stays_denied():
    """A token that IS the denial itself (or only ever appears inside denial
    text) must stay denied whether or not a corpus is supplied — this is the
    `_is_denied` branch-1 case (denial contained in the token) that never
    depended on `_independently_affirmed` in the first place."""
    from applire.services.keyword_ledger import profile_literal_corpus
    from applire.services.profile.reconcile.stance import is_denied_concept

    profile_json = _denial_only_profile_json()
    corpus = profile_literal_corpus(profile_json)
    assert is_denied_concept("Prometheus", ["Prometheus"], corpus) is True
    assert is_denied_concept("Prometheus", ["Prometheus"]) is True


def test_profile_literal_corpus_tolerates_missing_and_malformed_metadata():
    """None/absent/malformed metadata never crashes the corpus builder."""
    from applire.services.keyword_ledger import profile_literal_corpus

    assert profile_literal_corpus(None) == ""
    assert profile_literal_corpus({}) == ""
    # metadata=None: rest of the profile still flattens normally, no crash.
    assert (
        profile_literal_corpus({"metadata": None, "work_experience": [{"role": "Engineer"}]})
        == "engineer"
    )
    # metadata present but not a dict, or denied_concepts/enrichment_history
    # malformed shapes: tolerated, must not raise.
    profile_literal_corpus({"metadata": ["oops"], "work_experience": []})
    profile_literal_corpus({"metadata": {"denied_concepts": "oops", "enrichment_history": "oops"}})
    profile_literal_corpus({"metadata": {"enrichment_history": ["oops", {"changes": "oops"}]}})


def test_build_keyword_ledger_integration_denied_concept_stays_gap_not_claimable():
    """Full pipeline: the denied concept comes out of build_keyword_ledger as
    status=gap, claimable=False, even though its own receipt text would have
    "independently affirmed" it under the pre-fix corpus."""
    profile_json = _denial_only_profile_json()
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                "Embeddings", "partial", ["Embeddings", "embedding"],
                evidence="RAG pipelines imply use of embeddings",
            ),
        ],
        required_skills=["Embeddings"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["hands-on embedding model configuration"],
        profile_json=profile_json,
    )
    entry = _by_concept(ledger)["Embeddings"]
    assert entry["status"] == "denied"  # ADR-059 amended 2026-07-27: the floor writes "denied", not "gap"
    assert entry["claimable"] is False
