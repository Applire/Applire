# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Seam tests for ADR-089 clause 3: three readers switched from
``cluster.get("gaps")`` to ``gap_coverage.all_members`` (gaps + outcome.covered
+ outcome.declined) so a chip/prompt/hint can no longer treat an
``outcome.covered`` sibling as if it were not a member of the cluster at all.

One test per call site, each driving the REAL public function and asserting
on the artefact it produces. Each test also runs the SAME chip/cluster/hints
against a counterfactual cluster whose ``outcome.covered`` member has been
removed (plain data, no source mutation) and shows the artefact changes —
proof that the assertion above depends on ``all_members`` reading
``outcome.covered``, not on something else in the fixture.

Call sites (ADR-089 clause 3, main-session brief 2026-09-23):
1. ``choice_grounding._cluster_terms`` (via the public ``filter_ungrounded_choices``)
2. ``choice_grounding.constituent_evidence`` (via the public prompt builder
   ``prompts.interview.build_question_prompt``)
3. ``cv_gap_hints._merge_cluster_duplicates`` (via the public ``build_gap_hints``)
"""

from applire.prompts.interview import build_question_prompt
from applire.services.choice_grounding import filter_ungrounded_choices
from applire.services.cv_gap_hints import build_gap_hints

# ---------------------------------------------------------------------------
# 1. choice_grounding._cluster_terms via filter_ungrounded_choices
# ---------------------------------------------------------------------------


def test_seam_choice_grounding_cluster_terms_reads_covered_members():
    # gaps=["Kubernetes"], outcome.covered=["Terraform"] — Terraform is a
    # cluster member only via all_members(), never via "gaps" alone.
    cluster = {
        "id": "cl-infra",
        "label": "Infrastructure Automation",
        "category": "C",
        "gaps": ["Kubernetes"],
        "jd_skills": [],
        "jd_context": "",
        "outcome": {"asked": 1, "covered": ["Terraform"], "declined": [], "session_ids": ["s1"]},
    }
    # Profile evidences Kubernetes but not Terraform, and names no employer —
    # this exercises the whole-profile cluster-term branch (#236's
    # employer-scoped guard never engages: work_experience is empty).
    profile = {"skills": [{"name": "Kubernetes"}, {"name": "Python"}], "work_experience": []}

    unevidenced_chip = "I have written Terraform modules for production."
    # Control chip: mentions no cluster/JD term at all — proves the guard
    # isn't dropping everything (vacuous pass) and genuinely keeps a
    # legitimate chip alongside the dropped one.
    control_chip = "I coordinate cross-team roadmap planning and stakeholder communication."
    choices = [
        {"text": unevidenced_chip, "level": "direct"},
        {"text": control_chip, "level": "direct"},
    ]

    result = filter_ungrounded_choices(choices, cluster, profile, "C")
    assert result is not None
    assert control_chip in result
    assert unevidenced_chip not in result

    # Counterfactual: same chip, but a cluster whose outcome.covered never
    # names Terraform (no "outcome" key at all — Terraform appears nowhere
    # in gaps/jd_skills/label either) — the chip must now be KEPT, because
    # "Terraform" is no longer a cluster term to check evidence for. This
    # isolates the drop above as coming from all_members reading
    # outcome.covered, not from profile evidence or chip wording.
    cluster_without_covered_member = {
        "id": "cl-infra-2",
        "label": "Infrastructure Automation",
        "category": "C",
        "gaps": ["Kubernetes"],
        "jd_skills": [],
        "jd_context": "",
    }
    counterfactual_result = filter_ungrounded_choices(
        [{"text": unevidenced_chip, "level": "direct"}],
        cluster_without_covered_member,
        profile,
        "C",
    )
    assert counterfactual_result == [unevidenced_chip]


# ---------------------------------------------------------------------------
# 2. choice_grounding.constituent_evidence via prompts.interview.build_question_prompt
# ---------------------------------------------------------------------------


def test_seam_interview_prompt_hint_reads_covered_members():
    # Category C cluster: gaps=["Helm"], outcome.covered=["Terraform"].
    cluster = {
        "id": "cl-cloud-infra",
        "label": "Cloud Infrastructure",
        "category": "C",
        "gaps": ["Helm"],
        "jd_skills": [],
        "jd_context": "",
        "outcome": {"asked": 1, "covered": ["Terraform"], "declined": [], "session_ids": ["s1"]},
    }
    # Profile evidences Terraform, not Helm.
    profile = {"skills": [{"name": "Terraform"}], "work_experience": []}

    prompt = build_question_prompt(cluster, profile, [], gap_category="C")
    assert "now evidences: Terraform" in prompt
    assert "Still unevidenced: Helm" in prompt

    # Counterfactual: outcome.covered emptied — Terraform is no longer a
    # cluster member, so the CURRENT-profile signal has nothing evidenced
    # and the prompt falls back to the "no signal" hint instead of "now
    # evidences" — isolating the hint above to all_members reading
    # outcome.covered.
    cluster_without_covered_member = {
        **cluster,
        "outcome": {"asked": 1, "covered": [], "declined": [], "session_ids": ["s1"]},
    }
    counterfactual_prompt = build_question_prompt(
        cluster_without_covered_member, profile, [], gap_category="C"
    )
    assert "no signal for 'Cloud Infrastructure' was found" in counterfactual_prompt
    assert "now evidences" not in counterfactual_prompt


# ---------------------------------------------------------------------------
# 3. cv_gap_hints._merge_cluster_duplicates via build_gap_hints
# ---------------------------------------------------------------------------


def _ledger_entry(concept, status="gap", surface_forms=None, fit_weight=1.0):
    """Mirrors tests/unit/test_cv_gap_hints.py's `_entry` fixture shape."""
    return {
        "concept": concept,
        "surface_forms": surface_forms if surface_forms is not None else [concept],
        "sources": ["required"],
        "fit_weight": fit_weight,
        "status": status,
        "evidence": "" if status == "gap" else f"evidence for {concept}",
        "claimable": status in ("direct", "partial"),
    }


def _flatten_hints(gap_map, general):
    return [h for hints in gap_map.values() for h in hints] + list(general)


def test_seam_cv_gap_hints_merges_a_covered_sibling():
    # gaps=["AWS Certified"], outcome.covered=["Azure Certified"] — same
    # cluster. Both ledger candidates are genuinely open: neither concept
    # appears in section_contents, so document coverage doesn't suppress
    # either one before the merge step runs.
    cluster = {
        "id": "cluster-cloud-cert",
        "label": "Cloud Certifications",
        "category": "C",
        "gaps": ["AWS Certified"],
        "jd_skills": [],
        "jd_context": "",
        "outcome": {"asked": 1, "covered": ["Azure Certified"], "declined": [], "session_ids": ["s1"]},
    }
    ledger = [_ledger_entry("AWS Certified"), _ledger_entry("Azure Certified")]
    section_contents = {"skills": "Python\nDocker"}

    gap_map, general = build_gap_hints(ledger, [], [], section_contents, gap_clusters=[cluster])
    hints = _flatten_hints(gap_map, general)
    assert [h.label for h in hints] == ["Cloud Certifications"]
    assert hints[0].kind == "honest"

    # Counterfactual: outcome.covered emptied — Azure Certified is no longer
    # a member of the cluster, so the two open candidates must stay separate
    # instead of merging.
    cluster_without_covered_member = {
        **cluster,
        "outcome": {"asked": 1, "covered": [], "declined": [], "session_ids": ["s1"]},
    }
    gap_map2, general2 = build_gap_hints(
        ledger, [], [], section_contents, gap_clusters=[cluster_without_covered_member]
    )
    hints2 = _flatten_hints(gap_map2, general2)
    assert sorted(h.label for h in hints2) == ["AWS Certified", "Azure Certified"]
