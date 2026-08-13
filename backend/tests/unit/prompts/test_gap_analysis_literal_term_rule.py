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

"""A `direct` requires the profile's own words (ADR-048 amended 2026-08-13, #526).

**What these tests are, and are not.** They are PINS on the prompt text and on the
downstream shape the rule routes into. They are NOT evidence that the model obeys
the rule — no unit test can be, because CI mocks the provider. That evidence is the
captured-call replay of the run-1 classification against the real provider, and it
is a real gate rather than a formality: in run 1 the model had the (a) ADJACENT
branch available and used `adjacent_evidence` **zero times across 32
classifications**.

The seam being closed: two instruments decide whether the candidate "has" a
requirement, and they ask different questions. This prompt judges by MEANING; the
ADR-061/#318 affirmative floor grounds by LITERAL TERM, using the Oracle's own
`ground_skill_claim`. Nothing reconciled them, so a `direct` graded on
differently-named vault evidence was demoted to `gap` with its evidence blanked —
and the adjacency the classifier had just cited was discarded with it.
"""

import pytest

from applire.prompts.gap_analysis import SYSTEM_PROMPT
from applire.services.keyword_ledger import (
    build_keyword_ledger,
    is_positioning_only,
)


def test_the_direct_status_definition_requires_the_profile_to_name_the_term():
    """The status list itself carries the condition — a reader who only skims the
    three bullets must still see it."""
    direct_line = next(
        line for line in SYSTEM_PROMPT.splitlines() if line.strip().startswith('• "direct"')
    )
    idx = SYSTEM_PROMPT.splitlines().index(direct_line)
    block = "\n".join(SYSTEM_PROMPT.splitlines()[idx:idx + 2])
    assert "names the requirement itself" in block


def test_the_rule_routes_the_differently_named_case_to_partial_plus_adjacent():
    """The rule must name the destination, not merely forbid `direct` — a
    prohibition with no alternative is how the run-1 dead cell was produced in the
    first place."""
    assert "THE PROFILE'S OWN WORDS DECIDE" in SYSTEM_PROMPT
    rule = SYSTEM_PROMPT.split("THE PROFILE'S OWN WORDS DECIDE", 1)[1].split("  - ", 1)[0]
    assert "surface_forms" in rule
    assert '"partial"' in rule and '"adjacent_evidence"' in rule
    assert 'Never "direct"' in rule
    # It must also say WHY, because the consequence is invisible from here: the
    # cost of getting this wrong lands two components downstream.
    assert "no evidence at all" in rule.lower()


def test_the_rule_does_not_reopen_the_invented_adjacency_it_depends_on():
    """`adjacent_evidence` is only honest when the profile really holds the
    substitute. The pre-existing prohibition must survive the new rule, or the
    fix trades an unsatisfiable requirement for a fabricated one."""
    assert "Never invent an adjacency to fill the field" in SYSTEM_PROMPT


@pytest.mark.parametrize("status", ["direct", "gap"])
def test_only_a_partial_carries_the_pointer_the_rule_produces(status):
    """The shape the rule routes into, checked against the builder rather than
    assumed: the ledger writes `adjacent_evidence` for a `partial` and nothing
    else, so a model that ignores the rule cannot smuggle the pointer onto
    another status."""
    out = build_keyword_ledger(
        required_skills=["Digitalisierung"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "Digitalisierung",
                "status": status,
                "reason": "MES rollout and Industrie-4.0 roadmap",
                "adjacent_evidence": "MES-Einführung",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "Digitalisierung")
    assert not entry.get("adjacent_evidence")
    assert is_positioning_only(entry) is False


def test_the_partial_the_rule_asks_for_is_exempt_from_the_vault_evidence_floor():
    """The whole point of routing to `partial` + `adjacent_evidence` rather than
    `direct`: the row survives `assert_claimable_backed`, which is what demoted the
    `direct` to a material-free `gap` in run 1.

    `is_positioning_only` is the exemption predicate (`_claimable_backing_violation`
    clause 3), so this pins the property the rule depends on — with a vault that
    does NOT contain the JD's term, exactly as run 1's did not."""
    from applire.services.keyword_ledger import assert_claimable_backed

    out = build_keyword_ledger(
        required_skills=["Digitalisierung"],
        nice_to_have_skills=[],
        keywords=[],
        classifications=[
            {
                "concept": "Digitalisierung",
                "status": "partial",
                # `services/gap.py::ledger_input_from_classification` copies the
                # classifier's `reason` onto `evidence`; the builder reads
                # `evidence`. It must be non-empty here, because
                # `_claimable_backing_violation` checks row COHERENCE (clause 2,
                # `no_evidence`) BEFORE it reaches the clause-3 exemption — the
                # adjacency pointer does not excuse a row from carrying its own
                # grounding, it only excuses it from the literal-term floor.
                "evidence": "Einführung eines MES-Systems, Industrie-4.0-Roadmap",
                "adjacent_evidence": "MES-Einführung",
            }
        ],
    )
    entry = next(e for e in out if e["concept"] == "Digitalisierung")
    assert is_positioning_only(entry) is True
    assert entry["evidence"], "fixture premise: an adjacent partial still needs evidence"

    vault = {
        "skills": [{"name": "MES", "category": "technical"}],
        "work_experience": [
            {
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "achievements": ["Leitung der Einführung eines MES-Systems"],
            }
        ],
    }
    # Fixture premise, checked with the real predicate rather than by eye: the
    # vault must NOT name the JD's term, or this test proves nothing.
    assert "digitalisierung" not in str(vault).lower()

    healed, violations = assert_claimable_backed(out, vault, seam="test")
    survivor = next(e for e in healed if e["concept"] == "Digitalisierung")
    assert not violations, f"the adjacent partial was healed away: {violations!r}"
    assert survivor["claimable"] is True
    assert survivor["status"] == "partial"


def test_the_adjacency_pointer_survives_the_api_schema():
    """`services/gap.py`'s `_LEDGER_PUBLISHABLE_KEYS` has listed `adjacent_evidence`
    as publishable since ADR-048, but `KeywordLedgerEntry` declared neither the
    field nor `extra="allow"` — so every gap-analysis response silently dropped it,
    on every status. The allowlist was a control the schema beneath it defeated.

    This matters more after ADR-048's 2026-08-13 clause 1, which makes the field
    the carrier of the honest answer for a whole class of requirement: without it
    on the wire, the agent channel and the frontend see a bare `partial` and cannot
    tell an adjacent capability from a below-the-bar shortfall."""
    from applire.schemas.gap import KeywordLedgerEntry
    from applire.services.gap import _LEDGER_PUBLISHABLE_KEYS

    assert "adjacent_evidence" in _LEDGER_PUBLISHABLE_KEYS
    assert "adjacent_evidence" in KeywordLedgerEntry.model_fields

    row = KeywordLedgerEntry.model_validate({
        "concept": "TOGAF",
        "surface_forms": ["TOGAF"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "partial",
        "evidence": "Five years of architecture practice.",
        "claimable": True,
        "adjacent_evidence": "arc42",
    })
    assert row.adjacent_evidence == "arc42"
    # A row that never had one stays clean rather than emitting a null the
    # frontend would have to special-case.
    plain = KeywordLedgerEntry.model_validate({
        "concept": "Kubernetes", "surface_forms": ["K8s"], "sources": ["required"],
        "fit_weight": 1.0, "status": "direct", "evidence": "Ran EKS.", "claimable": True,
    })
    assert plain.adjacent_evidence is None
