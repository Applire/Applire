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

"""A `partial` entry records WHAT makes it partial (ADR-048 amended 2026-07-27).

`partial` conflated two different situations under one bare label:

  * the candidate has an ADJACENT capability — the JD asks for TOGAF, the
    candidate has five years of arc42;
  * the candidate has the RIGHT capability below a stated bar — the JD asks for
    five years, the candidate has two.

The discriminator survived only as free-text `evidence` prose, so no consumer
could act on it. That is why the CV writer is told "TOGAF is claimable, surface
it" when the only truthful instruction is "give arc42 prominence".

One pointer field, not a taxonomy of adjacency kinds.

**The lifecycle invariant (ADR-048 amended 2026-08-13, #526).** `adjacent_evidence`
is present on a row **iff** that row is `claimable` and `status == "partial"`.
The field means "the candidate does NOT have this term, they have that one
instead", which is only a coherent statement about a claimable adjacent partial —
and it is load-bearing: `is_positioning_only` gates the vault-evidence floor, the
coverage demand, the ATS surfacing-miss grade and the page budget off it. A row
that keeps the pointer after leaving that shape is therefore not cosmetic dirt;
it re-routes four instruments at once. Two live paths did exactly that before
this amendment, and both are pinned below.
"""

import pytest

from applire.services.keyword_ledger import build_keyword_ledger


def test_partial_entry_carries_the_adjacent_evidence_pointer():
    out = build_keyword_ledger(
        required_skills=["TOGAF"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "TOGAF",
                "status": "partial",
                "reason": "no TOGAF, but 5 years applying arc42 for architecture documentation",
                "adjacent_evidence": "arc42",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "TOGAF")
    assert entry["status"] == "partial"
    assert entry["adjacent_evidence"] == "arc42"


def test_below_the_bar_partial_has_no_adjacent_pointer():
    """The other kind of `partial`: right capability, not enough of it. There
    is no adjacent thing to promote, and the field must stay absent rather than
    inventing one."""
    out = build_keyword_ledger(
        required_skills=["5+ years Python"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "5+ years Python",
                "status": "partial",
                "reason": "Python present, 2 years — below the stated 5-year bar",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "5+ years Python")
    assert entry["status"] == "partial"
    assert not entry.get("adjacent_evidence")


def test_direct_entry_never_carries_an_adjacent_pointer():
    """A full match has nothing adjacent about it — a stray pointer here would
    mislead the writer into promoting a substitute over the real thing."""
    out = build_keyword_ledger(
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "Kubernetes",
                "status": "direct",
                "reason": "led ECS to EKS migration for 12 services",
                "adjacent_evidence": "Docker",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "Kubernetes")
    assert not entry.get("adjacent_evidence")


# ── the lifecycle invariant (ADR-048 amended 2026-08-13, #526) ───────────────


def _adjacent_partial(concept="TOGAF", adjacent="arc42"):
    """A claimable adjacent `partial` — the ONE shape the pointer may live on."""
    return {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "partial",
        "evidence": f"5 years applying {adjacent}",
        "claimable": True,
        "adjacent_evidence": adjacent,
    }


@pytest.mark.parametrize(
    "mutation, why",
    [
        ({"claimable": False, "status": "gap"}, "an unknown has nothing to substitute FOR"),
        ({"claimable": False, "status": "denied"}, "a denial is the candidate's own position"),
        ({"status": "direct"}, "a full match has nothing adjacent about it"),
    ],
)
def test_is_positioning_only_is_false_off_the_claimable_partial_shape(mutation, why):
    """The reader half of the invariant. `is_positioning_only` used to be a bare
    `bool(entry.get("adjacent_evidence"))` with no status check, so ANY row that
    kept the pointer was treated as "claimable only through a substitute" — which
    exempts it from the ADR-061 vault-evidence floor, from the coverage demand,
    from the outcome critic's presence facts and from the load-bearing veto. The
    function's own docstring always said *claimable*; the code did not."""
    from applire.services.keyword_ledger import is_positioning_only

    entry = {**_adjacent_partial(), **mutation}
    assert entry.get("adjacent_evidence"), "fixture premise: the stale pointer is present"
    assert is_positioning_only(entry) is False, why
    # ...and the shape it IS true for still works.
    assert is_positioning_only(_adjacent_partial()) is True


def test_declining_a_keyword_liability_drops_the_adjacent_pointer():
    """#260 exit (b): the candidate is shown a claimable-but-unstoried hard
    requirement and chooses to DROP it rather than substantiate it.

    `downgrade_ledger_for_concepts` wrote `claimable=False, status="gap",
    evidence=""` and left `adjacent_evidence` standing. The letter's UNADDRESSED
    HARD REQUIREMENTS block reads that field unconditionally and renders
    "ADJACENT CAPABILITY IN THE VAULT: ... give it prominence" — instructing the
    writer to lean on the very capability the candidate has just declined."""
    from applire.services.keyword_ledger import downgrade_ledger_for_concepts

    ledger, changed = downgrade_ledger_for_concepts([_adjacent_partial()], ["TOGAF"])

    assert changed
    entry = ledger[0]
    assert entry["claimable"] is False
    assert entry["status"] == "gap"
    assert "adjacent_evidence" not in entry


def test_a_prefix_duplicate_merge_preserves_the_adjacent_pointer():
    """`_collapse_prefix_duplicates` rebuilt the merged row from a fixed key
    literal, so every field outside that literal was silently dropped — and
    `adjacent_evidence` is outside it. A JD naming both "Digitalisierung" and
    "Digitalisierung der Fertigung" therefore collapsed an adjacent partial into
    a below-the-bar partial: the over-claim protection and the positioning
    obligation vanish together, and nothing logs it.

    The fix is the shape, not the field — the merged row starts from the
    canonical entry and overrides — so this test also stands for whatever field
    is added next."""
    out = build_keyword_ledger(
        required_skills=["Digitalisierung", "Digitalisierung der Fertigung"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "Digitalisierung",
                "status": "partial",
                "reason": "MES rollout and Industrie-4.0 roadmap",
                "adjacent_evidence": "MES-Einführung",
            },
            {
                "concept": "Digitalisierung der Fertigung",
                "status": "partial",
                "reason": "same programme, stated at shop-floor scope",
            },
        ],
    )
    merged = [e for e in out if "Digitalisierung" in e["concept"]]
    assert len(merged) == 1, f"expected a prefix-duplicate collapse, got {merged!r}"
    assert merged[0]["adjacent_evidence"] == "MES-Einführung"


def test_the_denial_floor_writes_drop_the_adjacent_pointer():
    """The three writes that move a row to a denial-derived shape — the declared
    denial, the containment floor, and `assert_claimable_backed`'s heal — all
    leave the claimable-partial shape, so the pointer leaves with them.

    This is not only about `is_positioning_only`, which the reader guard already
    makes inert: `render_unaddressed_hard_requirements_block` reads
    `adjacent_evidence` unconditionally, so a surviving pointer would print
    "ADJACENT CAPABILITY IN THE VAULT: ... give it prominence" underneath the
    candidate's own denial of the term."""
    from applire.services.keyword_ledger import (
        DENIAL_FLOOR_EVIDENCE,
        DENIED_EVIDENCE,
        _denied_row,
        _floored_row,
        assert_claimable_backed,
    )

    src = _adjacent_partial()
    assert src.get("adjacent_evidence"), "fixture premise"

    denied = _denied_row(src, "direct")
    assert denied["evidence"] == DENIED_EVIDENCE
    assert "adjacent_evidence" not in denied

    floored = _floored_row(src)
    assert floored["evidence"] == DENIAL_FLOOR_EVIDENCE
    assert "adjacent_evidence" not in floored

    # The heal's non-polarity branch: an exempt row can still fail clause 2's
    # coherence checks (here: claimable with an empty `evidence`).
    incoherent = {**src, "evidence": ""}
    healed, violations = assert_claimable_backed([incoherent], {"skills": []}, seam="test")
    assert violations and violations[0]["reason"] == "no_evidence"
    assert healed[0]["status"] == "gap"
    assert "adjacent_evidence" not in healed[0]


# ---------------------------------------------------------------------------
# #555 — candidate-attested distinctness outranks the dedup heuristic
# ---------------------------------------------------------------------------


def test_attested_distinct_skills_survive_the_prefix_collapse():
    """#555 (run 2026-08-15): the candidate twice explicitly confirmed
    "SAP PP" and "SAP MM" as skills SEPARATE from "SAP" — the vault carries
    all three as attested entries, and the JD-extraction prompt itself
    mandates the decomposition (job_analysis.py, qualified-requirement
    disposition). `_collapse_prefix_duplicates` nonetheless merged all three
    into one generic "SAP" row once their statuses aligned post-interview.

    The narrowing is a fact, not a judgement (ADR-062 clause 1): two
    concepts that BOTH exist as distinct attested vault skills are distinct
    by the candidate's own testimony, and testimony outranks a dedup
    heuristic (ADR-059 line)."""
    out = build_keyword_ledger(
        required_skills=["SAP", "SAP PP", "SAP MM"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {"concept": "SAP", "status": "direct",
             "evidence": "15 Jahre tägliche Arbeit mit SAP."},
            {"concept": "SAP PP", "status": "direct",
             "evidence": "Key-User für PP im SAP-Rollout; tägliche Arbeit."},
            {"concept": "SAP MM", "status": "direct",
             "evidence": "Tägliche Arbeit mit SAP MM in der Disposition."},
        ],
        profile_json={
            "skills": [
                {"name": "SAP"},
                {"name": "SAP PP"},
                {"name": "SAP MM"},
            ]
        },
    )
    concepts = sorted(e["concept"] for e in out if e["concept"].startswith("SAP"))
    assert concepts == ["SAP", "SAP MM", "SAP PP"], (
        f"attested-distinct skills must never be merged, got {concepts!r}"
    )


def test_unattested_restatement_duplicates_still_merge():
    """The narrowing must not reopen E037 F2: a JD-phrase restatement the
    candidate never attested as its own skill still collapses into the
    short concept term."""
    out = build_keyword_ledger(
        required_skills=["Kubernetes", "Kubernetes (production at scale)"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {"concept": "Kubernetes", "status": "direct",
             "evidence": "Runs three production clusters."},
            {"concept": "Kubernetes (production at scale)", "status": "direct",
             "evidence": "Runs three production clusters."},
        ],
        profile_json={"skills": [{"name": "Kubernetes"}]},
    )
    kube = [e for e in out if "Kubernetes" in e["concept"]]
    assert len(kube) == 1, f"expected the restatement collapse, got {kube!r}"
    assert kube[0]["concept"] == "Kubernetes"


def test_denied_skill_entries_do_not_count_as_attested_distinctness():
    """The attestation fact rides `entry_is_claimable`: a denied or
    unconfirmed vault skill is not testimony of distinctness, so the
    collapse behaves exactly as before the narrowing."""
    out = build_keyword_ledger(
        required_skills=["Terraform", "Terraform Cloud"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {"concept": "Terraform", "status": "direct",
             "evidence": "IaC for every environment."},
            {"concept": "Terraform Cloud", "status": "direct",
             "evidence": "IaC for every environment."},
        ],
        profile_json={
            "skills": [
                {"name": "Terraform"},
                {"name": "Terraform Cloud", "status": "denied"},
            ]
        },
    )
    tf = [e for e in out if "Terraform" in e["concept"]]
    assert len(tf) == 1, f"a denied entry must not block the merge, got {tf!r}"
