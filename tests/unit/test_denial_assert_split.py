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

"""Asserting a denial vs. refusing a claim — the two halves of the ADR-059
floor, split (ADR-059 amended 2026-08-08; #486), and the corpus the release
predicate is allowed to read (#480 step 1).

**#486 — one predicate was serving two acts with opposite polarity.**
``is_denied_concept``'s compound-containment branch ("CSS" is a whole word
strictly inside the denied "Tailwind CSS") is the right instrument for
*refusing a claim*: a false positive there claims LESS, which is ADR-062
clause 5's sanctioned direction. It is the wrong instrument for *asserting a
denial* — writing ``status="denied"`` + :data:`DENIED_EVIDENCE` is testimony,
a statement that the candidate said they lack this, and the candidate never
named "CSS". These tests pin the split: the never-upgrade half keeps
containment everywhere, the assert half narrows to the DECLARED term.

**#480 step 1 — an ``unconfirmed`` entry may not release a denial.** The
release condition (``_independently_affirmed``, via the containment branch)
reads the vault's flattened literal text, and that corpus was built from the
whole profile with no status filter — so an ADR-061 clause-3 ``unconfirmed``
entry (the reconciler's own inference, never claimable, able to back nothing)
released a persisted denial at the next ledger rebuild. Synthetic
reconstruction of the issue's ``probe_compound_denial`` sequence.
"""

import pytest

from applire.services.keyword_ledger import (
    DENIAL_FLOOR_EVIDENCE,
    DENIED_EVIDENCE,
    _enforce_denial_stance,
    assert_claimable_backed,
    build_keyword_ledger,
    profile_literal_corpus,
    upgrade_ledger_for_concepts,
)

# ── synthetic fixtures ──────────────────────────────────────────────────────

DENIED_COMPOUND = "Tailwind CSS"
HEAD_NOUN = "CSS"


def _cls(concept, status, surface_forms=None, evidence=""):
    item = {"concept": concept, "status": status, "evidence": evidence}
    if surface_forms is not None:
        item["surface_forms"] = surface_forms
    return item


def _by_concept(ledger):
    return {e["concept"]: e for e in ledger}


def _entry(concept, status="gap", claimable=False, evidence="", **kw):
    e = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": status,
        "evidence": evidence,
        "claimable": claimable,
    }
    e.update(kw)
    return e


def _denial(concept, level="direct"):
    """A complete persisted ``DeniedConcept`` (the schema validates every
    field, and a half-filled one makes the heal's vault index fail open)."""
    return {
        "concept": concept,
        "statement": f"I have never worked with {concept}.",
        "source": "interview",
        "date": "2026-01-15",
        "denial_level": level,
    }


def _vault(*, css_status=None, extra_skills=()):
    """A vault denying the compound, optionally carrying a CSS skill at the
    given ADR-061 status ("confirmed" / "unconfirmed")."""
    skills = [{"name": "Python", "category": "technical"}]
    skills.extend(dict(s) for s in extra_skills)
    if css_status is not None:
        skills.append(
            {"name": HEAD_NOUN, "category": "technical", "status": css_status}
        )
    return {
        "skills": skills,
        "metadata": {"denied_concepts": [_denial(DENIED_COMPOUND, "direct")]},
    }


def _rebuild(profile_json, concepts=(HEAD_NOUN,)):
    return build_keyword_ledger(
        classifications=[
            _cls(c, "direct", [c], evidence="the classifier's own adjacency read")
            for c in concepts
        ],
        required_skills=list(concepts),
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=[{"concept": DENIED_COMPOUND, "denial_level": "direct"}],
        profile_json=profile_json,
    )


# ── #480 step 1 — an unconfirmed entry may not release a denial ─────────────


def test_unconfirmed_vault_entry_does_not_release_a_persisted_denial():
    """#480's ``probe_compound_denial`` sequence: the candidate denies the
    compound, the reconciler later infers an ``unconfirmed`` skill of the head
    noun, and the NEXT ledger rebuild hands that inference to the release
    predicate — the denial evaporates and the concept comes back claimable.

    An ``unconfirmed`` entry backs nothing (ADR-061 clause 3), so it cannot be
    the independent affirmation that releases a denial either."""
    entry = _by_concept(_rebuild(_vault(css_status="unconfirmed")))[HEAD_NOUN]
    assert entry["claimable"] is False


def test_a_confirmed_vault_entry_still_releases_the_containment_floor():
    """The contrast case — the filter is status-scoped, not a blanket
    re-closing of #249/#351. Real, confirmed vault evidence for the head noun
    outside the denied compound still keeps it claimable."""
    entry = _by_concept(_rebuild(_vault(css_status="confirmed")))[HEAD_NOUN]
    assert entry["claimable"] is True
    assert entry["status"] == "direct"


def test_an_entry_with_no_status_key_at_all_still_releases():
    """Legacy/hand-written vault rows carry no ``status`` key; only an
    explicit ``unconfirmed`` is filtered."""
    entry = _by_concept(_rebuild(_vault(css_status=None, extra_skills=[
        {"name": HEAD_NOUN, "category": "technical"}
    ])))[HEAD_NOUN]
    assert entry["claimable"] is True


# ── #486 — the canonical split ──────────────────────────────────────────────


def test_declared_denial_asserts_and_containment_only_floors_without_testimony():
    """The canonical #486 pin, both rows in ONE rebuild:

    * ``Tailwind CSS`` — the candidate's own declared term — keeps
      ``denied`` + :data:`DENIED_EVIDENCE`;
    * ``CSS`` — reached only by containment — is floored to a non-claimable
      ``gap`` with an honest, non-testimony marker. Floored, never asserted.
    """
    ledger = _by_concept(_rebuild(_vault(), concepts=(DENIED_COMPOUND, HEAD_NOUN)))

    declared = ledger[DENIED_COMPOUND]
    assert declared["status"] == "denied"
    assert declared["claimable"] is False
    assert declared["evidence"] == DENIED_EVIDENCE
    assert declared["denial_level"] == "direct"

    contained = ledger[HEAD_NOUN]
    assert contained["claimable"] is False  # the never-upgrade half, unchanged
    assert contained["status"] == "gap"
    assert contained["evidence"] != DENIED_EVIDENCE
    assert contained["evidence"] == DENIAL_FLOOR_EVIDENCE
    assert "denial_level" not in contained


def test_upgrade_seam_floors_a_containment_only_match_without_asserting():
    """The second assert site (``upgrade_ledger_for_concepts``) reaches the
    same verdict as a rebuild — ADR-059's every-seam rule. Nothing affirms the
    head noun (no vault corpus, a pure-denial turn), so the never-upgrade half
    fires and the assert half does not."""
    out, changed = upgrade_ledger_for_concepts(
        [_entry(HEAD_NOUN)],
        [HEAD_NOUN],
        "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0]["claimable"] is False
    assert out[0]["status"] == "gap"
    assert out[0]["evidence"] == DENIAL_FLOOR_EVIDENCE
    assert changed is True


def test_upgrade_seam_still_asserts_a_declared_denial():
    """The declared half is untouched at this seam."""
    out, changed = upgrade_ledger_for_concepts(
        [_entry(DENIED_COMPOUND)],
        [DENIED_COMPOUND],
        "I have never used Tailwind CSS.",
        denied_concepts=[{"concept": DENIED_COMPOUND, "denial_level": "partial"}],
    )
    assert out[0]["status"] == "denied"
    assert out[0]["evidence"] == DENIED_EVIDENCE
    assert out[0]["denial_level"] == "partial"
    assert changed is True


def test_a_containment_only_match_never_reverses_a_standing_claim_into_testimony():
    """#352's reversal keeps working — a claimable row still loses its claim —
    but the reversal is a floor, not a fabricated statement about testimony."""
    out, _ = upgrade_ledger_for_concepts(
        [_entry(HEAD_NOUN, status="direct", claimable=True, evidence="an earlier turn")],
        [HEAD_NOUN],
        "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0]["claimable"] is False
    assert out[0]["evidence"] != DENIED_EVIDENCE


# ── the never-upgrade half, pinned in all three floor call paths ────────────


def test_never_upgrade_half_still_fires_by_containment_in_all_three_paths():
    """ADR-064's consistency rule at its true width: the split narrows what may
    be ASSERTED, never what may be REFUSED. All three floor call paths still
    refuse the claim on a containment-only match."""
    from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

    profile = _vault()

    # 1 — the rebuild floor.
    assert _by_concept(_rebuild(profile))[HEAD_NOUN]["claimable"] is False

    # 2 — the in-place upgrade seam.
    out, _ = upgrade_ledger_for_concepts(
        [_entry(HEAD_NOUN)], [HEAD_NOUN], "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0]["claimable"] is False

    # 3 — the vault re-evaluation (skip-only by design: it never writes
    # "denied", it only refuses to upgrade).
    css_in_vault = dict(profile)
    css_in_vault["skills"] = profile["skills"] + [
        {"name": "Tailwind CSS", "category": "technical"}
    ]
    healed, changed = reevaluate_gap_ledger_against_vault(
        [_entry(HEAD_NOUN)], css_in_vault
    )
    assert healed[0]["claimable"] is False
    assert healed[0]["status"] != "denied"


def test_the_f8_adjacency_inference_is_still_floored():
    """The 2026-07-23 blind-run shape ("RAG experience typically involves
    embeddings"): the classifier upgrades a denied concept by adjacency and the
    floor reverses it. The vault carries the denied compounds only, so nothing
    affirms the broad term independently."""
    profile = {
        "work_experience": [
            {"role": "ML Engineer",
             "technologies": ["RAG pipeline design", "RAG reranking improvements"]}
        ],
        "metadata": {"denied_concepts": [_denial("RAG pipeline"),
                                         _denial("RAG reranking")]},
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], "typically involves embeddings")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=["RAG pipeline", "RAG reranking"],
        profile_json=profile,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["claimable"] is False
    assert rag["evidence"] != DENIED_EVIDENCE  # the candidate never named "RAG"


# ── instrument lockstep (ADR-059 am. 2026-08-08 clause (b)) ─────────────────

LOCKSTEP_PROFILE = {
    "skills": [{"name": "Kubernetes", "category": "technical"}],
    "metadata": {"denied_concepts": [_denial("Kubernetes", "partial")]},
}
LOCKSTEP_ROW = _entry(
    "Kubernetes", status="direct", claimable=True, evidence="ran clusters at Acme"
)


def test_floor_upgrade_seam_and_heal_write_one_identical_denied_shape():
    """#497's heal duplicated ``upgrade_ledger_for_concepts``'s denied write
    byte-for-byte and, by doing so, ALREADY diverged from
    ``_enforce_denial_stance``'s — which additionally mirrors ``denial_level``.
    A copy silently diverges on the first edit; this test fails when any of the
    three writers drifts."""
    corpus = profile_literal_corpus(LOCKSTEP_PROFILE) or None

    floored = _enforce_denial_stance(
        [dict(LOCKSTEP_ROW)],
        [{"concept": "Kubernetes", "denial_level": "partial"}],
        corpus,
    )[0]
    upgraded, _ = upgrade_ledger_for_concepts(
        [dict(LOCKSTEP_ROW)],
        ["Kubernetes"],
        "I have never run Kubernetes myself.",
        denied_concepts=[{"concept": "Kubernetes", "denial_level": "partial"}],
        vault_corpus=corpus,
    )
    healed, violations = assert_claimable_backed(
        [dict(LOCKSTEP_ROW)], LOCKSTEP_PROFILE, seam="lockstep-test"
    )

    assert violations, "the heal must fire on a claimable row matching a denial"
    assert floored["denial_level"] == "partial"
    assert healed[0] == floored
    assert upgraded[0] == floored


def test_the_heal_floors_a_containment_only_match_instead_of_asserting():
    """Lockstep on the OTHER half: the heal must not write testimony the floor
    would refuse to write."""
    row = _entry(HEAD_NOUN, status="direct", claimable=True, evidence="an earlier turn")
    healed, violations = assert_claimable_backed([row], _vault(), seam="lockstep-test")
    assert violations
    assert healed[0]["claimable"] is False
    assert healed[0]["evidence"] != DENIED_EVIDENCE


@pytest.mark.parametrize("writer", ["floor", "upgrade", "heal"])
def test_no_writer_asserts_testimony_for_an_undeclared_term(writer):
    """One property, three instruments: :data:`DENIED_EVIDENCE` is only ever
    written for a term the candidate actually declared."""
    row = _entry(HEAD_NOUN, status="direct", claimable=True, evidence="an earlier turn")
    corpus = profile_literal_corpus(_vault()) or None
    if writer == "floor":
        out = _enforce_denial_stance([dict(row)], [{"concept": DENIED_COMPOUND}], corpus)
    elif writer == "upgrade":
        out, _ = upgrade_ledger_for_concepts(
            [dict(row)], [HEAD_NOUN], "I have never used Tailwind CSS.",
            denied_concepts=[DENIED_COMPOUND], vault_corpus=corpus,
        )
    else:
        out, _ = assert_claimable_backed([dict(row)], _vault(), seam="lockstep-test")
    assert out[0]["evidence"] != DENIED_EVIDENCE
    assert out[0]["claimable"] is False
