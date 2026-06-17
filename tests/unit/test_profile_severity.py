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

"""
US162 (E033 / ADR-041 amended) — profile-mismatch severity classifier.

Tags each POST-MERGE (Tier-2) profile issue with
``profile_mismatch_severity = info | review | critical`` so the Health hub
(US160/US164) and the JD interview (US163) can route it. Deterministic, no LLM.

Architecture boundary (ADR-041 amended / epic Task 3):
- This is the Tier-2 axis ONLY. Destructive Tier-1 cases (not-a-CV, name
  divergence) are gated pre-merge in US167 — the classifier only *reports* an
  already-deferred gate as ``critical``; it never re-runs the gate.
- ``profile_mismatch_severity`` is distinct from the ADR-021 reviewer severity.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.schemas.profile import Conflict  # noqa: E402
from applire.services.profile import severity as sev  # noqa: E402


# ─── Trigger class: deferred Tier-1 gate ──────────────────────────────────────

def test_deferred_gate_is_always_critical():
    """A parked/unresolved Tier-1 gate (US167) is the canonical critical."""
    assert sev.GATE_SEVERITY == "critical"


# ─── Trigger class: merge data-loss reconciliation delta ──────────────────────

def _recon(**deltas: int) -> dict[str, dict[str, int]]:
    """Build a reconciliation block with the given per-entity deltas."""
    return {
        entity: {"extracted": d, "stored": 0, "delta": d}
        for entity, d in deltas.items()
    }


def test_zero_data_loss_delta_is_not_an_issue():
    """A clean merge (no lost data points) yields no severity at all."""
    assert sev.classify_reconciliation(_recon(work_experience=0, skills=0)) is None


def test_small_data_loss_delta_is_review():
    """A delta at/below the critical threshold (default 3) stays a review nudge."""
    assert sev.classify_reconciliation(_recon(skills=3)) == "review"


def test_large_data_loss_delta_is_critical():
    """A delta above the critical threshold (default 3) escalates to critical."""
    assert sev.classify_reconciliation(_recon(work_experience=4)) == "critical"


def test_data_loss_delta_sums_across_entities():
    """The delta is the total lost across all entities, not per-entity."""
    # 2 + 2 = 4 > 3 → critical, even though no single entity exceeds the threshold.
    assert sev.classify_reconciliation(_recon(skills=2, certifications=2)) == "critical"


def test_data_loss_critical_threshold_is_env_overridable(monkeypatch):
    """Threshold reads constants at call time (ADR-035 tunable precedent)."""
    monkeypatch.setattr(sev.constants, "MERGE_DATALOSS_CRITICAL_THRESHOLD", 1)
    # delta of 2 now exceeds the lowered threshold of 1 → critical
    assert sev.classify_reconciliation(_recon(skills=2)) == "critical"


# ─── Trigger class: post-merge conflicts (date/title contradictions) ──────────

def _conflict(section: str, field: str) -> Conflict:
    return Conflict(
        section=section,
        field=field,
        existing_value="a",
        incoming_value="b",
        source="cv_upload",
    )


def test_date_contradiction_conflict_is_review():
    """Contradictory work dates (reclassified from critical) → review (ADR-041 amended)."""
    assert sev.classify_conflict(_conflict("work_experience", "start_date")) == "review"
    assert sev.classify_conflict(_conflict("work_experience", "end_date")) == "review"


def test_title_contradiction_conflict_is_review():
    """A contradictory job title is a Tier-2 review, not critical."""
    assert sev.classify_conflict(_conflict("work_experience", "title")) == "review"


def test_minor_field_conflict_is_info():
    """Cosmetic differences (e.g. phone) are minor → info."""
    assert sev.classify_conflict(_conflict("personal_info", "phone")) == "info"


# ─── Trigger class: low merge confidence ──────────────────────────────────────

def test_low_merge_confidence_is_review():
    """Merge confidence below the threshold (default 0.75) → review."""
    assert sev.classify_confidence(0.5) == "review"


def test_high_merge_confidence_is_not_an_issue():
    """Confident merges raise no confidence-driven issue."""
    assert sev.classify_confidence(0.9) is None


def test_missing_confidence_is_not_an_issue():
    """A record without a confidence score (e.g. manual edit) is not flagged."""
    assert sev.classify_confidence(None) is None


def test_confidence_threshold_boundary_is_inclusive_pass(monkeypatch):
    """Exactly at the threshold is acceptable (not below) → no issue."""
    monkeypatch.setattr(sev.constants, "MERGE_CONFIDENCE_REVIEW_THRESHOLD", 0.75)
    assert sev.classify_confidence(0.75) is None


# ─── escalate(): combining multiple triggers on one issue ─────────────────────

def test_escalate_returns_the_highest_severity():
    assert sev.escalate("info", "critical", "review") == "critical"
    assert sev.escalate("info", "review") == "review"
    assert sev.escalate("info", "info") == "info"


def test_escalate_ignores_none_triggers():
    """A None (no-issue) trigger does not affect the outcome."""
    assert sev.escalate("review", None) == "review"
    assert sev.escalate(None, None) is None


def test_severity_values_are_the_three_canonical_tiers():
    assert sev.SEVERITY_ORDER == {"info": 0, "review": 1, "critical": 2}
