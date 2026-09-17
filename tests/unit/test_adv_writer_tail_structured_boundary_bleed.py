# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial pass on #415/#720 (ADR-072 amended 2026-09-17, WP-adv): the sole-carrier
TIER's new EVIDENCE corpus is built by joining ``cv_gap_hints.structured_section_texts``'
flat per-field-value list with ``"\\n".join(...)`` and then normalising it
(``ats_audit._norm`` collapses every run of whitespace, including newlines, to ONE
space). ``structured_section_texts`` itself flattens every string field of every item in
a structured section (``languages``/``certifications``/``education``) into one list with
no per-item boundary marker that survives that collapse.

**Consequence, proven below against the REAL functions (no provider calls):** two
UNRELATED certifications whose adjacent field values happen to spell out a claimable
concept across the item boundary ("… ISO" / "9001 …") read as if the CV states that
concept, exactly as if it had written "ISO 9001" in one place. This is the SAME class of
false coverage the whole 2026-09-17 amendment was built to close (a bare tag reading as
evidence) — here it survives the amendment because the corpus BUILDER, not the corpus
SELECTION, is what is fooled.

**Corrected finding (checked against the pre-amendment code path, not assumed):** this is
NOT a new hole #415 opened. ``load_bearing.stringify_draft`` — the function that already
built the PRE-#415 ``external_text`` (the only sole-carrier corpus that existed before this
amendment) — has the IDENTICAL "flatten every leaf string, join with a bare newline, let
``_norm`` collapse the join to nothing" shape. The third test below reconstructs the
pre-amendment call (``rank_cuts`` with only ``external_text=stringify_draft(...)``, no
``evidence_external_text`` — the documented byte-identical legacy contract) on the SAME
fixture and shows the SAME wrong deletion already happens there. So this class of harm —
silently deleting a CV's only real evidence because of a text-flattening artefact — already
shipped in production before 2026-09-17, via ``external_text``; it is not introduced by
#415 and #415 does not widen the SET of documents it can occur on (the same certifications
would have tripped it either way).

**What #415 changes is the SHAPE of the exposure, not its existence.** Before this
amendment, a skills tag ALSO satisfied whole-document "covered" — so on any document where
a skills tag already covered a concept, the boundary-bleed was moot (the bullet was already
going to read as non-sole either way). #415 closes exactly that skills-tag vector, which
means the STRUCTURED-SECTION flattening artefact — inherited unchanged — is now
proportionally a larger share of what still decides "covered elsewhere" once a concept is
NOT a skills tag. #415 is also the point where the ADR text starts asserting this corpus
"is what the delivered `narrative-evidence` check grades" and treats agreement between the
cap and the check as newly established; this test shows the agreement is real (the check is
fooled the same way, by the same shared function) but the joint false-positive survives
unfixed under the new framing.

Severity: real, reproducible, pre-existing defect (not an equivalent mutant — no shipped
fixture, before or after #415, populates two items in the same structured section, so
nothing pins this boundary either way). User impact: silent evidence loss on the delivered
CV, the exact harm class #415 exists to prevent — just not a #415 regression. Likelihood:
narrow — it needs an item boundary whose last non-empty field value and the next item's
first field value happen to complete a claimable multi-token concept between them, which is
coincidental for arbitrary CV data but not exotic (certification names are free text the
candidate/vault controls, and multi-item certifications/education
lists are common on senior CVs).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.ats_audit import _norm, surface_present  # noqa: E402
from applire.services.bullet_cuts import rank_cuts  # noqa: E402
from applire.services.cv import _restore_ledger_bullets  # noqa: E402
from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget  # noqa: E402
from applire.services.cv_gap_hints import (  # noqa: E402
    structured_section_texts,
    verified_narrative_underclaim,
)
from applire.services.load_bearing import stringify_draft  # noqa: E402

#: Two INDEPENDENT, individually-unremarkable certifications. Neither name alone states
#: "ISO 9001" — the concept only appears if the flattener runs them together.
CERT_A_NAME = "Fachkraft für Qualität ISO"
CERT_B_NAME = "9001 Grundschulung"
CERTIFICATIONS = [
    {"name": CERT_A_NAME, "issuing_organization": "", "date_obtained": "", "expiry_date": ""},
    {"name": CERT_B_NAME, "issuing_organization": "", "date_obtained": "", "expiry_date": ""},
]

#: The CV's only genuine narrative evidence for "ISO 9001" — figure-less, so it is the
#: first candidate the ranking would cut once (falsely) treated as covered elsewhere.
ISO_BULLET = "Interne ISO-9001-Schulungen für das Team durchgeführt."
FIGS = [
    "Ausschussquote von 4,1 % auf 2,3 % gesenkt.",
    "Termintreue von 87 % auf 96 % verbessert.",
    "Unfallquote von 8,2 auf 3,1 gesenkt.",
]


def _doc(bullets):
    return TailoredCVData.model_validate({
        "contact": {"name": "Stefan Brandt"},
        "summary": "Produktionsleiter.",
        "skills": [],
        "languages": [],
        "education": [],
        "certifications": CERTIFICATIONS,
        "work_history": [{
            "id": "w1", "company": "Weberit", "role": "Produktionsleiter",
            "start_date": "2019-01", "end_date": None,
            "bullets": list(bullets), "projects": [],
        }],
    })


def _profile(responsibilities):
    return {
        "work_experience": [{
            "id": "w1", "company": "Weberit", "role": "Produktionsleiter",
            "start_date": "2019-01", "end_date": None, "is_current": True,
            "responsibilities": list(responsibilities), "achievements": [],
        }],
        "projects": [],
    }


def _ledger():
    return [{
        "concept": "ISO 9001", "surface_forms": ["ISO 9001"], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0,
        "evidence": "ISO 9001 Kenntnisse",
    }]


def test_the_corpus_builder_spells_out_a_concept_across_an_item_boundary():
    """Root cause, isolated: `structured_section_texts` flattens every field of every
    item with no separator that survives `_norm`'s whitespace collapse, so two
    UNRELATED certifications read as one concept neither states alone."""
    doc = _doc(["placeholder"]).model_dump(mode="json")
    texts = structured_section_texts(doc)
    assert CERT_A_NAME in texts and CERT_B_NAME in texts, "fixture sanity"

    corpus_norm = _norm("\n".join(texts))
    assert surface_present("ISO 9001", corpus_norm), (
        "EXPECTED (documenting the defect): the flattener's cross-item join spells out "
        "'ISO 9001' even though no single certification states it. If this now fails, "
        "the corpus builder has been given a per-item boundary marker and this guard "
        "should be rewritten as a NEGATIVE assertion (the concept must NOT appear)."
    )

    # The same false reading already reaches the CORRECTOR-facing signal — #415 did not
    # create this half, it inherited it (RULING W1-3 / build 2, 2026-09-09).
    missing = verified_narrative_underclaim(doc, _ledger(), structured_document=doc)
    assert not any(m.concept == "ISO 9001" for m in missing), (
        "the pre-existing signal is ALSO fooled by the same boundary artefact"
    )


def test_the_cap_deletes_the_cvs_only_real_evidence_because_of_the_boundary_artefact():
    """The SAME false reading drives a DELETION, through the real
    `_restore_ledger_bullets` ceiling enforcer (the same one the 2026-09-11 production
    defect this PR fixes ran through, and the same one `test_415_enforcer_seams.py`
    seam-tests) — no shipped fixture populates TWO items in the same structured section,
    so no shipped test exercises the cross-item boundary this fixture forces, on either
    side of the amendment (see the next test)."""
    budget = BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=3)},
        tiers={"top": BulletTier("top", 3, 2)}, target_pages=2, region="DACH",
        claimable_forms=("ISO 9001",), claimable_concepts=(("ISO 9001",),),
    )
    bullets = FIGS + [ISO_BULLET]
    out = _restore_ledger_bullets(_doc(bullets), _profile(bullets), _ledger(), budget)
    kept = out.work_history[0].bullets

    assert ISO_BULLET in kept, (
        "REGRESSION-CLASS DEFECT (documents a real, surviving defect — not an equivalent "
        "mutant, and NOT a #415-introduced regression, see the next test): the cap "
        "deleted the CV's only narrative evidence for 'ISO 9001' because two unrelated "
        "certifications' flattened field values happened to spell the concept out across "
        "their item boundary. This reproduces the exact 2026-09-11 production shape (a "
        "bullet cut at what the code believes is sole_carrier=False, followed by a "
        "delivered `narrative-evidence: fail` on the same concept) through the "
        "structured-section corpus this PR adds to the tier."
    )


def test_the_same_boundary_bleed_already_existed_pre_amendment_via_stringify_draft():
    """Checked, not assumed: does `rank_cuts` with ONLY the pre-#415 `external_text`
    (no `evidence_external_text` — the documented byte-identical legacy contract, see
    `test_omitting_the_evidence_corpus_reproduces_the_pre_amendment_ranking` in
    `test_415_cut_reads_the_evidence_corpus.py`) ALREADY make the same wrong deletion on
    this exact fixture?

    Yes. `load_bearing.stringify_draft` — the function that built `external_text` before
    #415 existed, and still builds it today (`cv._external_text`) — has the IDENTICAL
    "flatten every leaf string, join with a bare '\\n', let `_norm` collapse the join"
    shape as `structured_section_texts`. So this class of harm (silently deleting a CV's
    only real evidence because of a text-flattening artefact) was ALREADY live in
    production before 2026-09-17; #415 does not introduce it and does not widen the set of
    documents it can occur on. What #415 changes is proportion, not existence: closing the
    skills-tag false-coverage vector makes this untouched, inherited one a larger share of
    what remains fooled once a concept is not a bare skills tag.
    """
    doc = {
        "contact": {"name": "Stefan Brandt"}, "summary": "x", "skills": [],
        "languages": [], "education": [], "certifications": CERTIFICATIONS,
        "work_history": [],  # the role's own bullets excluded, as `cv._external_text` does
    }
    external_text = stringify_draft(doc)
    corpus_norm = _norm(external_text)
    assert surface_present("ISO 9001", corpus_norm), (
        "fixture sanity: stringify_draft's OWN flattening reproduces the same bleed"
    )

    bullets = FIGS + [ISO_BULLET]
    tiers = [(True, -0), (True, -1), (True, -2), (False, -3)]
    cuts = rank_cuts(
        bullets, tiers, keep=3, concept_groups=(("ISO 9001",),),
        external_text=external_text,  # evidence_external_text omitted -> pre-#415 shape
    )
    assert ISO_BULLET in [c.text for c in cuts], (
        "the pre-amendment code path (external_text alone) ALREADY cuts the CV's only "
        "real ISO-9001 evidence on this fixture — proving the defect predates #415 "
        "rather than assuming it from the shared function's docstring"
    )
