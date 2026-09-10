# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#415 — the vault-evidence anchor stops choosing which SENSE of a concept answers the JD.

RULING W1-6 (founder, 2026-09-09), and it relocates the issue's own diagnosis. #415 says
*"the concept's granularity, not the writer's honesty, is what fails here"* and proposes
ADR-065 decomposition. Granularity is why several vault sentences qualify for one concept;
it is not what chooses between them. That was a deterministic selector, and it chose by
counting characters.

Captured run-13 (`backend/logs/llm/2026-08-01.jsonl` rec 361, `controlling_emma_de`,
`mistral-medium-3-5`), free evidence, no provider call. `HGB-Abschluss` has SIX qualifying
vault units; the one answering the JD's first-named duty is **third by length**:

    134  work_experience[0].responsibilities[0]  "…Monatsabschluss und Reporting (HGB)…"
     98  projects[2].org                         "…Fertigstellungsgrad (HGB-konform)…"
     57  work_experience[0].responsibilities[3]  "Betreuung der Wirtschaftsprüfer im
                                                  Jahresabschluss (HGB)."

`Jahresabschluss` occurred twice in a 55,264-character prompt, both times buried in the raw
profile JSON. The delivered CV carried `HGB` ×4, `Jahresabschluss` 0, `Wirtschaftsprüfer` 0,
and the blind CFO scored the role's first-named duty *teilweise* for exactly that.

**Before/after on that captured call** (`tmp/replay_415.py`, both arms deterministic, the
"before" arm's selector loaded from git @ `24ee8cd6`):

    Jahresabschluss in the digest   False → True
    Wirtschaftsprüfer in the digest False → True
    distinct concepts represented   8 → 9      (wider than before, not narrower)
    digest                          10 → 24 items · rendered block 3,112 → 5,123 chars
                                    (+3.6 % of that writer prompt)

Two narrower shapes were built and falsified before this one, and both are asserted below
so they are not re-proposed: top-2-by-length (the second unit is `projects[2].org`), and
anchor-plus-longest-same-owner (`projects[2]` shares the work entry, so the owner filter
does not discriminate). The per-concept cap alone is also not sufficient — under the shared
ceiling of 10 the answering sentence arrives but represented concepts fall 8 → 4, so the CV
chain gets its own ceiling.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.vault_evidence import (  # noqa: E402
    CV_DIGEST_CAP,
    DEFAULT_DIGEST_CAP,
    _MAX_UNITS_PER_CONCEPT,
    _anchor_for_concept,
    _anchors_for_concept,
    select_vault_evidence,
)

_WORK_ID = "11111111-2222-3333-4444-555555555555"

#: run-13's own shape, reduced to what the selector reads: one concept, three qualifying
#: units, and the one that answers the posting is the SHORTEST.
_LONGEST = (
    "Verantwortlich für Monatsabschluss und Reporting (HGB) der GmbH mit 480 "
    "Mitarbeitenden; Kommentierung für Geschäftsführung und Beirat."
)
_MIDDLE = (
    "Bewertung langfristiger Aufträge nach Fertigstellungsgrad (HGB-konform) für "
    "den Sondermaschinenbereich."
)
_ANSWERS_THE_JD = "Betreuung der Wirtschaftsprüfer im Jahresabschluss (HGB)."

_LEDGER = [
    {
        "concept": "HGB-Abschluss",
        "surface_forms": ["HGB-Abschluss", "HGB"],
        "claimable": True, "status": "direct", "fit_weight": 1.0,
        "evidence": "Monatsabschluss und Reporting (HGB)",
    }
]


def _profile() -> dict:
    return {
        "contact": {"first_name": "Emma", "last_name": "Schneider",
                    "email": "emma@example.com", "phone": None, "location": "Stuttgart",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"de": "Controllerin", "en": ""},
        "work_experience": [
            {
                "id": _WORK_ID, "company": "Maschinenbau GmbH", "role": "Controllerin",
                "start_date": "2018-01", "end_date": None, "is_current": True,
                "responsibilities": [_LONGEST, _MIDDLE, _ANSWERS_THE_JD],
            }
        ],
        "education": [], "skills": [], "languages": [], "certifications": [],
    }


def _units():
    from applire.services.oracle.matchers.vault import build_vault_index

    return build_vault_index(_profile()).units


def _texts(items):
    return [i.text for i in items]


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------


def test_the_answering_sentence_is_offered_although_it_is_the_shortest():
    """The whole bug in one assertion: the sentence that answers the JD's first-named duty
    is the shortest of the three, and it must reach the writer anyway."""
    got = _texts(_anchors_for_concept(_LEDGER[0], _units()))
    assert _ANSWERS_THE_JD in got
    assert len(got) == 3


def test_the_first_unit_is_unchanged_so_the_change_is_purely_additive():
    """Nothing previously selected may move — the old single-anchor rule's answer is still
    element 0, and `_anchor_for_concept` (which other call sites and tests use) still
    returns exactly it."""
    anchors = _anchors_for_concept(_LEDGER[0], _units())
    assert anchors[0].text == _LONGEST
    assert _anchor_for_concept(_LEDGER[0], _units()).text == _LONGEST


def test_a_concept_with_one_qualifying_unit_behaves_exactly_as_before():
    entry = {**_LEDGER[0], "concept": "Wirtschaftsprüfer",
             "surface_forms": ["Wirtschaftsprüfer"]}
    assert _texts(_anchors_for_concept(entry, _units())) == [_ANSWERS_THE_JD]


def test_the_cap_bounds_a_concept_the_vault_mentions_everywhere():
    """A coarse concept must not be able to spend the whole digest on itself. The RC run's
    coarsest concept had 17 qualifying units."""
    profile = _profile()
    profile["work_experience"][0]["responsibilities"] = [
        f"Monatsabschluss nach HGB im Bereich {i}, mit ausführlicher Kommentierung."
        for i in range(12)
    ]
    from applire.services.oracle.matchers.vault import build_vault_index

    got = _anchors_for_concept(_LEDGER[0], build_vault_index(profile).units)
    assert len(got) == _MAX_UNITS_PER_CONCEPT == 3


@pytest.mark.parametrize("n", [1, 2])
def test_the_two_falsified_shapes_are_not_re_proposed(n):
    """Both were built first and measured against the captured run. Recorded as assertions
    so the next agent re-derives the refutation instead of the proposal.

    * top-2 by length — the second unit is the MIDDLE one, not the answering sentence;
    * anchor + longest SAME-OWNER sibling — every unit here shares the work entry, so the
      owner filter does not discriminate (on run 13 the runner-up `projects[2]` is
      associated with the same entry for the same reason).
    """
    ordered = _texts(_anchors_for_concept(_LEDGER[0], _units()))
    assert ordered[:2] == [_LONGEST, _MIDDLE]
    assert _ANSWERS_THE_JD not in ordered[:n], (
        "a cap below 3 does not reach the answering sentence — this is the measurement "
        "that set _MAX_UNITS_PER_CONCEPT, not a preference"
    )


# ---------------------------------------------------------------------------
# The second half: the CV chain's own ceiling
# ---------------------------------------------------------------------------


def test_the_cv_chain_has_its_own_digest_ceiling_and_the_letter_keeps_the_default():
    """The per-concept cap alone buys the answering sentence by starving concept breadth:
    measured on run 13, represented concepts fall 8 → 4 under the shared ceiling of 10, and
    recover to 9 — wider than before the change — at 24. `services/cover_letter.py` is owned
    by no work package this run and #415 is a CV defect, so the letter is untouched."""
    assert CV_DIGEST_CAP > DEFAULT_DIGEST_CAP
    assert DEFAULT_DIGEST_CAP == 10


def test_the_cv_generation_path_passes_its_own_cap():
    """A constant nothing passes is a constant that does nothing — the seam, not the value."""
    import inspect

    from applire.services import cv

    src = inspect.getsource(cv)
    assert "cap=CV_DIGEST_CAP," in src
    assert "CV_DIGEST_CAP," in src, "and it is imported at the call site"


def test_breadth_and_the_answering_sentence_together_under_the_cv_cap():
    """The property the two halves buy jointly, on a vault carrying several concepts: every
    concept still reaches the digest AND the short answering sentence is there."""
    profile = _profile()
    profile["work_experience"][0]["responsibilities"] += [
        "Pflege der Kostenstellen- und Kostenträgerrechnung im Monatsabschluss.",
        "Konsolidierung von zwei Tochtergesellschaften nach IFRS 16.",
    ]
    ledger = _LEDGER + [
        {"concept": "Kostenrechnung", "surface_forms": ["Kostenrechnung", "Kostenstellen"],
         "claimable": True, "status": "direct", "fit_weight": 1.0, "evidence": "x"},
        {"concept": "Konsolidierung", "surface_forms": ["Konsolidierung"],
         "claimable": True, "status": "direct", "fit_weight": 1.0, "evidence": "x"},
    ]
    items = select_vault_evidence(ledger, "", profile, cap=CV_DIGEST_CAP)
    assert _ANSWERS_THE_JD in _texts(items)
    assert {i.concept for i in items} >= {"HGB-Abschluss", "Kostenrechnung", "Konsolidierung"}


def test_the_second_sense_is_labelled_so_the_digest_stays_readable():
    """`reason` is what the block's own rendering and every future measurement key on; a
    second sense arriving indistinguishable from an anchor is an unmeasurable change."""
    items = select_vault_evidence(_LEDGER, "", _profile(), cap=CV_DIGEST_CAP)
    reasons = {i.reason for i in items if i.text in (_MIDDLE, _ANSWERS_THE_JD)}
    assert reasons == {"claimable-concept-second-sense"}
