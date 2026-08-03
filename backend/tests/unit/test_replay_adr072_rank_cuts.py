# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-072 clause 1, asserted against REAL captured writer output.

Why this file exists, stated plainly so nobody merges it into
``test_bullet_cut_ranking.py`` and loses the point:

``rank_cuts`` recounts concept carriers *inside* its removal loop, so that the
second-to-last carrier of a concept becomes protected the moment the last one is
taken. On 2026-08-02 that recount was hoisted out of the loop as a mutation probe
— deleting exactly the behaviour clause 1 exists for — and **all 3991 tests
passed, including the test whose name claimed to guard it.**

The reason was not a weak assertion. It was the fixture. Its "second carrier" was
hand-written as ``Verpackungen`` against a first carrier saying
``Verpackungslinien``, and ``surface_present`` does not match those to each other
— German compounding diverges at ``Verpackung[s|en]``. So the fixture never had
two carriers, the recount never had anything to recount, and the guard was inert
while looking green.

A human writing a fixture does not naturally produce that divergence. The model
produces it constantly. So the inputs below are **not written** — they are lifted
verbatim from `cv_tailoring` generator responses captured on 2026-08-01/02, and
they carry one concept under two genuinely different surface forms:
``Spritzguss`` and ``Spritzgussmaschinen``.

The fixture's claimed property is not trusted either: ``test_the_fixture_really_
has_two_carriers`` runs the real predicate over the real strings before any
behaviour is asserted. A fixture that stops satisfying its own premise must fail
as a fixture, not silently disarm the test that depends on it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from applire.services.ats_audit import _norm, surface_present
from applire.services.bullet_cuts import rank_cuts

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "replay"
    / "adr072_rank_cuts_real_bullets.json"
)


@pytest.fixture(scope="module")
def real() -> dict:
    # Not gated on the file's existence. A missing replay fixture means this tier
    # is broken, not that the test is inapplicable — the `.run5fixture` skips are
    # the anti-pattern this deliberately does not copy.
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_really_has_two_carriers_under_different_surface_forms(real):
    """The premise, checked with the production predicate before anything is asserted.

    This is the assertion the 2026-08-02 fixture would have failed.
    """
    concept = real["concept"]
    a, b, f = real["carrier_a"], real["carrier_b"], real["non_carrier"]

    assert surface_present(concept, _norm(a)), "carrier_a does not carry the concept"
    assert surface_present(concept, _norm(b)), "carrier_b does not carry the concept"
    assert not surface_present(concept, _norm(f)), "non_carrier carries the concept"

    forms_a = set(real["carrier_a_surface_forms"])
    forms_b = set(real["carrier_b_surface_forms"])
    assert forms_a and forms_b
    assert forms_a != forms_b, (
        "the two carriers use the SAME surface form — the compound-divergence "
        f"shape this test exists for is gone: {forms_a} vs {forms_b}"
    )
    # And the divergence is real, not cosmetic: neither form matches the other's text.
    only_a = next(iter(forms_a - forms_b))
    only_b = next(iter(forms_b - forms_a))
    assert not surface_present(only_b, _norm(a)) or not surface_present(only_a, _norm(b)), (
        "the two surface forms match each other's text, so this fixture would not "
        "have caught the 2026-08-02 defect"
    )


def test_the_last_carrier_of_a_concept_is_never_cut(real):
    """ADR-072 clause 1 on real output.

    Three real bullets, ``keep=1``. The caller's own tier ranks BOTH carriers to be
    cut before the non-carrier, so nothing except the coverage criterion can save
    the concept — which is the only way this test can observe clause 1 at all.

    With the recount inside the loop: carrier_a is cut, carrier_b becomes sole and
    protected, the non-carrier is cut instead, and the concept survives.
    With the recount hoisted: both carriers read as count==2 forever, neither is
    ever protected, both are cut, and the concept is gone from the document.
    """
    concept = real["concept"]
    texts = [real["carrier_a"], real["carrier_b"], real["non_carrier"]]
    tiers = [(0,), (0,), (1,)]  # both carriers sort BEFORE the filler

    cuts = rank_cuts(texts, tiers, keep=1, concept_groups=([concept],))

    removed = {c.index for c in cuts}
    survivors = [t for i, t in enumerate(texts) if i not in removed]
    assert len(survivors) == 1

    assert surface_present(concept, _norm(survivors[0])), (
        f"the deterministic tail cut every carrier of {concept!r}. ADR-072 clause 1 "
        "requires the last carrier to be protected once its siblings are taken — "
        "check that rank_cuts still recounts INSIDE its removal loop."
    )
    # And it must be the protected carrier that survived, not the filler.
    assert survivors[0] == real["carrier_b"]
    # Protection shows up as a bullet NOT being cut — the protected carrier is
    # never a Cut, so no cut here may be flagged `sole_carrier`. Stated explicitly
    # so a future reader does not "fix" this into asserting the opposite.
    assert all(not c.sole_carrier for c in cuts)
