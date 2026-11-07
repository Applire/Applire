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

from applire.services.profile.reconcile.stance import denial_release_corpus
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
    """#480 step 1, re-read under G-1 (#821, ADR-059 amended 2026-11-07): a
    denial of the compound no longer floors the head noun at all, so there is
    no release left for an ``unconfirmed`` inference to perform. What still
    holds: the head noun is never ASSERTED as the candidate's "no", and an
    ``unconfirmed`` entry still backs nothing at the persist heal (ADR-061 cl. 3)."""
    entry = _by_concept(_rebuild(_vault(css_status="unconfirmed")))[HEAD_NOUN]
    assert entry["status"] != "denied" and entry["evidence"] != DENIED_EVIDENCE
    assert entry["evidence"] != DENIAL_FLOOR_EVIDENCE


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
    """The #486 pin under G-1 (#821): ``Tailwind CSS`` — the candidate's own
    declared term — keeps ``denied`` + :data:`DENIED_EVIDENCE`; ``CSS`` —
    reached only by containment — keeps the classifier's grade. A denial
    floors only the concept it names."""
    ledger = _by_concept(_rebuild(_vault(), concepts=(DENIED_COMPOUND, HEAD_NOUN)))

    declared = ledger[DENIED_COMPOUND]
    assert declared["status"] == "denied"
    assert declared["claimable"] is False
    assert declared["evidence"] == DENIED_EVIDENCE
    assert declared["denial_level"] == "direct"
    assert declared["denied_by"] == DENIED_COMPOUND

    contained = ledger[HEAD_NOUN]
    assert contained["status"] == "direct"
    assert contained["evidence"] == "the classifier's own adjacency read"
    assert "denial_level" not in contained and "denied_by" not in contained


def test_upgrade_seam_floors_a_containment_only_match_without_asserting():
    """G-1 at the turn seam (#821): a pure-denial turn of the compound neither
    floors the head noun nor upgrades it on the denial's words (#351)."""
    row = _entry(HEAD_NOUN)
    out, changed = upgrade_ledger_for_concepts(
        [row],
        [HEAD_NOUN],
        "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0] == row
    assert changed is False


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
    """#352's reversal stays a DECLARED-denial act; since G-1 (#821) a
    containment-only match reverses nothing at all."""
    row = _entry(HEAD_NOUN, status="direct", claimable=True, evidence="an earlier turn")
    out, changed = upgrade_ledger_for_concepts(
        [row],
        [HEAD_NOUN],
        "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0] == row and changed is False


# ── the never-upgrade half, pinned in all three floor call paths ────────────


def test_never_upgrade_half_still_fires_by_containment_in_all_three_paths():
    """ADR-064's consistency rule at its true width, re-pinned for G-1 (#821):
    NO floor path floors on containment any more, and all three agree.

    1 the rebuild keeps the classifier's grade; 2 the turn seam leaves the row
    as it stands (the denial's words are no evidence for it, #351); 3 the vault
    re-evaluation lifts only on real claimable evidence — the vault's own
    copy of the denied compound is a ``denied`` skill and backs nothing."""
    from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

    profile = _vault()

    # 1 — the rebuild floor.
    assert _by_concept(_rebuild(profile))[HEAD_NOUN]["status"] == "direct"

    # 2 — the in-place upgrade seam.
    out, changed = upgrade_ledger_for_concepts(
        [_entry(HEAD_NOUN)], [HEAD_NOUN], "I have never used Tailwind CSS.",
        denied_concepts=[DENIED_COMPOUND],
    )
    assert out[0]["claimable"] is False and changed is False

    # 3 — the vault re-evaluation: the denied compound, persisted as the
    # `denied` skill the commit re-floor writes, is no evidence for anything.
    css_in_vault = dict(profile)
    css_in_vault["skills"] = profile["skills"] + [
        {"name": "Tailwind CSS", "category": "technical", "status": "denied"}
    ]
    healed, changed = reevaluate_gap_ledger_against_vault(
        [_entry(HEAD_NOUN)], css_in_vault
    )
    assert healed[0]["claimable"] is False
    assert healed[0]["status"] != "denied"


def test_the_f8_adjacency_inference_is_still_floored():
    """The 2026-07-23 blind-run shape ("RAG experience typically involves
    embeddings"), re-pinned for G-1 (#821): the denial floor no longer refuses
    "RAG" for a denied "RAG pipeline" — the BACKING floor does, because no vault
    unit grounds an adjacency read (ADR-061 / ADR-100). Never asserted as the
    candidate's "no"."""
    profile = {
        "personal_info": {"name": "Test Person"},
        "work_experience": [
            {"id": "w-1", "company": "Beispiel AG", "role": "ML Engineer",
             "start_date": "2020-01", "is_current": True,
             "responsibilities": ["Built document search for the support team."],
             "achievements": [], "technologies": ["Python"]}
        ],
        "skills": [],
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
    healed, violations = assert_claimable_backed(ledger, profile, seam="f8-test")
    rag = _by_concept(healed)["RAG"]
    assert rag["claimable"] is False
    assert rag["evidence"] != DENIED_EVIDENCE  # the candidate never named "RAG"
    assert [v["reason"] for v in violations] == ["no_vault_evidence_unit"]


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
    written for a term the candidate actually declared. Since G-1 (#821) the
    floor and the turn seam leave the undeclared term untouched; the heal may
    still refuse it for want of vault evidence, never as a denial."""
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
    assert out[0]["status"] != "denied"
    if writer != "heal":
        assert out[0] == row


# ── SF-GAP.10 / E-4 — the RELEASE half at the entry's full width ────────────
#
# `_entry_is_denied` probes concept AND every surface form; the release half
# probed the concept alone. Asymmetric, and always failing the same way: every
# alias could pull an entry INTO the floor, none could carry it out.
#
# The 2026-09-10 delivery run (`build-1/delivery-run/artifacts/20-gaps-refresh
# .json`): the candidate denied "direkte Produktion für Lebensmittelkunden";
# the JD's bare requirement `Produktion` is a whole word inside that compound,
# so containment fired, and the German compound `produktionsleiter` — an
# attested work_experience[].role — is not the token `produktion` at a word
# boundary, so nothing released it. A met, `direct`, narrative-backed
# requirement shipped as a critical gap in `category_c` while the same response
# still listed it under `strengths`.
#
# Reconcile rule 9's principle, restored: A DENIAL IS ABOUT ITS OWN ITEM.

NARROW_DENIAL = "direkte Produktion für Lebensmittelkunden"
BARE_CONCEPT = "Produktion"
ATTESTED_FORM = "Produktionsleiter"


def _production_vault(*, role=ATTESTED_FORM, denial=NARROW_DENIAL):
    return {
        "skills": [{"name": "Shopfloor-Management", "category": "technical"}],
        "work_experience": [
            {
                "role": role,
                "company": "Weberit Kunststofftechnik GmbH",
                "technologies": [],
                "responsibilities": ["Verantwortung für zwei Fertigungsbereiche."],
            }
        ],
        "metadata": {"denied_concepts": [_denial(denial, "direct")]},
    }


def _production_row(**kw):
    return _entry(
        BARE_CONCEPT,
        status="direct",
        claimable=True,
        evidence="14 Jahre Erfahrung in der diskreten Fertigung.",
        surface_forms=[BARE_CONCEPT, ATTESTED_FORM, "Fertigung"],
        **kw,
    )


def test_an_attested_surface_form_releases_a_containment_only_floor():
    """The E-4 instance. The entry's own attested name (`Produktionsleiter`,
    a vault role) affirms it outside the denied compound, so the narrow denial
    about producing FOR FOOD CUSTOMERS does not floor bare `Produktion`."""
    profile = _production_vault()
    out = _enforce_denial_stance(
        [_production_row()],
        profile["metadata"]["denied_concepts"],
        denial_release_corpus(profile),
    )[0]
    assert out["claimable"] is True
    assert out["status"] == "direct"
    assert out["evidence"] != DENIAL_FLOOR_EVIDENCE
    assert out["evidence"] != DENIED_EVIDENCE


def test_without_an_attested_form_the_containment_floor_still_fires():
    """G-1 (#821): with nothing attesting any of the entry's names, a narrow
    denial still floors nothing — "direkte Produktion für Lebensmittelkunden"
    does not name "Produktion". The row keeps its grade."""
    profile = _production_vault(role="Qualitätsmanager")
    row = _production_row()
    out = _enforce_denial_stance(
        [row],
        profile["metadata"]["denied_concepts"],
        denial_release_corpus(profile),
    )[0]
    assert out == row


def test_a_declared_denial_is_absolute_however_loudly_the_vault_attests_it():
    """ADR-040 never-claim-beats-claim. The release may only ever reach the
    CONTAINMENT-only case — a denial that NAMES the concept (or one of its
    surface forms) outranks every vault attestation of the same term."""
    profile = {
        "skills": [{"name": "Kubernetes", "category": "technical"}],
        "work_experience": [{"role": "Platform Engineer", "technologies": ["K8s"]}],
        "metadata": {"denied_concepts": [_denial("Kubernetes", "direct")]},
    }
    row = _entry(
        "Kubernetes",
        status="direct",
        claimable=True,
        evidence="ran clusters at Acme",
        surface_forms=["Kubernetes", "K8s"],
    )
    out = _enforce_denial_stance(
        [row], profile["metadata"]["denied_concepts"], denial_release_corpus(profile)
    )[0]
    assert out["claimable"] is False
    assert out["status"] == "denied"
    assert out["evidence"] == DENIED_EVIDENCE


def test_the_denied_compound_in_the_vault_never_releases_the_head_noun():
    """G-1 (#821): the head noun is no longer floored by the compound's denial,
    so there is nothing to release; it is never asserted as denied either."""
    profile = {
        "skills": [{"name": DENIED_COMPOUND, "category": "technical"}],
        "metadata": {"denied_concepts": [_denial(DENIED_COMPOUND, "direct")]},
    }
    row = _entry(
        HEAD_NOUN,
        status="direct",
        claimable=True,
        evidence="styled the app",
        surface_forms=[HEAD_NOUN, "Cascading Style Sheets"],
    )
    out = _enforce_denial_stance(
        [row], profile["metadata"]["denied_concepts"], denial_release_corpus(profile)
    )[0]
    assert out == row


# ── one seam test per call site (ADR-059's every-seam rule) ─────────────────


def test_seam_rebuild_releases_the_containment_only_floor():
    profile = _production_vault()
    ledger = build_keyword_ledger(
        classifications=[
            _cls(
                BARE_CONCEPT,
                "direct",
                [BARE_CONCEPT, ATTESTED_FORM, "Fertigung"],
                evidence="14 Jahre in der diskreten Fertigung.",
            )
        ],
        required_skills=[BARE_CONCEPT],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=profile["metadata"]["denied_concepts"],
        profile_json=profile,
    )
    row = _by_concept(ledger)[BARE_CONCEPT]
    assert row["claimable"] is True
    assert row["evidence"] != DENIAL_FLOOR_EVIDENCE


def test_seam_upgrade_leaves_a_released_entry_as_it_stands():
    profile = _production_vault()
    out, _changed = upgrade_ledger_for_concepts(
        [_production_row()],
        [BARE_CONCEPT],
        "Ich habe nie direkt für Lebensmittelkunden produziert.",
        denied_concepts=profile["metadata"]["denied_concepts"],
        vault_corpus=denial_release_corpus(profile),
    )
    assert out[0]["claimable"] is True
    assert out[0]["evidence"] != DENIAL_FLOOR_EVIDENCE


def test_seam_heal_does_not_call_a_released_entry_denied():
    profile = _production_vault()
    healed, violations = assert_claimable_backed(
        [_production_row()], profile, seam="sf-gap-10-test"
    )
    assert "denied_concept" not in violations
    assert healed[0]["evidence"] != DENIED_EVIDENCE
    assert healed[0]["evidence"] != DENIAL_FLOOR_EVIDENCE


def test_seam_vault_reevaluation_may_heal_a_released_gap_row():
    """Site 4: the corpus-aware re-evaluation skipped every containment match,
    so a released row could never heal from the vault either."""
    from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

    profile = _production_vault()
    profile["work_experience"][0]["responsibilities"] = [
        "Leitung der Produktion an zwei Standorten."
    ]
    row = _entry(
        BARE_CONCEPT,
        status="gap",
        claimable=False,
        evidence="",
        surface_forms=[BARE_CONCEPT, ATTESTED_FORM, "Fertigung"],
    )
    healed, changed = reevaluate_gap_ledger_against_vault([row], profile)
    assert changed is True
    assert healed[0]["claimable"] is True


def test_seam_vault_reevaluation_still_skips_an_unreleased_containment_match():
    """G-1 (#821): the re-evaluation skips only a row a denial NAMES; a row
    merely contained in "direkte Produktion für Lebensmittelkunden" is lifted
    on the vault bullet that names it (the re-evaluation's corpus excludes the
    denial statements themselves)."""
    from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

    profile = _production_vault(role="Qualitätsmanager")
    profile["work_experience"][0]["responsibilities"] = [
        "Leitung der Produktion an zwei Standorten."
    ]
    row = _entry(
        BARE_CONCEPT,
        status="gap",
        claimable=False,
        evidence="",
        surface_forms=[BARE_CONCEPT, ATTESTED_FORM, "Fertigung"],
    )
    healed, changed = reevaluate_gap_ledger_against_vault([row], profile)
    assert changed is True
    assert healed[0]["claimable"] is True
    # ...and a row the denial NAMES stays skipped however the vault reads.
    named = _entry("direkte Produktion für Lebensmittelkunden", status="gap")
    profile["work_experience"][0]["responsibilities"].append(
        "Direkte Produktion für Lebensmittelkunden geplant."
    )
    healed, changed = reevaluate_gap_ledger_against_vault([named], profile)
    assert changed is False and healed[0]["claimable"] is False
