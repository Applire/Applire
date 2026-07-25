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

"""US259 — deterministic sufficiency + ordering helpers
(applire.services.interview.sufficiency).

Run-4 ground truth: the interview stopped at the count-based hard ceiling one
question before eliciting a JD-required capability's quantification. These
tests pin the two deterministic (no-LLM) pieces of machinery that fix the
shape:
  - concept_is_required / cluster_needs_priority: question ORDERING signal
  - is_interview_sufficient: the named termination predicate
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _cluster(gaps, jd_context=""):
    return {
        "id": "cluster-1",
        "label": "cluster",
        "category": "C",
        "gaps": gaps,
        "jd_skills": [],
        "jd_context": jd_context,
    }


def _ledger(**overrides):
    entry = {
        "concept": "CI/CD",
        "surface_forms": ["CI/CD"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    }
    entry.update(overrides)
    return [entry]


# ---------------------------------------------------------------------------
# concept_is_required
# ---------------------------------------------------------------------------


def test_concept_is_required_true_for_required_source():
    from applire.services.interview.sufficiency import concept_is_required

    ledger = _ledger(concept="CI/CD", sources=["required"])
    assert concept_is_required("CI/CD", ledger) is True


def test_concept_is_required_false_for_nice_to_have_only():
    from applire.services.interview.sufficiency import concept_is_required

    ledger = _ledger(concept="CI/CD", sources=["nice_to_have"])
    assert concept_is_required("CI/CD", ledger) is False


def test_concept_is_required_false_when_no_ledger_entry():
    from applire.services.interview.sufficiency import concept_is_required

    assert concept_is_required("Quantum Computing", _ledger()) is False


def test_concept_is_required_false_when_ledger_is_none():
    from applire.services.interview.sufficiency import concept_is_required

    assert concept_is_required("CI/CD", None) is False


def test_concept_is_required_matches_via_substring():
    """Cluster gap labels and ledger concepts are independently generated
    text (clustering LLM vs classification LLM) — a byte-identical key can't
    be assumed, so the match mirrors keyword_ledger.py's own containment
    rule."""
    from applire.services.interview.sufficiency import concept_is_required

    ledger = _ledger(concept="CI/CD pipelines", sources=["required"])
    assert concept_is_required("CI/CD", ledger) is True


# ---------------------------------------------------------------------------
# cluster_needs_priority
# ---------------------------------------------------------------------------


def test_cluster_needs_priority_true_when_keyword_only_required():
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster(["CI/CD"])
    ledger = _ledger(concept="CI/CD", sources=["required"], status="gap")
    assert cluster_needs_priority(cluster, {}, ledger) is True


def test_cluster_needs_priority_true_when_unquantified_required():
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster(["CI/CD"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": ["Introduced CI/CD practices across the team."],
                "achievements": [],
            }
        ]
    }
    # evidenced (status "partial") but no figure anywhere in the evidence text
    ledger = _ledger(concept="CI/CD", sources=["required"], status="partial")
    assert cluster_needs_priority(cluster, profile, ledger) is True


def test_cluster_needs_priority_false_when_quantified():
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster(["CI/CD"])
    profile = {
        "work_experience": [
            {
                "company": "Acme",
                "role": "Engineer",
                "technologies": ["CI/CD"],
                "responsibilities": ["Cut deploy time by 40% running CI/CD for a 12-person team."],
                "achievements": [],
            }
        ]
    }
    ledger = _ledger(concept="CI/CD", sources=["required"], status="direct")
    assert cluster_needs_priority(cluster, profile, ledger) is False


def test_cluster_needs_priority_false_for_nice_to_have_only():
    """A keyword-only/unquantified concept that is NOT a JD hard requirement
    never promotes its cluster — forcing every nice-to-have gap to the front
    would defeat the point of prioritising."""
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster(["Rust"])
    ledger = _ledger(concept="Rust", sources=["nice_to_have"], status="gap")
    assert cluster_needs_priority(cluster, {}, ledger) is False


def test_cluster_needs_priority_false_when_no_gaps():
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster([])
    assert cluster_needs_priority(cluster, {}, _ledger()) is False


def test_cluster_needs_priority_false_when_ledger_missing():
    """No ledger (e.g. MODE B / guided sessions have none) → never promote;
    never fabricate a priority signal from absent data."""
    from applire.services.interview.sufficiency import cluster_needs_priority

    cluster = _cluster(["CI/CD"])
    assert cluster_needs_priority(cluster, {}, None) is False


# ---------------------------------------------------------------------------
# is_interview_sufficient
# ---------------------------------------------------------------------------


def test_is_interview_sufficient_true_when_all_resolved():
    from applire.services.interview.sufficiency import is_interview_sufficient

    assert is_interview_sufficient(["a", "b"], 2, set()) is True


def test_is_interview_sufficient_false_when_gap_remains():
    from applire.services.interview.sufficiency import is_interview_sufficient

    assert is_interview_sufficient(["a", "b"], 1, set()) is False


def test_is_interview_sufficient_true_with_skipped_gaps():
    """A skipped gap (transitively resolved, or a denial-terminated one) counts
    as resolved-for-sufficiency — never blocks completion."""
    from applire.services.interview.sufficiency import is_interview_sufficient

    assert is_interview_sufficient(["a", "b"], 0, {"a", "b"}) is True


def test_is_interview_sufficient_true_on_empty_gaps():
    from applire.services.interview.sufficiency import is_interview_sufficient

    assert is_interview_sufficient([], 0, set()) is True
