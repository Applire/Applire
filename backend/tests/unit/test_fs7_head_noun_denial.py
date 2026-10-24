# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

"""FS-7 (ADR-059 amended 2026-10-24) — a denial phrased as term + generic head
noun DECLARES the bare term, so the candidate's own imported CV can no longer
release it as a "narrower compound".

Build-3 residue: "agile methodology experience" floored the row "agile
methodologies" until an imported bullet named the term, and then E-11's
containment release lifted it (787:L43 fixture shape; 7 of 54 distinct denials
in the E-11 label packet carry such a head noun).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault  # noqa: E402
from applire.services.profile.reconcile.stance import (  # noqa: E402
    containment_release_form,
    declared_denial_matches,
    declared_only_by_head_noun,
    denial_release_corpus,
    denial_release_units,
    head_noun_scope_conflicts,
    head_noun_term,
)


@pytest.mark.parametrize(
    "denial, bare",
    [
        ("payment-institution experience", "payment institution"),
        ("eigene Erfahrung mit Vertrieb", "vertrieb"),
        ("Erfahrung im Vertrieb", "vertrieb"),
        ("keine Kenntnisse in SAP", "sap"),
        ("audit-trail background", "audit trail"),
        ("agile methodology experience", "agile methodology"),
        ("regulated-environment experience", "regulated environment"),
        # A narrowing qualifier stays in the term — never bare "SBAR"/"Kubernetes".
        ("formal SBAR experience", "formal sbar"),
        ("hands-on Kubernetes experience", "hands on kubernetes"),
        ("praktische Erfahrung mit SAP", None),
        # Not a head-noun frame / a head noun alone.
        ("direkte Produktion für Lebensmittelkunden", None),
        ("Kafka transactions", None),
        ("experience", None),
        ("Erfahrung", None),
    ],
)
def test_the_closed_head_noun_list(denial, bare):
    assert head_noun_term(denial) == bare


def test_a_head_noun_denial_declares_the_bare_term():
    assert declared_denial_matches("Vertrieb", ["eigene Erfahrung mit Vertrieb"]) == [
        "eigene Erfahrung mit Vertrieb"
    ]
    assert declared_only_by_head_noun("Vertrieb", ["eigene Erfahrung mit Vertrieb"]) == [
        "eigene Erfahrung mit Vertrieb"
    ]
    # A literal denial of the term is not "only by head noun".
    assert declared_only_by_head_noun("Vertrieb", ["Vertrieb"]) == []


def test_the_cv_no_longer_releases_a_head_noun_denial():
    """The release predicate itself: a declared match is absolute."""
    corpus = "teamarbeit nach agile methodologies im projektalltag"
    assert (
        containment_release_form(
            "agile methodologies", ["agile methodologies"], ["agile methodology experience"], corpus
        )
        is None
    )
    # A narrowing qualifier keeps the old reading: the CV still releases bare SBAR.
    assert (
        containment_release_form("SBAR", ["SBAR"], ["formal SBAR experience"], "sbar handover in the icu")
        == "SBAR"
    )


def _vault(bullet: str, denial: str) -> dict:
    return {
        "work_experience": [{"id": "w1", "company": "Acme", "role": "PM", "responsibilities": [bullet]}],
        "metadata": {
            "denied_concepts": [{"concept": denial, "statement": "s", "source": "interview",
                                 "date": "2026-10-01"}],
            "enrichment_history": [{
                "source": "cv_upload", "timestamp": "2026-09-01T00:00:00Z",
                "changes": [{"section": "work_experience", "field": "responsibilities",
                             "action": "merged", "old_value": None, "new_value": [bullet]}],
            }],
        },
    }


def test_seam_the_vault_recheck_keeps_the_bare_term_floored_against_an_imported_bullet():
    """Seam (ledger re-evaluation, the E-11 release path): the 787:L43 shape
    with the bullet from the IMPORTED CV — before FS-7 this released."""
    row = {"concept": "agile methodologies", "status": "gap", "claimable": False,
           "surface_forms": ["agile methodologies"], "evidence": "", "fit_weight": 1.0,
           "sources": ["required"]}
    vault = _vault("Teamarbeit nach agile methodologies im Projektalltag", "agile methodology experience")
    out, _changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert out[0]["claimable"] is False


def test_seam_a_narrowing_qualifier_still_lets_the_imported_cv_release():
    row = {"concept": "SBAR", "status": "gap", "claimable": False,
           "surface_forms": ["SBAR"], "evidence": "", "fit_weight": 1.0, "sources": ["required"]}
    vault = _vault("Structured SBAR handover on every shift change", "formal SBAR experience")
    out, _changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert out[0]["claimable"] is True


# --- FS-7 Health item detector (the contradiction the founder asked to surface) ---


def _hv(denials, *, bullets=(), skills=()):
    return {
        "skills": [{"name": n, "status": "confirmed"} for n in skills],
        "work_experience": [
            {"role": "Account Manager", "company": "Acme GmbH", "responsibilities": list(bullets)}
        ],
        "metadata": {"denied_concepts": [{"concept": d} for d in denials]},
    }


def test_conflict_when_the_imported_cv_names_the_bare_term():
    v = _hv(
        ["eigene Erfahrung mit Vertrieb"],
        bullets=["Vertrieb von Verpackungslösungen an Lebensmittelkunden."],
    )
    assert head_noun_scope_conflicts(v) == [
        {
            "denial_concept": "eigene Erfahrung mit Vertrieb",
            "bare_term": "vertrieb",
            "cv_label": "Vertrieb von Verpackungslösungen an Lebensmittelkunden.",
            "denial_date": "",
        }
    ]


def test_conflict_from_an_attested_skill_label():
    v = _hv(["payment-institution experience"], skills=["Payment institution onboarding"])
    (hit,) = head_noun_scope_conflicts(v)
    assert hit["bare_term"] == "payment institution"
    assert hit["cv_label"] == "Payment institution onboarding"


def test_no_conflict_when_the_cv_is_silent_or_the_denial_has_no_head_noun():
    assert head_noun_scope_conflicts(_hv(["eigene Erfahrung mit Vertrieb"], bullets=["Einkauf."])) == []
    # a plain denial declares its term literally — no head-noun-only scope to ask about
    assert head_noun_scope_conflicts(_hv(["Vertrieb"], bullets=["Vertrieb im Außendienst."])) == []


def test_no_conflict_when_the_term_only_appears_inside_the_denial_wording():
    v = _hv(
        ["agile methodology experience"],
        bullets=["Keine agile methodology experience; Wasserfall-Projekte."],
    )
    assert head_noun_scope_conflicts(v) == []


def test_an_unconfirmed_skill_or_interview_prose_raises_nothing():
    v = _hv(["eigene Erfahrung mit Vertrieb"])
    v["skills"] = [{"name": "Vertrieb", "status": "unconfirmed"}]
    assert head_noun_scope_conflicts(v) == []


def test_units_rebuild_the_release_corpus_exactly():
    v = _hv(["x"], bullets=["Vertrieb B2B.", "Kundenpflege."], skills=["SAP SD"])
    from applire.services.ats_audit import _norm

    assert denial_release_corpus(v) == _norm(" | ".join(denial_release_units(v)))


def test_malformed_input():
    assert head_noun_scope_conflicts(None) == []
    assert head_noun_scope_conflicts({"metadata": {"denied_concepts": "nope"}}) == []
    assert head_noun_scope_conflicts({"metadata": None}) == []


def test_the_builder_parks_a_language_independent_record_with_both_answers():
    from applire.services.profile.reconcile.stance import (
        CV_HOLDS,
        DENIAL_HOLDS,
        denial_scope_confirmation,
    )

    hit = {"denial_concept": "eigene Erfahrung mit Vertrieb", "bare_term": "vertrieb", "cv_label": "Vertrieb B2B."}
    c = denial_scope_confirmation(hit)
    assert c.source == "denial_scope" and c.context["kind"] == "denial_scope"
    assert c.context["denial_concept"] == "eigene Erfahrung mit Vertrieb"
    assert c.option_keys == [DENIAL_HOLDS, CV_HOLDS]  # G-5: cv_holds = V's WithdrawDenial
    assert c.context["conflict_kind"] == "head_noun"
    q_de, opts_de = c.rendered("de")
    assert "„Vertrieb B2B“" in q_de
    assert opts_de == ["Ja, das stimmt so", "Nein, der Lebenslauf stimmt: Angabe zurücknehmen"]
    assert c.rendered("en")[1] == ["Yes, that's right", "No, the CV is right: take my answer back"]


def test_the_builder_words_a_literal_contradiction_with_v5_copy():
    from applire.services.profile.reconcile.stance import denial_scope_confirmation

    c = denial_scope_confirmation(
        {"kind": "literal", "denial_concept": "SAP PP", "bare_term": "sap pp",
         "cv_label": "SAP PP (Fertigungsaufträge, Stücklisten)", "denial_date": "2026-10-12"}
    )
    q_de, opts_de = c.rendered("de")
    assert q_de == (
        "Stimmt deine frühere Angabe noch? Am 12.10.2026 hast du gesagt, dass SAP PP nicht auf "
        "dich zutrifft. In deinem Lebenslauf steht aber: „SAP PP (Fertigungsaufträge, Stücklisten)“."
    )
    assert opts_de == ["Ja, meine Angabe stimmt", "Nein, der Lebenslauf stimmt: Angabe zurücknehmen"]
    assert c.rendered("en")[0].startswith("Is your earlier answer still right? On 12 Oct 2026 you said")
    assert c.context["conflict_kind"] == "literal"


def test_parking_is_once_per_denial_even_after_an_answer():
    from applire.schemas.profile import DeniedConcept, ProfileMetadata
    from applire.services.profile.reconcile.stance import park_denial_scope_confirmations

    v = _hv(["eigene Erfahrung mit Vertrieb"], bullets=["Vertrieb von Verpackungslösungen."])
    md = ProfileMetadata(denied_concepts=[DeniedConcept(concept="eigene Erfahrung mit Vertrieb", statement="", source="interview", date="2026-10-24")])
    first = park_denial_scope_confirmations(md, v)
    assert len(first) == 1 and len(md.pending_confirmations) == 1
    md.pending_confirmations[0].resolved = True
    md.pending_confirmations[0].chosen_option = "Ja, das stimmt so"
    assert park_denial_scope_confirmations(md, v) == []
    assert len(md.pending_confirmations) == 1
    assert park_denial_scope_confirmations(None, v) == []


# --- Seam: the commit path's denial hunk parks the Health item ---


@pytest.mark.asyncio
async def test_seam_commit_ops_parks_the_health_item_when_a_head_noun_denial_meets_the_cv():
    import copy
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot, authorized_profile_write
    from applire.services.profile.commit import CommitProvenance, TurnGrounding, commit_ops

    seed = {
        "personal_info": {"full_name": "Erika Muster", "email": "erika@example.invalid"},
        "work_experience": [
            {
                "id": "w1",
                "company": "Rheinwerk GmbH",
                "role": "Key Account Managerin",
                "start_date": "2018-01",
                "end_date": "2023-12",
                "responsibilities": ["Vertrieb von Verpackungslösungen an Lebensmittelkunden."],
            }
        ],
        "skills": [],
        "metadata": {
            "completeness_score": 0.0,
            "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z",
            "last_updated": "2020-01-01T00:00:00Z",
        },
    }
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            with authorized_profile_write():
                record = MasterProfile(profile_json=copy.deepcopy(seed))
            db.add(record)
            await db.commit()
            prov = CommitProvenance(source="testimony", intake="testimony", session_id=str(uuid.uuid4()))
            grounding = TurnGrounding(
                text="Eigene Erfahrung mit Vertrieb habe ich nicht.",
                denials=["eigene Erfahrung mit Vertrieb"],
            )
            await commit_ops(db, [], prov, grounding=grounding)
            # The same denial again: the question is never parked twice.
            await commit_ops(db, [], prov, grounding=grounding)
            parked = [
                c for c in record.profile_json["metadata"].get("pending_confirmations") or []
                if c.get("source") == "denial_scope"
            ]
            assert len(parked) == 1
            assert parked[0]["context"]["cv_label"] == "Vertrieb von Verpackungslösungen an Lebensmittelkunden."
            assert parked[0]["option_keys"] == ["denial_holds", "cv_holds"]
    finally:
        await engine.dispose()


def test_the_general_detector_also_finds_a_literal_denial_the_cv_names():
    from applire.services.profile.reconcile.stance import denial_cv_conflicts

    v = _hv(["Vertrieb", "eigene Erfahrung mit Einkauf"], bullets=["Vertrieb im Außendienst.", "Einkauf von Rohstoffen."])
    hits = denial_cv_conflicts(v)
    assert [(h["kind"], h["denial_concept"], h["cv_label"]) for h in hits] == [
        ("literal", "Vertrieb", "Vertrieb im Außendienst."),
        ("head_noun", "eigene Erfahrung mit Einkauf", "Einkauf von Rohstoffen."),
    ]
    # the FS-7 view keeps only the head-noun half
    from applire.services.profile.reconcile.stance import head_noun_scope_conflicts

    assert [h["denial_concept"] for h in head_noun_scope_conflicts(v)] == ["eigene Erfahrung mit Einkauf"]


def test_the_general_detector_is_word_bounded():
    from applire.services.profile.reconcile.stance import denial_cv_conflicts

    assert denial_cv_conflicts(_hv(["Vertrieb"], bullets=["Vertriebsunterstützung im Innendienst."])) == []


def test_parking_covers_a_literal_contradiction_too():
    from applire.schemas.profile import DeniedConcept, ProfileMetadata
    from applire.services.profile.reconcile.stance import park_denial_scope_confirmations

    v = _hv(["Vertrieb"], bullets=["Vertrieb im Außendienst."])
    v["metadata"]["denied_concepts"][0]["date"] = "2026-10-12"
    md = ProfileMetadata(denied_concepts=[DeniedConcept(concept="Vertrieb", statement="", source="interview", date="2026-10-12")])
    (c,) = park_denial_scope_confirmations(md, v)
    assert c.context["conflict_kind"] == "literal"
    assert "Am 12.10.2026" in c.rendered("de")[0]
    assert c.rendered("de")[0].endswith("„Vertrieb im Außendienst“.")  # no doubled full stop


@pytest.mark.asyncio
async def test_seam_an_answered_question_is_not_re_parked_on_the_next_import():
    """V's resolution REMOVES the answered item from pending_confirmations; the
    answered key (metadata.denial_scope_answered) keeps the next import from
    asking again. A withdrawn denial (cv_holds) cannot re-park: it is gone."""
    import copy
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot, authorized_profile_write
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import AddBullets, UpsertSkill
    from applire.services.profile.reconcile.stance import denial_scope_key

    seed = {
        "personal_info": {"full_name": "Erika Muster", "email": "erika@example.invalid"},
        "work_experience": [
            {"id": "w1", "company": "Rheinwerk GmbH", "role": "Key Account Managerin",
             "start_date": "2018-01", "end_date": "2023-12",
             "responsibilities": ["Key Accounts im Maschinenbau."]}
        ],
        "skills": [],
        "metadata": {
            "completeness_score": 0.0, "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z", "last_updated": "2020-01-01T00:00:00Z",
            "denied_concepts": [{"concept": "Vertrieb", "statement": "Vertrieb nie.", "source": "interview", "date": "2026-10-12"}],
        },
    }
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def _parked(rec):
        return [c for c in rec.profile_json["metadata"].get("pending_confirmations") or [] if c.get("source") == "denial_scope"]

    try:
        async with factory() as db:
            with authorized_profile_write():
                record = MasterProfile(profile_json=copy.deepcopy(seed))
            db.add(record)
            await db.commit()
            prov = CommitProvenance(source="cv_upload", intake="cv_import", session_id=str(uuid.uuid4()))
            # An unrelated import: nothing names the denied term, nothing parks.
            await commit_ops(db, [UpsertSkill(name="SAP SD", category="technical", status="confirmed")], prov)
            assert _parked(record) == []
            # An import that NAMES the denied term meets the old denial: the item parks.
            await commit_ops(db, [UpsertSkill(name="Vertrieb", category="soft", status="confirmed")], prov)
            (item,) = _parked(record)
            assert item["context"]["conflict_kind"] == "literal"
            assert item["context"]["cv_label"] == "Vertrieb"

            # V's resolution on denial_holds: the item is removed, the answer remembered.
            pj = copy.deepcopy(record.profile_json)
            pj["metadata"]["pending_confirmations"] = []
            pj["metadata"]["denial_scope_answered"] = [denial_scope_key(item["context"])]
            with authorized_profile_write():
                record.profile_json = pj
            await db.commit()
            # A later import names the term in NEW prose: same question, already answered.
            await commit_ops(db, [AddBullets(target="w1", responsibilities=["Vertrieb im Außendienst."])], prov)
            assert "Vertrieb im Außendienst." in record.profile_json["work_experience"][0]["responsibilities"]
            assert _parked(record) == [], "an answered question is never asked again"

            # cv_holds: V withdraws the denial — nothing left to contradict.
            pj = copy.deepcopy(record.profile_json)
            pj["metadata"]["denied_concepts"] = []
            pj["metadata"]["denial_scope_answered"] = []
            with authorized_profile_write():
                record.profile_json = pj
            await db.commit()
            await commit_ops(db, [AddBullets(target="w1", responsibilities=["Vertrieb Export."])], prov)
            assert _parked(record) == []
    finally:
        await engine.dispose()
