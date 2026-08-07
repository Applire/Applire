# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#271 Tasks 2/3 — strongest-vault-evidence digest for the letter.

Ground truth (charter run #5, ``.run5fixture/`` — git-excluded, read at
runtime, never copied verbatim beyond the short quotes the issue brief itself
gives): the letter's CANDIDATE PROFILE block is built from the TAILORED CV
(``cv_data``), condensed to ``work_history[:6]`` x ``bullets[:6]``. The
run-5 NordPharm entry survived tailoring with only 3 bullets, so
``work_experience[0].achievements[3]`` — "Human-authored documents usually
need two to three review rounds, while the right LLMs pass the first
round" — never reached the CV or the letter, even though both run-4 blind
panel reviewers named it as the invite-flipping fact. See
``services.vault_evidence`` module docstring for the full selection design.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from applire.services.vault_evidence import (
    DEFAULT_DIGEST_CAP,
    EvidenceDigestItem,
    jd_signals_leadership,
    render_vault_evidence_block,
    select_vault_evidence,
)

_FIXTURE_DIR = Path(__file__).parents[4] / ".run5fixture"

pytestmark_run5 = pytest.mark.skipif(
    not _FIXTURE_DIR.exists(), reason="run-5 charter fixture not present in this checkout"
)


def _load_run5():
    profile = json.loads((_FIXTURE_DIR / "profile.json").read_text(encoding="utf-8"))
    ledger = json.loads((_FIXTURE_DIR / "ledger.json").read_text(encoding="utf-8"))
    jd_raw = (_FIXTURE_DIR / "jd.txt").read_text(encoding="utf-8")
    return profile, ledger, jd_raw


# ---------------------------------------------------------------------------
# Run-5 ground truth — the pinned regression test
# ---------------------------------------------------------------------------


@pytestmark_run5
def test_run5_digest_surfaces_review_rounds_sentence_never_in_cv_or_letter():
    """The exact sentence #271's ground truth says never reached EITHER
    document — carried verbatim, from its own vault path."""
    from applire.services.jd_excerpt import build_jd_excerpt

    profile, ledger, jd_raw = _load_run5()
    jd_excerpt = build_jd_excerpt(jd_raw)

    items = select_vault_evidence(ledger, jd_excerpt, profile)

    review_rounds = [i for i in items if i.path == "work_experience[0].achievements[3]"]
    assert review_rounds, "achievements[3] (the review-rounds sentence) must be selected"
    assert review_rounds[0].text == (
        "Human-authored documents usually need two to three review rounds, "
        "while the right LLMs pass the first round"
    )
    # Verbatim means verbatim — not a paraphrase of the vault text.
    assert review_rounds[0].text in json.dumps(profile)


@pytestmark_run5
def test_run5_digest_surfaces_leadership_arc_when_jd_states_leadership_weighting():
    """The JD's own 60/40 leadership-weighting line makes the vault's
    leadership material eligible (rule 3) — the mentoring/transformation
    arc the run-5 letter never used."""
    from applire.services.jd_excerpt import build_jd_excerpt

    profile, ledger, jd_raw = _load_run5()
    jd_excerpt = build_jd_excerpt(jd_raw)
    assert jd_signals_leadership(jd_excerpt)

    items = select_vault_evidence(ledger, jd_excerpt, profile)
    paths = {i.path for i in items}
    assert "work_experience[0].responsibilities[14]" in paths  # "Leads a distributed team..."


@pytestmark_run5
def test_run5_digest_never_exceeds_default_cap():
    from applire.services.jd_excerpt import build_jd_excerpt

    profile, ledger, jd_raw = _load_run5()
    items = select_vault_evidence(ledger, build_jd_excerpt(jd_raw), profile)
    assert len(items) <= DEFAULT_DIGEST_CAP


# ---------------------------------------------------------------------------
# Unit-level behaviour (small synthetic fixtures — no run-5 dependency)
# ---------------------------------------------------------------------------


def _profile(work_experience=None, **extra):
    return {"work_experience": work_experience or [], **extra}


def test_select_vault_evidence_none_and_empty_tolerant():
    assert select_vault_evidence(None, "", None) == []
    assert select_vault_evidence([], "", {}) == []


def test_claimable_concept_anchor_selected_verbatim_with_path():
    ledger = [
        {
            "concept": "Kubernetes",
            "claimable": True,
            "surface_forms": ["Kubernetes", "K8s"],
            "sources": ["required"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Platform Engineer",
                "company": "Acme",
                "achievements": ["Migrated the fleet to Kubernetes, cutting deploy time by half."],
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    assert len(items) == 1
    assert items[0].path == "work_experience[0].achievements[0]"
    assert items[0].text == "Migrated the fleet to Kubernetes, cutting deploy time by half."
    assert items[0].concept == "Kubernetes"
    assert items[0].reason == "claimable-concept"


def test_non_claimable_ledger_entry_never_anchors():
    """A ``claimable: false`` (honest-gap) entry must never contribute an
    anchor — the digest only ever surfaces material the candidate can
    truthfully stand behind."""
    ledger = [
        {"concept": "Kubernetes", "claimable": False, "surface_forms": ["Kubernetes"]},
    ]
    profile = _profile(
        work_experience=[
            {"id": "w1", "role": "Eng", "company": "Acme", "achievements": ["Worked with Kubernetes."]}
        ]
    )
    assert select_vault_evidence(ledger, "", profile) == []


def test_measured_outcome_preferred_over_target_anchor():
    """Rule 2 (#261 extended): when the anchor itself reads as a
    target/aspirational phrase, the digest swaps it for its safely-paired
    measured outcome instead of surfacing the bare target."""
    ledger = [
        {
            "concept": "cost reduction",
            "claimable": True,
            "surface_forms": ["cost reduction", "cost savings"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Ops Lead",
                "company": "Acme",
                "achievements": [
                    "Targeting a 30% cost reduction in cloud spend across the whole "
                    "platform organisation this fiscal year, agreed with finance.",
                    "Achieved a 32% cost reduction in cloud spend, confirmed Q3.",
                ],
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    paths = {i.path: i for i in items}
    # The bare target (achievements[0]) must never be the surfaced anchor...
    assert "work_experience[0].achievements[0]" not in paths
    # ...its measured pair (achievements[1]) must be, instead.
    anchor_items = [i for i in items if i.reason == "measured-outcome-preferred"]
    assert len(anchor_items) == 1
    assert anchor_items[0].path == "work_experience[0].achievements[1]"


def test_hermetic_same_initiative_evidence_reaches_digest_without_its_own_concept_match():
    """Hermetic twin of ``test_run5_digest_surfaces_review_rounds_sentence_
    never_in_cv_or_letter`` above — same channel (the SAME-INITIATIVE
    EXTENSION to rule 2), synthetic vault, no ``.run5fixture/`` dependency.

    A concept anchor only proves ONE sentence of an initiative is
    JD-relevant; another figure-bearing achievement on the SAME work entry
    reaches the digest too, even though its own text never mentions the
    ledger concept's surface form — this is exactly how run-5's
    achievements[3] (the review-rounds sentence) reached the digest for
    real."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": True,
            "surface_forms": ["RAG pipelines", "RAG"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "ML Engineer",
                "company": "Acme",
                "achievements": [
                    # The anchor: literally contains the ledger surface form.
                    "Built RAG pipelines for the internal search platform.",
                    # Same owner, same initiative, figure-bearing, NOT a
                    # target phrase, and never says "RAG" itself — must
                    # still surface via the same-initiative extension.
                    "Reduced query latency by 42% across the retrieval stack.",
                ],
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    paths = {i.path: i for i in items}
    assert "work_experience[0].achievements[0]" in paths
    same_initiative = paths.get("work_experience[0].achievements[1]")
    assert same_initiative is not None, (
        "the figure-bearing same-initiative achievement must reach the digest "
        "even though it never says 'RAG' itself"
    )
    assert same_initiative.reason == "same-initiative-evidence"
    assert same_initiative.text == "Reduced query latency by 42% across the retrieval stack."


def test_hermetic_same_initiative_evidence_never_pulled_from_a_sibling_project():
    """The subset scoping (module docstring, 'SAME-INITIATIVE EXTENSION')
    must exclude a sibling project merely sharing the same parent employer —
    only work/project entries whose OWN owner set is a subset of the
    anchor's owner set qualify."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": True,
            "surface_forms": ["RAG pipelines", "RAG"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "ML Engineer",
                "company": "Acme",
                "achievements": ["Built RAG pipelines for the internal search platform."],
            }
        ],
        projects=[
            {
                "id": "p1",
                "name": "Unrelated internal tool",
                "associated_experience": "w1",
                "description": "A completely unrelated internal tool used by finance.",
                "achievements": ["Cut manual reconciliation time by 60% for the finance team."],
            }
        ],
    )
    items = select_vault_evidence(ledger, "", profile)
    # The finance-tool achievement shares the employer via associated_experience
    # (owner_ids = {"p1", "w1"}), which is NOT a subset of the anchor's
    # {"w1"} — it must never surface via the same-initiative extension.
    assert not any(
        i.text == "Cut manual reconciliation time by 60% for the finance team." for i in items
    )


def test_leadership_evidence_absent_when_jd_does_not_signal_leadership():
    ledger = [
        {"concept": "Kubernetes", "claimable": True, "surface_forms": ["Kubernetes"]},
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Eng",
                "company": "Acme",
                "achievements": ["Migrated the fleet to Kubernetes."],
                "responsibilities": ["Led a team of five engineers through a platform migration."],
            }
        ]
    )
    jd_no_leadership = "We are hiring a hands-on individual contributor to run our Kubernetes fleet."
    assert not jd_signals_leadership(jd_no_leadership)
    items = select_vault_evidence(ledger, jd_no_leadership, profile)
    # Channel 2 (same-initiative figures) is independent of the leadership
    # gate and may still surface this text on its own merits — what must
    # NEVER happen is the LEADERSHIP channel itself firing.
    assert not any(i.reason == "leadership-eligible" for i in items)


def test_leadership_evidence_eligible_when_jd_states_leadership_weighting():
    ledger = [
        {"concept": "Kubernetes", "claimable": True, "surface_forms": ["Kubernetes"]},
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Eng",
                "company": "Acme",
                "achievements": ["Migrated the fleet to Kubernetes."],
                "responsibilities": ["Led a team of five engineers through a platform migration."],
            }
        ]
    )
    jd_with_leadership = "This role carries significant leadership responsibility, managing a growing team."
    assert jd_signals_leadership(jd_with_leadership)
    items = select_vault_evidence(ledger, jd_with_leadership, profile)
    assert any(i.path == "work_experience[0].responsibilities[0]" for i in items)


def test_cap_is_enforced_and_drop_is_logged_not_silent(caplog):
    ledger = [
        {
            "concept": f"skill-{i}",
            "claimable": True,
            "surface_forms": [f"skill-{i}"],
        }
        for i in range(15)
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Eng",
                "company": "Acme",
                "achievements": [f"Used skill-{i} to ship a project." for i in range(15)],
            }
        ]
    )
    with caplog.at_level(logging.INFO, logger="applire.services.vault_evidence"):
        items = select_vault_evidence(ledger, "", profile, cap=5)
    assert len(items) == 5
    assert any("capped digest" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# #271 run-6 follow-up — the measured-outcome QUALIFIER for a non-target
# anchor is crowded out by the global cap before it ever reaches the digest.
#
# Ground truth (run-6, dev DB, 2026-07-26 — verified against the real vault/
# ledger/JD, never copied verbatim into this file): a claimable concept's
# anchor lives under ``responsibilities[]`` and is NOT itself a target phrase
# (rule 2's swap never fires), so it is left in place — correctly. But the
# SAME work entry also carries a bare headline achievement (a figure with no
# justification) AND a separate achievement that is the figure's measured
# justification/qualifier (``find_paired_outcome(anchor.text, ...)`` resolves
# to it directly — #261's own pairing function, reused unchanged). The
# same-initiative extension (channel 2) DOES compute this qualifier as a
# candidate, but only in path-sort order alongside the bare headline and
# behind every other claimable concept's own anchor already in the flat
# list — so when enough concepts/candidates compete for a bounded ``cap``,
# the qualifier (not the headline it explains) is the one silently dropped.
# Real-world verdict: BOTH blind run-4 panel reviewers named exactly this
# fact as what would change their minds — losing it to cap arithmetic is the
# #271 defect, even though ``select_vault_evidence`` in principle already
# "knows" the two achievements are linked.
# ---------------------------------------------------------------------------


def test_measured_outcome_qualifier_survives_cap_crowding_ahead_of_its_own_headline():
    """Minimal reproduction of the run-6 shape: a non-target anchor
    (``responsibilities[0]``) whose work entry ALSO carries a bare headline
    figure (``achievements[0]``) and that headline's measured justification
    (``achievements[1]``). Under a tight cap, the qualifier — not the bare
    headline — must be the one that survives, because it is the fact that
    makes the headline credible, never the reverse."""
    ledger = [
        {
            "concept": "filler capability",
            "claimable": True,
            "surface_forms": ["filler capability"],
        },
        {
            "concept": "workflow automation",
            "claimable": True,
            "surface_forms": ["workflow-automation", "workflow automation"],
        },
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w-filler",
                "role": "Support Eng",
                "company": "Northwind Labs",
                "achievements": ["Delivered filler capability improvements for the support desk."],
            },
            {
                "id": "w1",
                "role": "Automation Eng",
                "company": "Northwind Labs",
                "responsibilities": [
                    "Pioneered a self-service workflow-automation approach for onboarding, "
                    "replacing manual scripts across the team."
                ],
                "achievements": [
                    "Automated the onboarding pipeline, cutting manual setup time by an "
                    "estimated 80%.",
                    "Manual sign-off usually needs two to three review passes per onboarding "
                    "change, while the self-service workflow passes automated checks on the "
                    "first attempt, confirming the estimate is conservative.",
                ],
            },
        ]
    )
    items = select_vault_evidence(ledger, "", profile, cap=3)
    paths = {i.path for i in items}
    assert len(items) == 3
    qualifier_path = "work_experience[1].achievements[1]"
    headline_path = "work_experience[1].achievements[0]"
    assert qualifier_path in paths, (
        "the measured-outcome qualifier must survive the cap ahead of the bare "
        f"headline it explains — got {sorted(paths)}"
    )
    assert headline_path not in paths, (
        "the bare headline should be the one dropped under budget pressure, not "
        "the fact that makes it credible"
    )
    qualifier_item = next(i for i in items if i.path == qualifier_path)
    assert qualifier_item.reason == "measured-outcome-qualifier"
    assert qualifier_item.concept == "workflow automation"
    # Verbatim, never paraphrased.
    assert qualifier_item.text == (
        "Manual sign-off usually needs two to three review passes per onboarding "
        "change, while the self-service workflow passes automated checks on the "
        "first attempt, confirming the estimate is conservative."
    )


def test_measured_outcome_qualifier_never_added_when_no_pairing_exists():
    """No-regression / no-minting guard: when the anchor's work entry has no
    genuine measured-outcome pairing available at all, nothing is invented —
    the digest is exactly what channel 1 + the (empty) same-initiative
    extension would have produced without this fix."""
    ledger = [
        {
            "concept": "workflow automation",
            "claimable": True,
            "surface_forms": ["workflow-automation", "workflow automation"],
        },
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Automation Eng",
                "company": "Northwind Labs",
                "responsibilities": [
                    "Pioneered a self-service workflow-automation approach for onboarding."
                ],
                # No achievements at all — no possible pairing.
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    assert len(items) == 1
    assert items[0].path == "work_experience[0].responsibilities[0]"
    assert items[0].reason == "claimable-concept"


def test_measured_outcome_qualifier_never_duplicates_an_already_swapped_target_anchor():
    """When rule 2's own swap already fired (the anchor itself read as a bare
    target and was replaced by its paired outcome), the qualifier lookup must
    not run a second time against the (already-swapped) anchor text — no
    double-dip, no duplicate item, exactly one item for that concept."""
    ledger = [
        {
            "concept": "cost reduction",
            "claimable": True,
            "surface_forms": ["cost reduction", "cost savings"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "Ops Lead",
                "company": "Northwind Labs",
                "achievements": [
                    "Targeting a 30% cost reduction in cloud spend across the whole "
                    "platform organisation this fiscal year, agreed with finance.",
                    "Achieved a 32% cost reduction in cloud spend, confirmed Q3.",
                ],
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    matching = [i for i in items if i.concept == "cost reduction"]
    assert len(matching) == 1
    assert matching[0].reason == "measured-outcome-preferred"
    assert matching[0].path == "work_experience[0].achievements[1]"


def test_measured_outcome_qualifier_skips_self_match_when_anchor_is_itself_an_achievement():
    """Regression guard for the sibling hermetic same-initiative test above:
    when the anchor itself lives at an ``achievements[]`` path (so
    ``find_paired_outcome`` would trivially "pair" it with itself at 100%
    coverage), the qualifier lookup must recognise the self-match and skip
    it — never mint a self-referential qualifier — leaving channel 2's own
    same-initiative scan to surface the real sibling achievement exactly as
    before (unchanged behaviour, not a duplicate)."""
    ledger = [
        {
            "concept": "RAG pipelines",
            "claimable": True,
            "surface_forms": ["RAG pipelines", "RAG"],
        }
    ]
    profile = _profile(
        work_experience=[
            {
                "id": "w1",
                "role": "ML Engineer",
                "company": "Northwind Labs",
                "achievements": [
                    "Built RAG pipelines for the internal search platform.",
                    "Reduced query latency by 42% across the retrieval stack.",
                ],
            }
        ]
    )
    items = select_vault_evidence(ledger, "", profile)
    matching = [i for i in items if i.path == "work_experience[0].achievements[1]"]
    assert len(matching) == 1, "must appear exactly once, never duplicated"
    assert matching[0].reason == "same-initiative-evidence"


def test_render_vault_evidence_block_empty_for_no_items():
    assert render_vault_evidence_block([]) == ""


def test_render_vault_evidence_block_wording_and_verbatim_content():
    items = [
        EvidenceDigestItem(
            concept="Kubernetes",
            reason="claimable-concept",
            path="work_experience[0].achievements[0]",
            text="Migrated the fleet to Kubernetes, cutting deploy time by half.",
        )
    ]
    block = render_vault_evidence_block(items)
    assert "STRONGEST VAULT EVIDENCE" in block
    assert "Migrated the fleet to Kubernetes, cutting deploy time by half." in block
    assert "work_experience[0].achievements[0]" in block
    low = block.lower()
    assert "additional" in low and "not content that must all appear" in low
    assert "never overrides" in low or "grounding contract" in low


# ── #303 — owner scoping for the segmented CV path ────────────────────────


def _owned(path: str, owners: set[str]) -> EvidenceDigestItem:
    return EvidenceDigestItem(
        concept="Budgetverantwortung",
        reason="claimable-concept",
        path=path,
        text=f"evidence at {path}",
        owner_ids=frozenset(owners),
    )


def test_select_vault_evidence_carries_the_owner_ids_of_each_unit():
    """The digest is useless to a per-entry writer without the owner. Pins that
    selection populates it rather than leaving the default empty set."""
    ledger = [
        {"concept": "Kubernetes", "surface_forms": ["Kubernetes"], "claimable": True,
         "status": "direct"},
    ]
    profile = {
        "work_experience": [
            {"id": "w-alpha", "company": "Acme", "role": "SRE",
             "start_date": "2020-01", "end_date": None,
             "responsibilities": ["Ran the Kubernetes fleet across three regions."],
             "achievements": []},
        ],
    }
    items = select_vault_evidence(ledger, "", profile)
    assert items
    assert all(i.owner_ids for i in items), "every selected item must name its owner"
    assert any("w-alpha" in i.owner_ids for i in items)


def test_filter_vault_evidence_for_owner_keeps_only_that_entrys_evidence():
    """ADR-071: a per-entry writer offered another employer's achievement is
    being invited to misattribute it."""
    from applire.services.vault_evidence import filter_vault_evidence_for_owner

    mine = _owned("work_experience[0].achievements[0]", {"w1"})
    theirs = _owned("work_experience[1].achievements[0]", {"w2"})
    nested = _owned("projects[0].description", {"w1", "p9"})
    out = filter_vault_evidence_for_owner([mine, theirs, nested], "w1")
    assert out == [mine, nested]


def test_filter_vault_evidence_for_owner_fails_closed_on_missing_owner():
    """An unknown or absent owner id must yield nothing — never everything."""
    from applire.services.vault_evidence import filter_vault_evidence_for_owner

    items = [_owned("work_experience[0].achievements[0]", {"w1"})]
    assert filter_vault_evidence_for_owner(items, None) == []
    assert filter_vault_evidence_for_owner(items, "") == []
    assert filter_vault_evidence_for_owner(items, "w-unknown") == []
    assert filter_vault_evidence_for_owner([], "w1") == []


def test_filter_vault_evidence_for_owner_drops_ownerless_units():
    """Summary/certification-level units belong to no work entry."""
    from applire.services.vault_evidence import filter_vault_evidence_for_owner

    ownerless = EvidenceDigestItem(
        concept="x", reason="claimable-concept",
        path="professional_summary.en", text="A summary line.",
    )
    assert filter_vault_evidence_for_owner([ownerless], "w1") == []


def test_render_vault_evidence_block_rejects_an_unknown_chain():
    items = [_owned("work_experience[0].achievements[0]", {"w1"})]
    import pytest as _pytest

    with _pytest.raises(ValueError):
        render_vault_evidence_block(items, chain="resume")


def test_select_vault_evidence_does_not_mutate_the_profile_it_is_given():
    """``select_vault_evidence`` documents itself as pure, and its caller on the
    CV chain hands it the very ``profile_json`` that is then serialised as the
    ADR-021 reviewer's source of truth (#303).

    It is not pure by default: ``build_vault_index`` coerces via
    ``MasterProfileData.model_validate``, whose ``mode="before"``
    ``_migrate_legacy_fields`` validator rewrites its input dict IN PLACE
    (``data.pop("work_history")``, ``data["skills"] = [...]``). A legacy-shaped
    profile handed to this function would therefore come back normalised, and
    the reviewer would be grounding against a document the vault never stored.
    """
    import json as _json

    legacy_profile = {
        "work_history": [
            {"company": "Acme", "role": "Dev", "start_date": "2020",
             "end_date": None, "bullets": ["Ran the Kubernetes fleet."]},
        ],
        "skills": ["Python"],
        "contact": {"name": "Max", "linkedin": "https://example.invalid/max"},
    }
    before = _json.dumps(legacy_profile, ensure_ascii=False, sort_keys=True)
    select_vault_evidence(
        [{"concept": "Kubernetes", "surface_forms": ["Kubernetes"],
          "claimable": True, "status": "direct"}],
        "",
        legacy_profile,
    )
    assert _json.dumps(legacy_profile, ensure_ascii=False, sort_keys=True) == before
