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

"""ADR-060 Pass B — the deterministic fact half (#322).

SF-CRITIC.9 (RPN 18, the highest row): the control's measurement already
established that a naive concept-level presence check does NOT fire on
#322's own founding case. ``test_a_concept_present_in_both_with_letter_only_depth_fires``
is the pinned proof that the richer (presence + tenure-qualification) fact
DOES fire on that exact shape — no fixture tuned to pass, the real "ISO 9001
in both, only the letter carries a duration" case from the ADR amendment.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.outcome_critic import compute_presence_facts  # noqa: E402

_LEDGER = [
    {
        "concept": "ISO 9001",
        "surface_forms": ["ISO 9001", "ISO-9001"],
        "claimable": True,
        "status": "direct",
    },
]

# The exact founding shape from the ADR-060 amendment: ISO 9001 present in
# BOTH documents; only the letter's mention carries the depth qualifier
# ("zehn Jahre" — spelled out, not a digit, which is why a bare digit check
# would ALSO miss this).
CV_TAILORED = {
    "skills": ["ISO 9001", "Qualitätsmanagement", "Six Sigma"],
    "work_experience": [
        {
            "company": "Musterwerk GmbH",
            "achievements": ["Verantwortlich für ISO 9001 Zertifizierungsaudits."],
        }
    ],
}

LETTER_DATA = {
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Mit zehn Jahren ISO-9001-Auditpraxis bringe ich genau die "
            "Qualitätssicherungs-Expertise mit, die Sie suchen.",
            "Mit freundlichen Grüßen",
        ]
    }
}


def test_a_concept_present_in_both_with_letter_only_depth_fires():
    """#322's founding case: a bare presence diff reports NOTHING (ISO 9001 is
    in both documents) — the tenure-qualification fact is what must fire."""
    facts = compute_presence_facts(CV_TAILORED, LETTER_DATA, _LEDGER)
    assert len(facts) == 1
    fact = facts[0]

    # The naive check's blind spot, pinned explicitly: presence alone agrees
    # on both sides, so a presence-only diff would report zero findings.
    assert fact.cv_present is True
    assert fact.letter_present is True

    # The richer fact: only the letter's mention carries a duration figure.
    assert fact.cv_qualified is False
    assert fact.letter_qualified is True

    # And therefore the concept IS flagged as a genuine candidate — the
    # control fires on its own founding case.
    assert fact.letter_richer is True
    assert fact.flagged is True
    assert "zehn Jahren" in (fact.letter_snippet or "")


def test_a_concept_present_in_both_at_the_same_depth_does_not_fire():
    """Sibling/negative case: when the CV states the SAME depth, there is no
    incoherence and the concept must not be flagged — proves the fact isn't
    just "concept present in the letter", it genuinely requires the letter to
    carry MORE than the CV does."""
    cv = {
        "work_experience": [
            {
                "company": "Musterwerk GmbH",
                "achievements": [
                    "Zehn Jahre Erfahrung in ISO 9001 Zertifizierungsaudits."
                ],
            }
        ]
    }
    facts = compute_presence_facts(cv, LETTER_DATA, _LEDGER)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.cv_qualified is True
    assert fact.letter_qualified is True
    assert fact.letter_richer is False
    assert fact.flagged is False


def test_a_concept_only_in_the_letter_fires_as_letter_only():
    """The plain #270-class shape — no CV mention at all."""
    cv = {"skills": ["Six Sigma"]}
    facts = compute_presence_facts(cv, LETTER_DATA, _LEDGER)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.cv_present is False
    assert fact.letter_present is True
    assert fact.letter_only is True
    assert fact.flagged is True


def test_a_concept_absent_from_the_letter_is_never_a_candidate():
    """A concept the letter never mentions at all is not this pass's concern
    (that is Pass A's territory, deliberately unbuilt — #303 closes it
    deterministically instead) — compute_presence_facts must not even return
    a row for it."""
    ledger = _LEDGER + [
        {
            "concept": "Six Sigma",
            "surface_forms": ["Six Sigma"],
            "claimable": True,
            "status": "direct",
        }
    ]
    cv = {"skills": ["Six Sigma", "ISO 9001"]}
    letter = {"body": {"paragraphs": ["Nothing relevant here."]}}
    facts = compute_presence_facts(cv, letter, ledger)
    assert facts == []


def test_a_non_claimable_ledger_entry_is_never_a_candidate():
    """An honest-gap (non-claimable) entry must never be cross-checked — the
    critic's remit is coherence of what IS claimed, not honest-gap policing
    (that is the ledger floor's job, ADR-059)."""
    ledger = [
        {
            "concept": "SAP FI",
            "surface_forms": ["SAP FI"],
            "claimable": False,
            "status": "gap",
        }
    ]
    letter = {"body": {"paragraphs": ["Mit SAP FI habe ich zehn Jahre Erfahrung."]}}
    facts = compute_presence_facts({}, letter, ledger)
    assert facts == []


def test_an_adjacent_partial_positioning_only_entry_is_excluded():
    """ADR-048 amended 2026-07-27 (is_positioning_only): an adjacent partial
    the candidate does not literally hold must not be treated as a coherence
    candidate either — mirrors verified_missing_claimable's own exclusion."""
    ledger = [
        {
            "concept": "Payments platform",
            "surface_forms": ["Payments platform"],
            "claimable": True,
            "status": "partial",
            "adjacent_evidence": "Built an internal billing ledger instead.",
        }
    ]
    letter = {
        "body": {
            "paragraphs": [
                "I have ten years with a payments platform in production."
            ]
        }
    }
    facts = compute_presence_facts({}, letter, ledger)
    assert facts == []


# ── Adversarial pass (2026-07-30), finding 3 — snippet is a SENTENCE ────────
#
# A letter's document UNITS are whole paragraphs (_document_units reuses
# keyword_ledger._draft_strings, which flattens body.paragraphs entry-by-
# entry). Two advisories about two different concepts in the SAME paragraph
# therefore quoted the ENTIRE ~700-character paragraph twice, byte-identical
# except for the concept name — the candidate sees the same wall of text
# twice. Narrowing to the sentence containing the matched surface form is a
# FACT (ADR-062 clause 1: which sentence contains a given literal string is
# settled by string containment alone, no reading for meaning) — never a
# summarisation or rewrite.

_TWO_CONCEPT_LEDGER = [
    {
        "concept": "Arbeitsvorbereitung",
        "surface_forms": ["Arbeitsvorbereitung"],
        "claimable": True,
        "status": "direct",
    },
    {
        "concept": "Digitalisierung",
        "surface_forms": ["Digitalisierung"],
        "claimable": True,
        "status": "direct",
    },
]

_TWO_CONCEPT_LETTER = {
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "In meiner letzten Rolle war ich für die Arbeitsvorbereitung "
            "zuständig. Außerdem trieb ich die Digitalisierung der Fertigung "
            "entschlossen voran.",
            "Mit freundlichen Grüßen",
        ]
    }
}


def test_the_snippet_is_the_sentence_not_the_whole_paragraph():
    """Two concepts in ONE paragraph, each in its own sentence, neither in
    the CV at all (letter_only) -- each advisory's snippet must be its OWN
    sentence, not the shared ~120-char paragraph both concepts live in."""
    facts = compute_presence_facts({}, _TWO_CONCEPT_LETTER, _TWO_CONCEPT_LEDGER)
    by_concept = {f.concept: f for f in facts}
    assert set(by_concept) == {"Arbeitsvorbereitung", "Digitalisierung"}

    av = by_concept["Arbeitsvorbereitung"]
    dg = by_concept["Digitalisierung"]

    assert av.letter_snippet == (
        "In meiner letzten Rolle war ich für die Arbeitsvorbereitung zuständig."
    )
    assert dg.letter_snippet == (
        "Außerdem trieb ich die Digitalisierung der Fertigung entschlossen voran."
    )
    # The whole point: they must no longer be byte-identical.
    assert av.letter_snippet != dg.letter_snippet
    # Neither snippet is the whole two-sentence paragraph.
    full_paragraph = _TWO_CONCEPT_LETTER["body"]["paragraphs"][1]
    assert av.letter_snippet != full_paragraph
    assert dg.letter_snippet != full_paragraph


def test_the_advisory_message_quotes_the_narrowed_sentence_not_the_paragraph():
    """End to end through _build_advisory: the persisted message text quotes
    the SENTENCE, matching the FMEA's "adjudicate at a glance" requirement."""
    from applire.services.outcome_critic import _build_advisory

    facts = compute_presence_facts({}, _TWO_CONCEPT_LETTER, _TWO_CONCEPT_LEDGER)
    by_concept = {f.concept: f for f in facts}
    advisory_av = _build_advisory(by_concept["Arbeitsvorbereitung"])
    advisory_dg = _build_advisory(by_concept["Digitalisierung"])

    assert "Arbeitsvorbereitung zuständig" in advisory_av.message["de"]
    assert "Digitalisierung der Fertigung" in advisory_dg.message["de"]
    # Neither message drags in the OTHER concept's sentence.
    assert "Digitalisierung" not in advisory_av.letter_state
    assert "Arbeitsvorbereitung" not in advisory_dg.letter_state


def test_a_single_sentence_unit_is_unaffected_by_narrowing():
    """The pre-existing founding-case fixture (ISO 9001, one-sentence letter
    paragraph) must keep quoting the whole sentence unchanged -- narrowing is
    a no-op when the unit IS already one sentence."""
    facts = compute_presence_facts(CV_TAILORED, LETTER_DATA, _LEDGER)
    assert len(facts) == 1
    assert "zehn Jahren" in (facts[0].letter_snippet or "")
