# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#270 (Tiramisu wave-6, package 1) — cross-document consistency.

Charter run #5 ground truth (``logs/llm/2026-07-25.jsonl``, pinned — not
re-derived here): the CV claimed "Architected and designed the database for
the RAG system deployed on Databricks, with product ownership"; the cover
letter claimed "I have not worked hands-on with retrieval systems". Both were
individually vault-grounded; jointly they contradicted each other on a
load-bearing claim. Root cause: ``services/gap.py::askable_gap_inputs``'s
#260 keyword-liability fold leaked a CLAIMABLE keyword-ledger concept
("retrieval systems", status "direct") into the cover letter's gap-
positioning input, so a strength was positioned as the letter's honest gap.

``profile_json.metadata.denied_concepts`` in run 5 held ONLY: embedding
models, vector store, rerankers, hands-on ranking, hands-on embedding work,
Prometheus, Grafana, ELK stacks, production logging, tracing. "retrieval
systems" was NEVER denied — the verbatim denial statement scopes the
boundary to configuration only: "...The database was designed by me. The
actual configuration - embedding models, vector store, reranking - was done
by our system engineer. My contribution was architecture, database design
and product ownership. So I have not configured embedding models, vector
stores or rerankers myself..."
"""
from __future__ import annotations

from applire.services.cross_document import (
    Conflict,
    ScopedBoundary,
    cross_document_reviewer_prompt_fn,
    exclude_claimable_concepts,
    find_cross_document_conflicts,
    find_scoped_boundaries,
    find_unaddressed_hard_requirements,
    render_cross_document_conflicts_block,
    render_scoped_boundary_block,
    render_unaddressed_hard_requirements_block,
    unaddressed_hard_requirements_positioning,
)

# ---------------------------------------------------------------------------
# Run-5 ground truth fixtures
# ---------------------------------------------------------------------------

RUN5_LEDGER = [
    {
        "concept": "retrieval systems",
        "status": "direct",
        "claimable": True,
        "narrative_backed": False,
        "sources": ["required"],
        "fit_weight": 1.0,
        "surface_forms": ["retrieval systems", "retrieval"],
        "evidence": (
            "Architecture, database design, and product ownership for the RAG "
            "system deployed on Databricks, which inherently involves retrieval "
            "systems."
        ),
    }
]

RUN5_DENIAL_STATEMENT = (
    "The database was designed by me. The actual configuration - embedding "
    "models, vector store, reranking - was done by our system engineer. My "
    "contribution was architecture, database design and product ownership. So "
    "I have not configured embedding models, vector stores or rerankers myself."
)

RUN5_DENIED_CONCEPTS = [
    {"concept": c, "statement": RUN5_DENIAL_STATEMENT, "source": "interview"}
    for c in (
        "embedding models", "vector store", "rerankers", "hands-on ranking",
        "hands-on embedding work", "Prometheus", "Grafana", "ELK stacks",
        "production logging", "tracing",
    )
]

RUN5_CV_DATA = {
    "work_history": [
        {
            "id": "w1",
            "role": "Senior ML Engineer",
            "company": "Acme AI",
            "bullets": [
                "Architected and designed the database for the RAG system "
                "deployed on Databricks, with product ownership.",
            ],
        }
    ]
}

RUN5_LETTER_DATA = {
    "body": {
        "paragraphs": [
            "I am excited to apply for the Machine Learning Engineer role.",
            "In my current role I built the data layer for a Retrieval-Augmented "
            "Generation system on Databricks.",
            "I have not worked hands-on with retrieval systems, though I have "
            "owned the surrounding architecture.",
        ]
    }
}

# Coordinator follow-up (#270(c) — the hole left by Fix A): the FULL run-5
# ledger, standing in for the real 6-entry ledger. "retrieval systems" is the
# only entry the original ground truth quoted verbatim; embeddings/ranking/
# observability are the genuine (claimable: false, required) honest gaps left
# once Fix A correctly removes "retrieval systems" from gap-testimony
# selection — the ones #270(c) requires an explicit positioning decision for.
# Two more claimable entries pad the ledger to 6, exercising that claimable
# concepts are never reported as unaddressed regardless of letter content.
RUN5_LEDGER_FULL = RUN5_LEDGER + [
    {
        "concept": "embeddings",
        "claimable": False,
        "sources": ["required"],
        "fit_weight": 0.9,
        "surface_forms": ["embeddings", "embedding models"],
        "evidence": "",
    },
    {
        "concept": "ranking",
        "claimable": False,
        "sources": ["required"],
        "fit_weight": 0.8,
        "surface_forms": ["ranking", "rerankers"],
        "evidence": "",
    },
    {
        "concept": "observability",
        "claimable": False,
        "sources": ["required"],
        "fit_weight": 0.7,
        "surface_forms": ["observability", "tracing"],
        "evidence": "",
    },
    {
        "concept": "Databricks",
        "claimable": True,
        "sources": ["required"],
        "fit_weight": 0.6,
        "surface_forms": ["Databricks"],
        "evidence": "Deployed the RAG system on Databricks.",
    },
    {
        "concept": "Python",
        "claimable": True,
        "sources": ["nice_to_have"],
        "fit_weight": 0.3,
        "surface_forms": ["Python"],
        "evidence": "Used Python throughout.",
    },
]


# ---------------------------------------------------------------------------
# Mandatory case 1 — the run-5 regression fixture, verbatim
# ---------------------------------------------------------------------------


def test_run5_regression_flags_bare_denial_of_claimable_retrieval_systems():
    conflicts = find_cross_document_conflicts(
        RUN5_CV_DATA,
        RUN5_LETTER_DATA,
        keyword_ledger=RUN5_LEDGER,
        denied_concepts=RUN5_DENIED_CONCEPTS,
    )
    bare_denials = [c for c in conflicts if c.kind == "bare_denial_of_claimable"]
    assert bare_denials, "the bare denial of a claimable concept must be flagged"
    hit = bare_denials[0]
    assert hit.concept == "retrieval systems"
    assert "retrieval systems" in hit.quote
    # The remedy must carry the scoped evidence as its recommendation — never
    # instruct a plain "add the keyword", and never a rewrite itself.
    assert "RAG system deployed on Databricks" in hit.remedy


def test_run5_regression_conflict_is_not_flagged_as_claimable_false_gap():
    """The concept must be treated as CLAIMABLE throughout — a #270 regression
    would silently reclassify it as an honest gap instead of flagging it."""
    conflicts = find_cross_document_conflicts(
        RUN5_CV_DATA,
        RUN5_LETTER_DATA,
        keyword_ledger=RUN5_LEDGER,
        denied_concepts=RUN5_DENIED_CONCEPTS,
    )
    assert all(c.concept == "retrieval systems" for c in conflicts)


# ---------------------------------------------------------------------------
# Mandatory case 2 — honest denial of a genuinely non-claimable concept
# ---------------------------------------------------------------------------


def test_honest_denial_of_non_claimable_concept_is_never_flagged():
    ledger = [
        {
            "concept": "embeddings",
            "claimable": False,
            "sources": ["required"],
            "fit_weight": 0.6,
            "surface_forms": ["embeddings", "embedding models"],
            "evidence": "",
        },
        {
            "concept": "observability",
            "claimable": False,
            "sources": ["nice_to_have"],
            "fit_weight": 0.2,
            "surface_forms": ["observability"],
            "evidence": "",
        },
    ]
    letter_data = {
        "body": {
            "paragraphs": [
                "I have not worked with embeddings or observability tooling "
                "directly, but I have owned the surrounding data architecture.",
            ]
        }
    }
    conflicts = find_cross_document_conflicts(
        {}, letter_data, keyword_ledger=ledger, denied_concepts=[],
    )
    assert conflicts == []


# ---------------------------------------------------------------------------
# Mandatory case 3 — Fix A: claimable concepts excluded from gap positioning
# ---------------------------------------------------------------------------


def test_exclude_claimable_concepts_drops_claimable_liability():
    ledger = [
        {
            "concept": "retrieval systems",
            "claimable": True,
            "surface_forms": ["retrieval systems", "retrieval"],
        }
    ]
    gap_inputs = ["retrieval systems", "regulated industries experience"]
    result = exclude_claimable_concepts(gap_inputs, ledger)
    assert result == ["regulated industries experience"]


def test_exclude_claimable_concepts_matches_via_surface_form():
    """A gap-input label matching a claimable entry's SURFACE FORM (not its
    canonical concept string) must still be excluded."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": True,
            "surface_forms": ["RAG", "retrieval-augmented generation"],
        }
    ]
    result = exclude_claimable_concepts(["RAG"], ledger)
    assert result == []


def test_exclude_claimable_concepts_keeps_genuine_category_c_gap():
    """A genuinely non-claimable category-C gap (#255/US264) must survive
    unchanged — no regression of the gap-transfer-argument selection."""
    ledger = [
        {"concept": "retrieval systems", "claimable": True, "surface_forms": []},
    ]
    gap_inputs = ["regulated industries experience"]
    assert exclude_claimable_concepts(gap_inputs, ledger) == gap_inputs


def test_exclude_claimable_concepts_empty_ledger_is_noop():
    assert exclude_claimable_concepts(["some gap"], []) == ["some gap"]
    assert exclude_claimable_concepts(["some gap"], None) == ["some gap"]


# ---------------------------------------------------------------------------
# find_scoped_boundaries
# ---------------------------------------------------------------------------


def test_find_scoped_boundaries_fires_when_surface_form_in_denial_statement():
    """A claimable concept whose surface form literally appears in a denial's
    verbatim statement is a scoped boundary — the vault holds both halves."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": True,
            "surface_forms": ["RAG pipelines", "RAG"],
            "evidence": "Built and owned the RAG pipeline data layer on Databricks.",
        }
    ]
    denied_concepts = [
        {
            "concept": "hands-on embedding work",
            "statement": (
                "I designed the database for the RAG pipeline but did not "
                "configure the embedding models myself."
            ),
            "source": "interview",
        }
    ]
    boundaries = find_scoped_boundaries(ledger, denied_concepts)
    assert len(boundaries) == 1
    b = boundaries[0]
    assert isinstance(b, ScopedBoundary)
    assert b.concept == "RAG pipelines"
    assert "RAG pipeline data layer" in b.evidence
    assert "embedding models" in b.denial_statement


def test_find_scoped_boundaries_none_when_denial_unrelated():
    ledger = [
        {
            "concept": "Kubernetes",
            "claimable": True,
            "surface_forms": ["Kubernetes", "K8s"],
            "evidence": "Ran production Kubernetes clusters.",
        }
    ]
    denied_concepts = [
        {"concept": "Prometheus", "statement": "I have not used Prometheus.", "source": "interview"}
    ]
    assert find_scoped_boundaries(ledger, denied_concepts) == []


def test_find_scoped_boundaries_skips_non_claimable_entries():
    ledger = [
        {
            "concept": "retrieval systems",
            "claimable": False,
            "surface_forms": ["retrieval systems"],
            "evidence": "",
        }
    ]
    denied_concepts = [{"concept": "retrieval", "statement": "no retrieval work", "source": "interview"}]
    assert find_scoped_boundaries(ledger, denied_concepts) == []


# ---------------------------------------------------------------------------
# find_unaddressed_hard_requirements
# ---------------------------------------------------------------------------


def test_find_unaddressed_hard_requirements_returns_unmet_required_concepts():
    ledger = [
        {"concept": "Kubernetes", "claimable": False, "sources": ["required"], "fit_weight": 0.9, "surface_forms": ["Kubernetes"]},
        {"concept": "GraphQL", "claimable": False, "sources": ["required"], "fit_weight": 0.4, "surface_forms": ["GraphQL"]},
        # Not required — never reported even though unmet.
        {"concept": "Rust", "claimable": False, "sources": ["nice_to_have"], "fit_weight": 0.9, "surface_forms": ["Rust"]},
        # Claimable — never reported (it is not a gap at all).
        {"concept": "Python", "claimable": True, "sources": ["required"], "fit_weight": 0.9, "surface_forms": ["Python"]},
    ]
    letter_data = {"body": {"paragraphs": ["I bring strong Python experience."]}}
    result = find_unaddressed_hard_requirements(ledger, letter_data)
    concepts = {e["concept"] for e in result}
    assert concepts == {"Kubernetes", "GraphQL"}


def test_find_unaddressed_hard_requirements_empty_when_addressed():
    ledger = [
        {"concept": "Kubernetes", "claimable": False, "sources": ["required"], "fit_weight": 0.9, "surface_forms": ["Kubernetes"]},
    ]
    letter_data = {"body": {"paragraphs": ["I am eager to grow into Kubernetes."]}}
    assert find_unaddressed_hard_requirements(ledger, letter_data) == []


def test_find_unaddressed_hard_requirements_caps_at_three_highest_weight():
    ledger = [
        {"concept": f"Gap{i}", "claimable": False, "sources": ["required"], "fit_weight": w, "surface_forms": [f"Gap{i}"]}
        for i, w in enumerate([0.9, 0.8, 0.7, 0.6, 0.5], start=1)
    ]
    result = find_unaddressed_hard_requirements(ledger, {"body": {"paragraphs": []}})
    assert len(result) == 3
    assert [e["concept"] for e in result] == ["Gap1", "Gap2", "Gap3"]


# ---------------------------------------------------------------------------
# Mandatory case 5 — unicode: curly apostrophe negation
# ---------------------------------------------------------------------------


def test_curly_apostrophe_havent_is_detected_as_negation():
    ledger = [
        {
            "concept": "retrieval systems",
            "claimable": True,
            "surface_forms": ["retrieval systems"],
            "evidence": "Owned the RAG data layer end to end.",
        }
    ]
    letter_data = {
        "body": {
            "paragraphs": [
                # U+2019 RIGHT SINGLE QUOTATION MARK, not ASCII '
                "I haven’t worked hands-on with retrieval systems."
            ]
        }
    }
    conflicts = find_cross_document_conflicts(
        {}, letter_data, keyword_ledger=ledger, denied_concepts=[],
    )
    assert any(c.kind == "bare_denial_of_claimable" for c in conflicts)


# ---------------------------------------------------------------------------
# Mandatory case 6 — idempotence / no-op safety
# ---------------------------------------------------------------------------


def test_find_cross_document_conflicts_handles_none_and_empty_inputs():
    assert find_cross_document_conflicts(None, None, keyword_ledger=None, denied_concepts=None) == []
    assert find_cross_document_conflicts({}, {}, keyword_ledger=[], denied_concepts=[]) == []
    assert find_cross_document_conflicts({}, {"body": {}}, keyword_ledger=RUN5_LEDGER, denied_concepts=[]) == []


def test_find_scoped_boundaries_handles_none_and_empty_inputs():
    assert find_scoped_boundaries(None, None) == []
    assert find_scoped_boundaries([], []) == []


def test_find_unaddressed_hard_requirements_handles_none_and_empty_inputs():
    assert find_unaddressed_hard_requirements(None, None) == []
    assert find_unaddressed_hard_requirements([], {}) == []


def test_render_helpers_handle_empty_input():
    assert render_scoped_boundary_block([]) == ""
    assert render_cross_document_conflicts_block([]) == ""


# ---------------------------------------------------------------------------
# assert_vs_deny — the second conflict kind
# ---------------------------------------------------------------------------


def test_assert_vs_deny_fires_across_documents():
    ledger = [
        {
            "concept": "Kubernetes",
            "claimable": True,
            "surface_forms": ["Kubernetes"],
            "evidence": "Ran production Kubernetes clusters at Acme.",
        }
    ]
    cv_data = {
        "work_history": [
            {"id": "w1", "role": "SRE", "company": "Acme", "bullets": ["Ran production Kubernetes clusters."]}
        ]
    }
    letter_data = {
        "body": {
            "paragraphs": [
                "I have not worked with Kubernetes in a production setting.",
            ]
        }
    }
    conflicts = find_cross_document_conflicts(
        cv_data, letter_data, keyword_ledger=ledger, denied_concepts=[],
    )
    kinds = {c.kind for c in conflicts}
    assert "assert_vs_deny" in kinds
    assert "bare_denial_of_claimable" in kinds  # the letter-side denial alone


# ---------------------------------------------------------------------------
# Render helpers carry the ground-truth wording constraints
# ---------------------------------------------------------------------------


def test_render_cross_document_conflicts_block_never_calls_concept_a_gap():
    conflicts = [
        Conflict(
            kind="bare_denial_of_claimable",
            concept="retrieval systems",
            surface_form="retrieval systems",
            document="letter",
            location="body.paragraphs[2]",
            quote="I have not worked hands-on with retrieval systems.",
            remedy="Render the scoped claim from its own vault evidence.",
        )
    ]
    block = render_cross_document_conflicts_block(conflicts)
    assert "CLAIMABLE" in block
    assert "never" in block.lower()


def test_render_scoped_boundary_block_names_both_halves():
    boundaries = [
        ScopedBoundary(
            concept="RAG pipelines",
            surface_forms=("RAG pipelines", "RAG"),
            evidence="Built and owned the RAG pipeline data layer.",
            denial_concept="embedding models",
            denial_statement="I did not configure the embedding models myself.",
        )
    ]
    block = render_scoped_boundary_block(boundaries)
    assert "Built and owned the RAG pipeline data layer." in block
    assert "I did not configure the embedding models myself." in block


# ---------------------------------------------------------------------------
# #270(c) follow-up — find_unaddressed_hard_requirements wired into the
# reviewer + writer (previously implemented and unit-tested but wired
# nowhere: dead code, and a real hole given Fix A — see module docstring
# addendum above and RUN5_LEDGER_FULL).
# ---------------------------------------------------------------------------


def test_render_unaddressed_hard_requirements_block_empty():
    assert render_unaddressed_hard_requirements_block([]) == ""


def test_render_unaddressed_hard_requirements_block_names_concepts_and_forbids_assertion():
    entries = [
        {"concept": "embeddings", "evidence": ""},
        {"concept": "ranking", "evidence": ""},
    ]
    block = render_unaddressed_hard_requirements_block(entries)
    assert "embeddings" in block
    assert "ranking" in block
    assert "claimable: false" in block
    assert "never" in block.lower()
    assert "litany" in block.lower()
    assert "silence" in block.lower()


def test_unaddressed_hard_requirements_positioning_shape():
    entries = [{"concept": "observability", "evidence": "some context"}]
    positioning = unaddressed_hard_requirements_positioning(entries)
    assert positioning["required"] is True
    assert positioning["concepts"] == [{"concept": "observability", "evidence": "some context"}]
    assert "litany" in positioning["instruction"].lower()


def test_unaddressed_hard_requirements_positioning_empty_is_empty_dict():
    assert unaddressed_hard_requirements_positioning([]) == {}


def _noop_base_fn(source: str, draft: dict) -> str:
    return "BASE PROMPT"


def test_reviewer_prompt_fn_flags_unaddressed_requirements_on_run5_full_ledger():
    """The coordinator's trace: with 'retrieval systems' correctly excluded by
    Fix A, embeddings/ranking/observability are the genuine honest gaps left —
    and the letter (run-5 verbatim body) never addresses any of them at all.
    The reviewer must be told so, regardless of whether find_gap_testimony
    happened to find a signature-story match for one of them."""
    reviewer_fn = cross_document_reviewer_prompt_fn(
        _noop_base_fn,
        cv_data=RUN5_CV_DATA,
        keyword_ledger=RUN5_LEDGER_FULL,
        denied_concepts=RUN5_DENIED_CONCEPTS,
    )
    prompt = reviewer_fn("source", RUN5_LETTER_DATA)
    assert "embeddings" in prompt
    assert "ranking" in prompt
    assert "observability" in prompt
    # Claimable concepts must never appear in this block.
    assert "UNADDRESSED HARD REQUIREMENTS" in prompt
    unaddressed_section = prompt.split("UNADDRESSED HARD REQUIREMENTS")[1]
    assert "Databricks" not in unaddressed_section
    assert "Python" not in unaddressed_section


def test_reviewer_prompt_fn_omits_block_when_letter_addresses_all_three():
    letter_data = {
        "body": {
            "paragraphs": [
                "I have not directly configured embeddings or reranking models "
                "myself, and observability tooling sits outside my own remit, "
                "though I have owned the surrounding architecture end to end.",
            ]
        }
    }
    reviewer_fn = cross_document_reviewer_prompt_fn(
        _noop_base_fn,
        cv_data=RUN5_CV_DATA,
        keyword_ledger=RUN5_LEDGER_FULL,
        denied_concepts=RUN5_DENIED_CONCEPTS,
    )
    prompt = reviewer_fn("source", letter_data)
    assert "UNADDRESSED HARD REQUIREMENTS" not in prompt


def test_reviewer_prompt_fn_caps_unaddressed_at_three_and_logs_drop(caplog):
    ledger = [
        {"concept": f"Gap{i}", "claimable": False, "sources": ["required"], "fit_weight": w, "surface_forms": [f"Gap{i}"]}
        for i, w in enumerate([0.9, 0.8, 0.7, 0.6], start=1)
    ]
    reviewer_fn = cross_document_reviewer_prompt_fn(
        _noop_base_fn, cv_data={}, keyword_ledger=ledger, denied_concepts=[],
    )
    with caplog.at_level("INFO"):
        prompt = reviewer_fn("source", {"body": {"paragraphs": []}})
    assert "Gap1" in prompt and "Gap2" in prompt and "Gap3" in prompt
    assert "Gap4" not in prompt
    assert any("dropped" in r.message.lower() for r in caplog.records)


def test_writer_gets_the_same_unaddressed_list_before_any_draft_exists():
    """The writer prompt is built BEFORE a letter_data draft exists — passing
    letter_data=None means every required honest gap is trivially
    'unaddressed', which is exactly the pre-draft input the writer needs."""
    pre_draft = find_unaddressed_hard_requirements(RUN5_LEDGER_FULL, None)
    concepts = {e["concept"] for e in pre_draft}
    assert concepts == {"embeddings", "ranking", "observability"}
    block = render_unaddressed_hard_requirements_block(pre_draft)
    assert "embeddings" in block and "ranking" in block and "observability" in block
