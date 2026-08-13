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

import pytest

from applire.services.keyword_ledger import DENIED_EVIDENCE
from applire.services.cross_document import (
    collect_stated_limits,
    exclude_claimable_concepts,
    find_unaddressed_hard_requirements,
    render_stated_limits_block,
    unaddressed_requirements_reviewer_prompt_fn,
    render_unaddressed_hard_requirements_block,
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
        "status": "denied",
        "evidence": DENIED_EVIDENCE,
    },
    {
        "concept": "ranking",
        "claimable": False,
        "sources": ["required"],
        "fit_weight": 0.8,
        "surface_forms": ["ranking", "rerankers"],
        "status": "denied",
        "evidence": DENIED_EVIDENCE,
    },
    {
        "concept": "observability",
        "claimable": False,
        "sources": ["required"],
        "fit_weight": 0.7,
        "surface_forms": ["observability", "tracing"],
        "status": "denied",
        "evidence": DENIED_EVIDENCE,
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






# ---------------------------------------------------------------------------
# Mandatory case 2 — honest denial of a genuinely non-claimable concept
# ---------------------------------------------------------------------------




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
# collect_stated_limits — replaced find_scoped_boundaries (charter run #8)
# ---------------------------------------------------------------------------


def test_collect_stated_limits_returns_the_candidates_own_words():
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
    assert collect_stated_limits(denied_concepts) == [
        "I designed the database for the RAG pipeline but did not "
        "configure the embedding models myself."
    ]


def test_collect_stated_limits_dedupes_one_answer_persisted_under_many_concepts():
    """Run-8 ground truth: ONE interview answer is persisted once per concept it
    denies — five records, one sentence. The writer must see it once, not five
    times."""
    statement = (
        "Nein, mit IFS oder BRC habe ich keine Erfahrung. Ich habe auch nie "
        "direkt fuer Lebensmittelkunden produziert."
    )
    denied_concepts = [
        {"concept": c, "statement": statement, "source": "interview"}
        for c in ("IFS", "BRC", "Lebensmittelindustrie", "Produktion fuer Lebensmittelkunden")
    ]
    assert collect_stated_limits(denied_concepts) == [statement]


def test_collect_stated_limits_never_pairs_a_limit_with_a_concept():
    """THE run-8 regression (charter run #8, operations_marcus_de).

    ``find_scoped_boundaries`` decided which claimable concept a denial "limits"
    by text overlap, and emitted four boundaries against this exact data — ISO
    9001, Produktion, Supply Chain, Qualitaet — every one of them a load-bearing
    STRENGTH the candidate names inside their own honest denial. The writer was
    then told to render "both halves" for each, so it invented limits that do not
    exist and the delivered letter disclaimed the candidate's best evidence.

    The contract now: this function returns statements and NOTHING else. There is
    no concept attached to a limit, so there is no false pairing to emit. Whether
    a limit bears on a given sentence is the model's judgement, and the block in
    :func:`render_stated_limits_block` tells it how to make that call.
    """
    ledger = [
        {"concept": c, "claimable": True, "surface_forms": [c], "evidence": f"{c} evidence"}
        for c in ("ISO 9001", "Produktion", "Supply Chain", "Qualitaet")
    ]
    denied_concepts = [
        {
            "concept": "IFS",
            "statement": (
                "Nein, mit IFS oder BRC habe ich keine Erfahrung. Was ich mitbringe: "
                "Hygiene- und Dokumentationsdisziplin aus der Fertigung und zehn Jahre "
                "ISO-9001-Audit-Praxis."
            ),
            "source": "interview",
        },
        {
            "concept": "direkte Vertriebsverantwortung",
            "statement": (
                "Direkte Vertriebsverantwortung hatte ich nicht. Bei Weberit bin ich "
                "aber die Schnittstelle zu Einkauf, Qualitaetssicherung und Supply Chain."
            ),
            "source": "interview",
        },
    ]
    limits = collect_stated_limits(denied_concepts)
    assert len(limits) == 2

    block = render_stated_limits_block(limits)
    # Both statements reach the writer verbatim — nothing is dropped.
    for text in limits:
        assert text in block
    # But no claimable concept is ever named as limited by them.
    for entry in ledger:
        assert f"- {entry['concept']}\n" not in block
        assert entry["evidence"] not in block
    assert "POSITIVE" not in block
    assert "STATED LIMIT (candidate" not in block


def test_render_stated_limits_block_forbids_inventing_a_limit():
    """The one rule that does the job the matcher could not: a concept named
    inside a denial as something the candidate HAS is a strength, not a limit."""
    block = render_stated_limits_block(["Mit IFS habe ich keine Erfahrung."]).lower()
    assert "strength" in block
    assert "invent" in block
    assert "claimable" in block


# ---------------------------------------------------------------------------
# find_unaddressed_hard_requirements
# ---------------------------------------------------------------------------


def test_find_unaddressed_hard_requirements_returns_unmet_required_concepts():
    ledger = [
        {"concept": "Kubernetes", "claimable": False, "status": "denied", "evidence": DENIED_EVIDENCE, "sources": ["required"], "fit_weight": 0.9, "surface_forms": ["Kubernetes"]},
        {"concept": "GraphQL", "claimable": False, "status": "denied", "evidence": DENIED_EVIDENCE, "sources": ["required"], "fit_weight": 0.4, "surface_forms": ["GraphQL"]},
        # ADR-074: required, unclaimable, and NOTHING on it — never asked. Excluded
        # from generation, told to the candidate instead.
        {"concept": "Terraform", "claimable": False, "status": "gap", "evidence": "", "sources": ["required"], "fit_weight": 0.9, "surface_forms": ["Terraform"]},
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
        {"concept": f"Gap{i}", "claimable": False, "status": "denied", "evidence": DENIED_EVIDENCE, "sources": ["required"], "fit_weight": w, "surface_forms": [f"Gap{i}"]}
        for i, w in enumerate([0.9, 0.8, 0.7, 0.6, 0.5], start=1)
    ]
    result = find_unaddressed_hard_requirements(ledger, {"body": {"paragraphs": []}})
    assert len(result) == 3
    assert [e["concept"] for e in result] == ["Gap1", "Gap2", "Gap3"]


# ---------------------------------------------------------------------------
# Mandatory case 5 — unicode: curly apostrophe negation
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Mandatory case 6 — idempotence / no-op safety
# ---------------------------------------------------------------------------




def test_collect_stated_limits_handles_none_and_empty_inputs():
    assert collect_stated_limits(None) == []
    assert collect_stated_limits([]) == []
    assert collect_stated_limits([{"concept": "", "statement": ""}]) == []
    assert collect_stated_limits(["a bare string denial"]) == ["a bare string denial"]


def test_find_unaddressed_hard_requirements_handles_none_and_empty_inputs():
    assert find_unaddressed_hard_requirements(None, None) == []
    assert find_unaddressed_hard_requirements([], {}) == []


def test_render_helpers_handle_empty_input():
    assert render_stated_limits_block([]) == ""
    assert render_unaddressed_hard_requirements_block([]) == ""


# ---------------------------------------------------------------------------
# assert_vs_deny — the second conflict kind
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Render helpers carry the ground-truth wording constraints
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# #270(c) follow-up — find_unaddressed_hard_requirements wired into the
# reviewer + writer (previously implemented and unit-tested but wired
# nowhere: dead code, and a real hole given Fix A — see module docstring
# addendum above and RUN5_LEDGER_FULL).
# ---------------------------------------------------------------------------


def test_render_unaddressed_hard_requirements_block_empty():
    assert render_unaddressed_hard_requirements_block([]) == ""


OBSERVABILITY_ENTRY = {
    "concept": "observability",
    "claimable": False,
    "sources": ["required"],
    "fit_weight": 0.7,
    "surface_forms": [
        "observability", "Prometheus", "Grafana", "ELK",
        "production logging", "tracing",
    ],
    "status": "denied",
    "evidence": DENIED_EVIDENCE,
}

EMBEDDINGS_ENTRY = {
    "concept": "embeddings",
    "claimable": False,
    "sources": ["required"],
    "fit_weight": 0.9,
    "surface_forms": ["embeddings", "embedding models", "embedding work"],
    "status": "denied",
    "evidence": DENIED_EVIDENCE,
}

RANKING_ENTRY = {
    "concept": "ranking",
    "claimable": False,
    "sources": ["required"],
    "fit_weight": 0.8,
    "surface_forms": ["ranking", "rerankers"],
    "status": "denied",
    "evidence": DENIED_EVIDENCE,
}


def test_render_unaddressed_hard_requirements_block_names_concepts_and_forbids_assertion():
    entries = [
        {"concept": "embeddings", "evidence": ""},
        {"concept": "ranking", "evidence": ""},
    ]
    block = render_unaddressed_hard_requirements_block(entries)
    assert "embeddings" in block
    assert "ranking" in block
    assert "does NOT have the named requirement" in block
    assert "never" in block.lower()
    assert "litany" in block.lower()
    assert "silence" in block.lower()


def test_the_positioning_snapshot_builder_is_gone():
    """ADR-021 amended 2026-08-13 (#526). `unaddressed_hard_requirements_positioning`
    snapshotted the unmet-requirement list into the letter's `grounding_source`,
    which `review_and_refine` hands unchanged to the reviewer AND the corrector on
    every round — so a statement about the CURRENT DRAFT was computed once with
    `letter_data=None` and then asserted for the whole loop, overruling the per-round
    wrapper below when the two disagreed. The function is deleted, not deprecated:
    a re-import here is the snapshot growing back."""
    import applire.services.cross_document as xd

    assert not hasattr(xd, "unaddressed_hard_requirements_positioning")


def _noop_base_fn(source: str, draft: dict) -> str:
    return "BASE PROMPT"


def test_reviewer_prompt_fn_flags_unaddressed_requirements_on_run5_full_ledger():
    """The coordinator's trace: with 'retrieval systems' correctly excluded by
    Fix A, embeddings/ranking/observability are the genuine honest gaps left —
    and the letter (run-5 verbatim body) never addresses any of them at all.
    The reviewer must be told so, regardless of whether find_gap_testimony
    happened to find a signature-story match for one of them."""
    reviewer_fn = unaddressed_requirements_reviewer_prompt_fn(
        _noop_base_fn,
        keyword_ledger=RUN5_LEDGER_FULL,
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
    reviewer_fn = unaddressed_requirements_reviewer_prompt_fn(
        _noop_base_fn,
        keyword_ledger=RUN5_LEDGER_FULL,
    )
    prompt = reviewer_fn("source", letter_data)
    assert "UNADDRESSED HARD REQUIREMENTS" not in prompt


def test_reviewer_prompt_fn_caps_unaddressed_at_three_and_logs_drop(caplog):
    ledger = [
        {"concept": f"Gap{i}", "claimable": False, "status": "denied", "evidence": DENIED_EVIDENCE, "sources": ["required"], "fit_weight": w, "surface_forms": [f"Gap{i}"]}
        for i, w in enumerate([0.9, 0.8, 0.7, 0.6], start=1)
    ]
    reviewer_fn = unaddressed_requirements_reviewer_prompt_fn(
        _noop_base_fn, keyword_ledger=ledger,
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


def test_the_block_never_quotes_a_span_out_of_a_denial_statement():
    """ADR-062: `find_denial_transfer_bridge` used to extract the "what I do
    bring instead" SENTENCE out of a denial statement and quote it here, on a
    position rule plus three prose guards. Which part of a paragraph is the
    transfer argument is a question about meaning; the STATED LIMITS block now
    hands over the whole statement and the model reads it. Nothing in this
    block may quote a fragment of one again."""
    denied = [
        {"concept": "embeddings",
         "statement": ("I have not configured embedding models myself. What I do bring "
                       "is the architecture and the database design behind them."),
         "source": "interview"},
    ]
    del denied  # the parameter is gone; the statements reach the prompt via STATED LIMITS
    block = render_unaddressed_hard_requirements_block([EMBEDDINGS_ENTRY])
    assert "What I do bring" not in block
    assert "TRANSFER-ARGUMENT TESTIMONY" not in block
    # ...and it still tells the writer where the candidate's own words live.
    assert "STATED LIMITS" in block


def test_the_block_no_longer_accepts_a_denied_concepts_argument():
    """`denied_concepts` was accepted and unused from ADR-062 (2026-07-28) until it
    was deleted on 2026-08-13 (ADR-062 clause 3). A parameter every caller passes and
    no body reads is a control that cannot fire; the candidate's own words reach the
    prompt whole via STATED LIMITS. Re-adding it is the extraction growing back."""
    import inspect

    sig = inspect.signature(render_unaddressed_hard_requirements_block)
    assert list(sig.parameters) == ["entries"]
    with pytest.raises(TypeError):
        render_unaddressed_hard_requirements_block([OBSERVABILITY_ENTRY], [{"concept": "x"}])


def test_the_block_never_calls_an_adjacent_partial_a_plain_gap():
    """ADR-062 clause 4 — the run-8 non-termination. This block and the Keyword
    Ledger block reach the reviewer in one prompt, and they used to disagree:
    this one said "these are honest gaps (claimable: false)" while including
    adjacent partials the ledger marks claimable, so the reviewer was told the
    same concept both is and is not a gap and could never approve a draft."""
    adjacent = {**EMBEDDINGS_ENTRY, "claimable": True,
                "adjacent_evidence": "vector database schema design"}
    block = render_unaddressed_hard_requirements_block([adjacent])
    assert "claimable: false" not in block
    assert "vector database schema design" in block
    # The one thing that must survive: never assert the requirement's own term.
    assert "never" in block.lower()
    # And a claimable concept that is NOT listed here is not a gap either.
    assert "not listed here is not a gap" in block.lower()








# ---------------------------------------------------------------------------
# Wave-7 (#278) — negation misattribution: co-occurrence is not attribution.
#
# Charter run #6 ground truth (``backend/logs/llm/2026-07-26.jsonl``, pinned,
# never copied verbatim — reproduced here as an invented fixture of the SAME
# shape): a CV bullet reads "...taught a curriculum of three
# software-engineering courses ... to a team of engineers from
# natural-science backgrounds with no prior IT/software experience". The
# claimable concept 'Software engineering' surface-matches the EARLY, POSITIVE
# "software-engineering courses" text; the negation "no" many words later
# belongs to a DIFFERENT noun phrase (the trainees' own background) and must
# never be attributed to the concept's own occurrence. The second real
# defect ('AI', surface_forms ['AI', 'Artificial Intelligence']) is a bare
# substring collision: 'ai' is a literal substring of 'domain' and of
# 'claim' — ``surface_present``'s plain ``.find()`` search matches both,
# with zero relation to the word 'AI'.
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Wave-7 (#277) — DELETED 2026-07-28 (charter run #8).
#
# The ``unqualified_cv_vs_scoped_letter`` conflict kind was built on
# ``find_scoped_boundaries``, whose pairing of a denial to a claimable concept
# was wrong on real data in the only direction that matters (an honest denial
# names the adjacent strengths that transfer, so the concepts it overlaps
# hardest are the ones it does NOT limit). The conflict kind inherited every
# false boundary as a false instruction to the reviewer, so it went with it
# rather than being repaired on a broken foundation. It never fired on any
# charter run. See ``collect_stated_limits`` for what replaced the primitive.
#
# What it aimed at — "the CV over-claims what the letter honestly scopes" — is
# real, and is now the reviewers' job on both sides, not a text matcher's.
# ---------------------------------------------------------------------------
