# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-100 amended 2026-11-04 — M-2: a denial is bound to the requirement by id.

The ledger's denial floor matched a persisted denial to a requirement with the
string matcher of ``stance.py`` (alias groups, head nouns, containment over the
row's concept and surface forms). A German denial ("Referenzdesigns") reached the
English requirement ("Reference designs") only when the gap classifier happened to
list the German spelling among the row's surface forms — the invariance gate of
2026-11-04 showed it `denied` in 2 of 8 cross-language runs on luna and 0 of 4
zh-en runs on Claude.

Now the classifier sees the active recorded denials, each keyed by the record's
own ``concept``, and cites one in ``denied_by`` (a Judgment, any language). The
floor checks only that the cited id is a live record (a Fact). The string matcher
stays as the fallback for a row the classifier did not bind.

All fixtures are synthetic: a fictional integration lead, fictional employers.
"""

from __future__ import annotations

import json

import pytest

from applire.prompts.gap_analysis import (
    SYSTEM_PROMPT,
    build_user_prompt,
    recorded_denials_view,
)
from applire.services.cross_document import select_jd_relevant_limits
from applire.services.gap import _apply_floors_to_merged, ledger_input_from_classification
from applire.services.gap_coverage import _is_declined
from applire.services.gap_inference import PreClassification
from applire.services.keyword_ledger import (
    DENIED_BY_KEY,
    DENIED_EVIDENCE,
    DENIAL_FLOOR_EVIDENCE,
    _enforce_denial_stance,
    build_keyword_ledger,
)

DE_DENIAL = {
    "concept": "Referenzdesigns",
    "statement": "Referenzdesigns für Integrationsplattformen habe ich nie erstellt.",
    "source": "interview",
    "date": "2026-11-01",
    "denial_level": "direct",
}
ZH_DENIAL = {
    "concept": "参考设计",
    "statement": "我从未为集成平台创建过参考设计。",
    "source": "interview",
    "date": "2026-11-01",
    "denial_level": "direct",
}
#: The 11-04 E2E shape: the record names the requirement plus a qualifier.
E2E_DENIAL = {
    "concept": "Referenzdesigns für Integrationsplattformen",
    "statement": "Referenzdesigns für Integrationsplattformen habe ich nie erstellt.",
    "source": "interview",
    "date": "2026-11-04",
    "denial_level": "direct",
}

VAULT = {
    "personal_info": {"name": "Jorinde Albrecht", "location": "Kiel"},
    "work_experience": [
        {
            "id": "w-fennbruck",
            "company": "Fennbruck Analytik GmbH",
            "role": "Leiterin Labor-IT",
            "start_date": "2019-04",
            "responsibilities": [
                "Anbindung des Probeneingangs an das Auftragssystem über eine Schnittstellenplattform"
            ],
            "technologies": ["Python", "SQL"],
        }
    ],
    "skills": [{"name": "Python"}, {"name": "SQL"}],
    "metadata": {"denied_concepts": [DE_DENIAL]},
}


def _gap_row(concept: str, **extra) -> dict:
    return {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
        **extra,
    }


# ── the floor: the bound arm ────────────────────────────────────────────────


@pytest.mark.parametrize("record", [DE_DENIAL, ZH_DENIAL, E2E_DENIAL])
def test_a_row_bound_to_a_live_record_is_denied_in_any_language(record):
    """THE M-2 property: the English requirement, the record in German or
    Chinese — the string matcher sees no relation, the binding does."""
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: record["concept"]})
    [out] = _enforce_denial_stance([row], [record], None)
    assert out["status"] == "denied"
    assert out["claimable"] is False
    assert out["evidence"] == DENIED_EVIDENCE
    assert out["denial_level"] == "direct"
    assert out[DENIED_BY_KEY] == record["concept"]


@pytest.mark.parametrize("record", [DE_DENIAL, ZH_DENIAL])
def test_without_the_binding_the_string_matcher_still_misses_cross_language(record):
    """The fallback is unchanged — and blind across languages, which is why the
    binding exists (pins that the arm above is what moved the row)."""
    [out] = _enforce_denial_stance([_gap_row("Reference designs")], [record], None)
    assert out["status"] == "gap"
    assert DENIED_BY_KEY not in out


def test_the_id_is_matched_normalised_not_byte_for_byte():
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "  referenzdesigns "})
    [out] = _enforce_denial_stance([row], [DE_DENIAL], None)
    assert out["status"] == "denied"
    assert out[DENIED_BY_KEY] == "Referenzdesigns"  # the record's own spelling


def test_an_unknown_id_binds_nothing_and_is_dropped():
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzarchitekturen"})
    [out] = _enforce_denial_stance([row], [DE_DENIAL], None)
    assert out["status"] == "gap"
    assert DENIED_BY_KEY not in out


def test_a_withdrawn_record_binds_nothing():
    """G-5: a withdrawn denial has left ``denied_concepts``; the live list is the
    only list the id is checked against."""
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    [out] = _enforce_denial_stance([row], [ZH_DENIAL], None)
    assert out["status"] == "gap"
    assert DENIED_BY_KEY not in out


def test_with_no_live_denial_a_cited_id_is_stripped():
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    [out] = _enforce_denial_stance([row], [], None)
    assert out["status"] == "gap"
    assert DENIED_BY_KEY not in out


def test_the_bound_records_level_is_mirrored_and_partial_wins():
    partial = {**DE_DENIAL, "denial_level": "partial"}
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    [out] = _enforce_denial_stance([row], [partial], None)
    assert out["denial_level"] == "partial"
    # a second record the string matcher DECLARES at partial outranks a direct binding
    row2 = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    declared_partial = {**DE_DENIAL, "concept": "Reference designs", "denial_level": "partial"}
    [out2] = _enforce_denial_stance([row2], [DE_DENIAL, declared_partial], None)
    assert out2["denial_level"] == "partial"


def test_a_binding_on_a_claimable_grade_refuses_the_claim_without_asserting_testimony():
    """The classifier contradicted itself (cited a denial, graded `direct`): the
    fail-safe direction — not claimable, and no "the candidate said no"."""
    row = {
        **_gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"}),
        "status": "direct",
        "claimable": True,
        "evidence": "states it",
        "backing": {"quote": "x", "instrument": "quote"},
    }
    [out] = _enforce_denial_stance([row], [DE_DENIAL], None)
    assert out["claimable"] is False
    assert out["status"] == "gap"
    assert out["evidence"] == DENIAL_FLOOR_EVIDENCE
    assert "denial_level" not in out
    assert "backing" not in out
    assert DENIED_BY_KEY not in out


def test_a_same_language_part_binding_keeps_the_release():
    """#486 / SF-GAP.10 — the NO the string matcher said by itself: a record whose
    words contain the requirement only as a part ("Tailwind CSS" ⊃ "CSS") does not
    deny it when the vault attests it outside that compound. A citation does not
    remove that release."""
    tailwind = {**DE_DENIAL, "concept": "Tailwind CSS", "statement": "Kein Tailwind CSS."}
    row = _gap_row("CSS", **{DENIED_BY_KEY: "Tailwind CSS"})
    [out] = _enforce_denial_stance([row], [tailwind], "css python")
    assert out["status"] == "gap"
    assert out["evidence"] != DENIED_EVIDENCE
    assert DENIED_BY_KEY not in out


def test_an_e2e_shaped_same_language_record_still_binds_when_nothing_releases_it():
    """The record "Reference designs for integration platforms" contains the
    requirement; the vault does not attest it elsewhere — the binding stands
    (the string matcher alone would only floor it, never say `denied`)."""
    record = {**DE_DENIAL, "concept": "Reference designs for integration platforms"}
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: record["concept"]})
    [out] = _enforce_denial_stance([row], [record], "python sql")
    assert out["status"] == "denied"
    [unbound] = _enforce_denial_stance([_gap_row("Reference designs")], [record], "python sql")
    assert unbound["status"] == "gap" and unbound["evidence"] == DENIAL_FLOOR_EVIDENCE


def test_a_persisted_bound_row_is_idempotent_at_a_re_floor():
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    once = _enforce_denial_stance([row], [DE_DENIAL], None)
    twice = _enforce_denial_stance(once, [DE_DENIAL], None)
    assert twice == once


# ── seam 1: build_keyword_ledger (the fresh analysis) ───────────────────────


def _classification(status="gap", **extra):
    return ledger_input_from_classification(
        {"requirement": "Reference designs", "status": status, "reason": "", **extra}
    )


def test_SEAM_build_keyword_ledger_carries_the_citation_to_the_floor():
    ledger = build_keyword_ledger(
        [_classification(denied_by="Referenzdesigns")],
        ["Reference designs"],
        [],
        [],
        denied_concepts=[DE_DENIAL],
        profile_json=VAULT,
    )
    [row] = [r for r in ledger if r["concept"] == "Reference designs"]
    assert row["status"] == "denied"
    assert row[DENIED_BY_KEY] == "Referenzdesigns"


def test_SEAM_build_keyword_ledger_without_a_citation_is_unchanged():
    ledger = build_keyword_ledger(
        [_classification()],
        ["Reference designs"],
        [],
        [],
        denied_concepts=[DE_DENIAL],
        profile_json=VAULT,
    )
    [row] = [r for r in ledger if r["concept"] == "Reference designs"]
    assert row["status"] == "gap"


def test_SEAM_build_keyword_ledger_quote_arm_never_sees_a_bound_row():
    """ADR-100 cl. 7 + M-2: the denial floor runs before the backing predicate and
    drops `backing`, so a bound row cannot be backed by a quote — the quote arm is
    bound by order."""
    ledger = build_keyword_ledger(
        [
            _classification(
                denied_by="Referenzdesigns",
                evidence_quote="Anbindung des Probeneingangs an das Auftragssystem",
            )
        ],
        ["Reference designs"],
        [],
        [],
        denied_concepts=[DE_DENIAL],
        profile_json=VAULT,
    )
    [row] = [r for r in ledger if r["concept"] == "Reference designs"]
    assert row["status"] == "denied"
    assert "backing" not in row


def test_SEAM_prefix_duplicates_keep_a_members_binding():
    ledger = build_keyword_ledger(
        [
            ledger_input_from_classification(
                {"requirement": "Reference designs", "status": "gap", "reason": ""}
            ),
            ledger_input_from_classification(
                {
                    "requirement": "Reference designs for platforms",
                    "status": "gap",
                    "reason": "",
                    "denied_by": "Referenzdesigns",
                }
            ),
        ],
        ["Reference designs", "Reference designs for platforms"],
        [],
        [],
        denied_concepts=[DE_DENIAL],
        profile_json=VAULT,
    )
    assert all(r["status"] == "denied" for r in ledger if "Reference designs" in r["concept"])


# ── seam 2: the answer-driven merge re-floor (gap._apply_floors_to_merged) ──


def test_SEAM_merge_re_floor_honours_a_carried_binding():
    row = _gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})
    [out] = _apply_floors_to_merged([row], [0], denied_concepts=[DE_DENIAL], profile_json=VAULT)
    assert out["status"] == "denied"


# ── readers of the bound row ────────────────────────────────────────────────


def test_READER_topic_chip_reads_a_bound_member_as_declined():
    """gap_coverage reads the row's status; a bound row is `denied`, so the member
    the German record rules out is declined, not open."""
    [row] = _enforce_denial_stance(
        [_gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})], [DE_DENIAL], None
    )
    assert _is_declined("Reference designs", [row], ["Referenzdesigns"]) is True
    assert _is_declined("Reference designs", [_gap_row("Reference designs")], ["Referenzdesigns"]) is False


def test_READER_letter_stated_limits_join_a_bound_row_to_its_record():
    """cross_document.select_jd_relevant_limits joined record ↔ row by the
    concept's spelling; a cross-language bound row joins by its id."""
    [row] = _enforce_denial_stance(
        [_gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})], [DE_DENIAL], None
    )
    assert select_jd_relevant_limits([DE_DENIAL], [row]) == [DE_DENIAL["statement"]]
    unbound = {**row}
    unbound.pop(DENIED_BY_KEY)
    assert select_jd_relevant_limits([DE_DENIAL], [unbound]) == []


# ── the classifier's input and contract ─────────────────────────────────────


def test_the_classifier_sees_the_active_denials_with_their_ids():
    profile = {
        **VAULT,
        "metadata": {
            "denied_concepts": [DE_DENIAL, ZH_DENIAL, {**DE_DENIAL, "concept": "referenzdesigns"}],
            "withdrawn_denials": [{**DE_DENIAL, "concept": "Kubernetes", "withdrawn": "2026-11-02"}],
        },
    }
    view = recorded_denials_view(profile)
    assert view == [
        {"id": "Referenzdesigns", "statement": DE_DENIAL["statement"]},
        {"id": "参考设计", "statement": ZH_DENIAL["statement"]},
    ]
    prompt = build_user_prompt(
        {"required_skills": ["Reference designs"], "nice_to_have_skills": [], "keywords": []},
        profile,
        PreClassification(),
    )
    block = prompt.split("RECORDED DENIALS", 1)[1].split("PRE-CLASSIFICATION")[0]
    assert json.dumps("参考设计", ensure_ascii=False) in block
    assert "Kubernetes" not in prompt  # a withdrawn record is never shown
    profile_block = prompt.split("CANDIDATE PROFILE:")[1].split("RECORDED DENIALS")[0]
    assert DE_DENIAL["statement"] not in profile_block  # never a quote source


def test_no_block_without_a_live_denial():
    prompt = build_user_prompt(
        {"required_skills": ["Reference designs"], "nice_to_have_skills": [], "keywords": []},
        {**VAULT, "metadata": {"denied_concepts": []}},
        PreClassification(),
    )
    assert "RECORDED DENIALS" not in prompt


def test_the_schema_asks_for_the_citation_and_the_rule_binds_only_with_gap():
    schema = SYSTEM_PROMPT.split("Respond ONLY")[1]
    assert '"denied_by"' in schema
    assert 'set "denied_by" to that entry\'s "id"' in SYSTEM_PROMPT
    assert 'classify\n    "gap"' in SYSTEM_PROMPT or 'and classify "gap"' in SYSTEM_PROMPT


def test_ledger_input_carries_the_citation_unverified():
    row = ledger_input_from_classification(
        {"requirement": "Reference designs", "status": "gap", "denied_by": "Referenzdesigns"}
    )
    assert row["denied_by"] == "Referenzdesigns"


# ── withdrawal releases (founder ruling WP2-1) ──────────────────────────────


def test_SEAM_merge_withdrawal_releases_a_previously_bound_row():
    """A row bound and persisted `denied`; the candidate withdraws the denial (G-5
    removes it from the live list); the next analysis no longer binds. The ADR-089
    merge must NOT carry the previous `denied` over the fresh `gap` (both rank 0),
    and the re-floor must not resurrect it."""
    from applire.services.gap import merge_ledger_per_requirement

    [previous] = _enforce_denial_stance(
        [_gap_row("Reference designs", **{DENIED_BY_KEY: "Referenzdesigns"})], [DE_DENIAL], None
    )
    assert previous["status"] == "denied"
    fresh = [_gap_row("Reference designs")]
    merged, carried = merge_ledger_per_requirement(
        fresh, [previous], jd_terms=["Reference designs"], touched_members=[]
    )
    assert carried == []
    out = _apply_floors_to_merged(merged, carried, denied_concepts=[], profile_json=VAULT)
    assert out[0]["status"] == "gap"
    assert DENIED_BY_KEY not in out[0]
