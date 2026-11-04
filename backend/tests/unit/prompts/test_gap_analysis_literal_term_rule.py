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

"""The classifier judges MEANING and quotes it (ADR-100, 2026-11-04; supersedes the
ADR-048 2026-08-13 "own words" rule, #526).

**What these tests are, and are not.** They are PINS on the prompt text and on the
downstream shape the rule routes into. They are NOT evidence that the model obeys
the rule — no unit test can be, because CI mocks the provider. That evidence is the
real-provider invariance gate of ADR-100 cl. 8 (synthetic LIMS twin, six language
constellations).

History, kept because the file name still carries it: v7 told the model a later
check would refuse any term the profile does not spell, which closed the meaning-vs-
literal seam by making the classifier's verdict depend on the vault's LANGUAGE
(founder UAT-3: a German vault scored "C — Lücken" against an English posting on
five concepts the interview had just established). v9 closes the same seam the
other way: the model judges meaning in any language and cites one verbatim vault
span; the floor checks only that the span exists (``citation.quote_in_vault``).
"""

import pytest

from applire.prompts.gap_analysis import SYSTEM_PROMPT
from applire.services.keyword_ledger import (
    build_keyword_ledger,
    is_positioning_only,
)


def test_the_direct_status_definition_is_language_neutral():
    """The status list itself carries the condition — a reader who only skims the
    three bullets must see that a differently-worded or other-language statement
    counts, and must NOT see the retired own-words condition."""
    direct_line = next(
        line for line in SYSTEM_PROMPT.splitlines() if line.strip().startswith('• "direct"')
    )
    idx = SYSTEM_PROMPT.splitlines().index(direct_line)
    block = "\n".join(SYSTEM_PROMPT.splitlines()[idx:idx + 2])
    assert "in any language or wording" in block
    assert "names the requirement itself" not in block
    assert "THE PROFILE'S OWN WORDS DECIDE" not in SYSTEM_PROMPT


def test_the_rule_asks_for_one_verbatim_quote_and_routes_its_absence_to_gap():
    """The rule must name what backs a verdict, and what happens without it — a
    claim with no quotable span is a gap, never a `direct` on the model's word."""
    rule = SYSTEM_PROMPT.split("JUDGE MEANING, AND QUOTE IT", 1)[1].split("\n  - ", 1)[0]
    assert '"evidence_quote"' in rule
    assert "ONE entry" in rule and "never translated" in rule and "never stitched" in rule
    assert "INTERVIEW STATEMENTS" in rule  # a paraphrase is not a source
    assert 'the status is "gap"' in rule
    # a shared word is not evidence (SF-GAP.28)
    assert "merely shares a word" in rule
    # and the schema carries the field, or the rule asks for something un-emittable
    schema = SYSTEM_PROMPT.split("Respond ONLY", 1)[1] if "Respond ONLY" in SYSTEM_PROMPT else SYSTEM_PROMPT
    assert '"evidence_quote": "REQUIRED when status is direct or partial' in schema


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
