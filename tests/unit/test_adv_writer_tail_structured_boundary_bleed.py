# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial pass on #415/#720 (ADR-072 amended 2026-09-17), follow-up fix.

**Original finding.** `cv_gap_hints.structured_section_texts` flattens every string field
of every item in a structured section (`languages`/`certifications`/`education`) into ONE
LIST with no per-item boundary marker; every caller then did `"\\n".join(...)` and
normalised through `ats_audit._norm`, which collapses EVERY run of whitespace — including
the joining newline — to a single space. So two UNRELATED certifications whose adjacent
field values happened to spell out a claimable concept across their item boundary
("…ISO" / "9001…") read as if the CV stated that concept in one place, and the ADR-072
clause 1 sole-carrier TIER (which now reads exactly this corpus, #415/#720's own change)
deleted the CV's only real narrative evidence for it — reproducing the 2026-09-11
production defect #720 was built to close, through a corpus-BUILDER artefact rather than
the corpus-SELECTION defect #720 fixed. Checked against the pre-amendment code path
(`load_bearing.stringify_draft`, which has the IDENTICAL flatten-then-join shape): the same
wrong deletion already happened there too, so this was a PRE-EXISTING defect, not something
#415 introduced — #415 only made it proportionally larger (closing the skills-tag vector
leaves this one as a bigger share of what still decides "covered elsewhere").

**Fix (this follow-up, main-session triage 2026-09-17): a shared join primitive,
`ats_audit.join_corpus_fragments`.** `structured_section_texts`'s and
`keyword_ledger._tailored_narrative_texts`'s return SHAPE is unchanged (still a plain list
of raw, un-normalised fragment strings) — the fix lives at the JOIN, not the list-returning
helpers, per ADR-066 ("one implementation per capability"): every call site that used to
hand-roll `"\\n".join(...)` for a MATCHING corpus now calls the one shared join, which
inserts `U+241E SYMBOL FOR RECORD SEPARATOR` between fragments — a character that survives
`_norm`'s NFKC pass unchanged, is never matched by `\\s` (so the whitespace collapse cannot
erase it), and cannot appear inside any real surface form (neither `surface_present`'s
direct substring check nor its loose, extra-space-tolerant fallback can bridge it — the
loose match only ever inserts SPACES between the needle's own characters).

**Join-site table (the positive set — every caller of `structured_section_texts` or
`stringify_draft`, plus every sibling flatten-join found via
`grep -rn '"\\\\n".join' backend/applire/services/`, triaged):**

| # | site (file:function) | corpus it builds | fixed? |
|---|---|---|---|
| 1 | `cv_gap_hints.py::_structured_norm` | `verified_narrative_underclaim`'s structured half | YES — seam A |
| 2 | `cv_gap_hints.py::verified_narrative_underclaim` (`narrative_norm`) | same check's narrative half | YES — seam B |
| 3 | `cv_budget.py::condense_to_budget` (`narrative_external`) | clause-4 demand-exemption narrative test | YES — seam E |
| 4 | `cv_budget.py::condense_to_budget` (`evidence_external`) | clause-1 sole-carrier TIER | YES — seam F |
| 5 | `cv.py::_restore_ledger_bullets._narrative_external_text` | clause-4 demand-exemption narrative test | YES — seam C |
| 6 | `cv.py::_restore_ledger_bullets._evidence_external_text` | clause-1 sole-carrier TIER | YES — seam D |
| — | `cv_gap_hints.py::verified_narrative_underclaim` (`document_norm`, `_draft_strings`) | whole-doc `tag_only` metadata, informational only, never gates a deletion | NOT fixed — out of scope (see below) |
| — | `cv.py::_restore_ledger_bullets._external_text` (via `load_bearing.stringify_draft`) | clause-4 exemption's OWN whole-document absence test | NOT fixed — out of scope |
| — | `cv_budget.py::condense_to_budget` (`external`, via `stringify_draft`) | same as above | NOT fixed — out of scope |
| — | `keyword_ledger.py::_coverage_split` (×2, `text_norm`) | `verified_missing_claimable`'s own whole-document demand-raising scan | NOT fixed — out of scope |
| — | `load_bearing.py::stringify_draft` itself | figure extraction (`retained_load_bearing_figures`), reviewer issue-soundness (`reviewer.py`) | NOT fixed — out of scope, unrelated capability |
| — | every other `"\\n".join` in `backend/applire/services/` (prompt rendering, PDF text extraction, log lines) | none feed `_norm`/`surface_present` matching | not applicable |

**Scope decision, stated so it is not mistaken for an oversight.** The main-session triage
scoped this fix to "the tier [that] now reads the structured sections on purpose" — ADR-072
clause 1's EVIDENCE corpus (sites 1/2/5/6) and its sibling clause-4 narrative-provenance test
(sites 3/5, which share `_narrative_external_text`/`narrative_external`'s output). The WIDER,
pre-existing `external_text`/`stringify_draft`-built whole-document corpus (clause 4's OWN
verified-coverage absence test, and `verified_missing_claimable`'s demand-raising scan) is
DELIBERATELY left unfixed here: it predates #415, #415 does not touch it, and
`stringify_draft` is shared by figure extraction and reviewer-soundness measurement outside
the CV writer tail — widening this fix to it is a separate, larger change with its own
verification cost. `test_the_pre_existing_external_text_path_is_intentionally_still_exposed`
below keeps this documented and pinned, not silently dropped.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.ats_audit import _norm, join_corpus_fragments, surface_present  # noqa: E402
from applire.services.bullet_cuts import demanded_exempt_indices, rank_cuts  # noqa: E402
from applire.services.cv import _restore_ledger_bullets  # noqa: E402
from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget, condense_to_budget  # noqa: E402
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


def _doc(bullets, **kw):
    base = {
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
    }
    base.update(kw)
    return TailoredCVData.model_validate(base)


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


# ── the shared primitive ──────────────────────────────────────────────────────────────


def test_join_corpus_fragments_blocks_cross_fragment_matches():
    """`join_corpus_fragments` is what the fix hangs on: prove its boundary survives
    `_norm` and defeats BOTH `surface_present`'s direct substring check and its "loose"
    extra-space-tolerant fallback (`ats_audit._find`), while a same-fragment match still
    works normally (the fix must not become a false negative machine)."""
    bled = join_corpus_fragments(["…endet mit ISO", "9001 beginnt…"])
    assert not surface_present("ISO 9001", _norm(bled)), (
        "the boundary must block the direct substring check"
    )
    # `_find`'s loose fallback only ever inserts SPACES between the needle's own
    # characters — never an arbitrary character — so a non-space boundary defeats it too.
    same_fragment = join_corpus_fragments(["Wir arbeiten nach ISO 9001 und Six Sigma."])
    assert surface_present("ISO 9001", _norm(same_fragment)), (
        "a genuine WITHIN-fragment match must still work — this is not a false-negative fix"
    )


def test_structured_section_texts_shape_is_unchanged():
    """The list-returning helper itself was deliberately NOT changed (ADR-066: the fix
    lives at the join, once, not duplicated into every list-builder)."""
    doc = _doc(["placeholder"]).model_dump(mode="json")
    texts = structured_section_texts(doc)
    assert texts == [CERT_A_NAME, "", "", "", CERT_B_NAME, "", "", ""], (
        "structured_section_texts must keep returning RAW, un-joined, un-marked fragments"
    )


# ── seam A / B: `cv_gap_hints.verified_narrative_underclaim` (sites 1 and 2) ──────────


def test_seam_a_structured_norm_no_longer_reads_the_boundary_as_coverage():
    """Site 1 (`_structured_norm`). Structured-section-only bleed, no narrative bullet
    anywhere states the concept either — isolates the STRUCTURED half of the check."""
    doc = _doc(["placeholder"]).model_dump(mode="json")
    missing = verified_narrative_underclaim(doc, _ledger(), structured_document=doc)
    assert any(m.concept == "ISO 9001" for m in missing), (
        "the check must report 'ISO 9001' as genuinely missing — the cross-certification "
        "boundary must not manufacture coverage"
    )


def test_seam_b_narrative_norm_no_longer_reads_bullet_boundaries_as_coverage():
    """Site 2 (`narrative_norm`). Two DIFFERENT narrative bullets whose boundary spells a
    concept neither states alone, zero structured content — isolates the NARRATIVE half."""
    b1 = "Qualifizierung im Bereich Six"
    b2 = "Sigma-Symbolik in der Statistikvorlesung behandelt."
    doc = _doc([b1, b2], certifications=[], languages=[], education=[]).model_dump(mode="json")
    ledger = [{
        "concept": "Six Sigma", "surface_forms": ["Six Sigma"], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "x",
    }]
    missing = verified_narrative_underclaim(doc, ledger, structured_document=doc)
    assert any(m.concept == "Six Sigma" for m in missing), (
        "two bullets whose text boundary spells 'Six Sigma' must not read as narrative "
        "evidence for it — neither bullet states it"
    )


# ── seam C / D: `cv._restore_ledger_bullets` (sites 5 and 6) ──────────────────────────


def test_seam_c_cv_narrative_external_text_protects_the_demand_exemption():
    """Site 5 (`_narrative_external_text`), isolated from site 6 by construction: zero
    structured content, so `_evidence_external_text`'s own join (site 6) wraps a SINGLE
    fragment and cannot itself hide a site-5 defect. Two roles: role `w2`'s bullets bleed
    "Six Sigma" across THEIR OWN boundary (role `w2` is untouched, not even being ranked);
    role `w1` carries the ONE real "Six Sigma" bullet and must keep it via the ADR-072
    clause-4 demand exemption despite that unrelated bleed."""
    six_sigma_bullet = "Six-Sigma-Projekt zur Ausschussreduktion geleitet."
    w1_bullets = [six_sigma_bullet, FIGS[0], FIGS[1]]
    w2_bullets = ["Qualifizierung im Bereich Six", "Sigma-Symbolik behandelt."]
    doc = TailoredCVData.model_validate({
        "contact": {"name": "Stefan Brandt"}, "summary": "x", "skills": [],
        "languages": [], "education": [], "certifications": [],
        "work_history": [
            {"id": "w1", "company": "Weberit", "role": "PL", "start_date": "2019-01",
             "end_date": None, "bullets": w1_bullets, "projects": []},
            {"id": "w2", "company": "Alte Firma", "role": "SL", "start_date": "2010-01",
             "end_date": "2019-01", "bullets": w2_bullets, "projects": []},
        ],
    })
    profile = {
        "work_experience": [
            {"id": "w1", "company": "Weberit", "role": "PL", "start_date": "2019-01",
             "end_date": None, "is_current": True, "responsibilities": w1_bullets,
             "achievements": []},
            {"id": "w2", "company": "Alte Firma", "role": "SL", "start_date": "2010-01",
             "end_date": "2019-01", "is_current": False, "responsibilities": w2_bullets,
             "achievements": []},
        ],
        "projects": [],
    }
    ledger = [{
        "concept": "Six Sigma", "surface_forms": ["Six Sigma"], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "x",
    }]
    budget = BudgetResult(
        roles={
            "w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=2),
            "w2": RoleBudget(work_entry_id="w2", tier="mid", max_bullets=2),
        },
        tiers={"top": BulletTier("top", 2, 1), "mid": BulletTier("mid", 2, 1)},
        target_pages=2, region="DACH",
        claimable_forms=("Six Sigma",), claimable_concepts=(("Six Sigma",),),
        demanded_concepts=(("Six Sigma",),),
    )
    out = _restore_ledger_bullets(doc, profile, ledger, budget)
    kept_w1 = out.work_history[0].bullets
    assert six_sigma_bullet in kept_w1, (
        "the demanded bullet must be exempted from the cap; an unrelated cross-item "
        "bleed in a DIFFERENT, untouched role must not withhold the exemption"
    )


def test_seam_d_cv_evidence_external_text_protects_the_sole_carrier_tier():
    """Site 6 (`_evidence_external_text`) — the original finding, now asserting the FIX
    holds: the cap must KEEP the CV's only real ISO-9001 evidence despite the
    cross-certification boundary bleed."""
    budget = BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=3)},
        tiers={"top": BulletTier("top", 3, 2)}, target_pages=2, region="DACH",
        claimable_forms=("ISO 9001",), claimable_concepts=(("ISO 9001",),),
    )
    bullets = FIGS + [ISO_BULLET]
    out = _restore_ledger_bullets(_doc(bullets), _profile(bullets), _ledger(), budget)
    kept = out.work_history[0].bullets
    assert ISO_BULLET in kept, (
        "FIXED: the cap must no longer delete the CV's only narrative evidence for "
        "'ISO 9001' because of the cross-certification boundary artefact"
    )


# ── seam E / F: `cv_budget.condense_to_budget` (sites 3 and 4) ────────────────────────


def test_seam_e_cv_budget_narrative_external_protects_the_demand_exemption():
    """Site 3 (`narrative_external` in `condense_to_budget`), isolated the same way as
    seam C: zero structured content, two roles, the bleed lives entirely in role `w2`
    (untouched, well within its own ceiling) while role `w1` must keep its one demanded
    "Six Sigma" bullet under a tight page-overrun ceiling."""
    six_sigma_bullet = "Six-Sigma-Projekt zur Ausschussreduktion geleitet."
    w1_bullets = [six_sigma_bullet, FIGS[0], FIGS[1]]
    w2_bullets = ["Qualifizierung im Bereich Six", "Sigma-Symbolik behandelt."]
    data = TailoredCVData.model_validate({
        "contact": {"name": "Stefan Brandt"}, "summary": "x", "skills": [],
        "languages": [], "education": [], "certifications": [],
        "work_history": [
            {"id": "w1", "company": "Weberit", "role": "PL", "start_date": "2019-01",
             "end_date": None, "bullets": w1_bullets, "projects": []},
            {"id": "w2", "company": "Alte Firma", "role": "SL", "start_date": "2010-01",
             "end_date": "2019-01", "bullets": w2_bullets, "projects": []},
        ],
    }).model_dump(mode="json")
    budget = BudgetResult(
        roles={
            "w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=2),
            "w2": RoleBudget(work_entry_id="w2", tier="mid", max_bullets=2),
        },
        tiers={"top": BulletTier("top", 2, 1), "mid": BulletTier("mid", 2, 1)},
        target_pages=2, region="DACH",
        claimable_forms=("Six Sigma",), claimable_concepts=(("Six Sigma",),),
        demanded_concepts=(("Six Sigma",),),
    )
    out, _changed = condense_to_budget(data, budget, iteration=1)
    kept_w1 = out["work_history"][0]["bullets"]
    assert six_sigma_bullet in kept_w1, (
        "the demanded bullet must be exempted from condense_to_budget's cap; the "
        "unrelated bleed in role w2 must not withhold the exemption"
    )


def test_seam_f_cv_budget_evidence_external_protects_the_sole_carrier_tier():
    """Site 4 (`evidence_external` in `condense_to_budget`) — the page-overrun path's own
    copy of the original finding."""
    data = _doc(FIGS + [ISO_BULLET]).model_dump(mode="json")
    budget = BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=3)},
        tiers={"top": BulletTier("top", 3, 2)}, target_pages=2, region="DACH",
        claimable_forms=("ISO 9001",), claimable_concepts=(("ISO 9001",),),
    )
    out, changed = condense_to_budget(data, budget, iteration=1)
    assert changed
    kept = out["work_history"][0]["bullets"]
    assert ISO_BULLET in kept, (
        "FIXED: condense_to_budget must no longer delete the CV's only ISO-9001 evidence "
        "because of the cross-certification boundary artefact"
    )


# ── mechanism-level regression guard (mirrors seam C/E's logic without the full pipeline) ──


def test_demanded_exempt_indices_direct_mechanism_check():
    """The exact mechanism seams C and E exercise end to end, pinned directly against
    `bullet_cuts.demanded_exempt_indices` so a future refactor of the pipeline plumbing
    cannot silently reopen it without a low-level test noticing first."""
    six_sigma_bullet = "Six-Sigma-Projekt zur Ausschussreduktion geleitet."
    texts = [six_sigma_bullet, "Ausschussquote von 4,1 % auf 2,3 % gesenkt."]
    other_1, other_2 = "Qualifizierung im Bereich Six", "Sigma-Symbolik behandelt."
    exempt = demanded_exempt_indices(
        texts, concept_groups=(("Six Sigma",),), demanded_groups=(("Six Sigma",),),
        narrative_external_text=join_corpus_fragments([other_1, other_2]),
    )
    assert exempt == {0}, "the real bullet must be exempted despite the unrelated bleed"


# ── documented scope boundary: NOT fixed here ──────────────────────────────────────────


def test_the_pre_existing_external_text_path_is_intentionally_still_exposed():
    """`load_bearing.stringify_draft`-built `external_text` (clause 4's OWN whole-document
    absence test, `cv._external_text` / `cv_budget`'s `external`) is DELIBERATELY left
    unfixed by this follow-up — it predates #415, #415 does not touch it, and
    `stringify_draft` is shared by figure extraction and reviewer-soundness measurement
    outside the writer tail (a separate, larger change). Pinned here so the gap is a
    documented scope decision, not a silent regression nobody is tracking."""
    doc = {
        "contact": {"name": "Stefan Brandt"}, "summary": "x", "skills": [],
        "languages": [], "education": [], "certifications": CERTIFICATIONS,
        "work_history": [],  # the role's own bullets excluded, as `cv._external_text` does
    }
    external_text = stringify_draft(doc)
    corpus_norm = _norm(external_text)
    assert surface_present("ISO 9001", corpus_norm), (
        "still bleeds, on purpose left unfixed — see the module docstring's scope table"
    )

    bullets = FIGS + [ISO_BULLET]
    tiers = [(True, -0), (True, -1), (True, -2), (False, -3)]
    cuts = rank_cuts(
        bullets, tiers, keep=3, concept_groups=(("ISO 9001",),),
        external_text=external_text,  # evidence_external_text omitted -> pre-#415 shape
    )
    assert ISO_BULLET in [c.text for c in cuts], (
        "the pre-amendment code path (external_text alone) still cuts the CV's only real "
        "ISO-9001 evidence on this fixture — documented, out-of-scope residual"
    )
