# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""D-4 / MD6-10 (Hazelnut build 6): a WITHDRAWN denial's statement is never
vault evidence.

After the A-1 heal question is answered "Ja, zurücknehmen", the chip-clause
denials move verbatim to ``metadata.withdrawn_denials``. The delivery run found
``keyword_ledger.profile_literal_corpus`` (via ``_strip_denial_text``) still
reading those statements: ``assert_claimable_backed`` healed "Microsoft Azure"
and "Apache Kafka" to ADJACENT partials on the withdrawn clause
"…, aber zu … Microsoft Azure … steht in meinem Profil noch nichts", and both
writer prompts then said the profile's adjacent capability IS that clause.
Withdrawing a "no" revokes it; it never turns the words into a "yes".

The same strip now also drops the system's own questions about a denial (the
open ``pending_confirmations``, the resolved question's receipt, the
``denial_scope_answered`` keys): they restate the concepts they ask about.

One seam test per consumer of the literal corpus, each with a positive control
that the same text in real vault content still counts:

1. ``assert_claimable_backed`` — the ADJACENT heal (the D-4 site);
2. ``reevaluate_gap_ledger_against_vault`` — the gap-heal presence corpus;
3. ``cv_diff.compute_cv_profile_diff`` — the US147 pre-download diff;
4. the ATS ``present_unsupported`` guard, fed by ``profile_literal_corpus`` at
   four doors (CV report, CV docx, letter report, letter docx);
5. the real path: resolve the heal question through ``resolve_confirmation``.

The other corpora were audited and read no withdrawn statement (fact, pinned
here): ``stance.denial_release_corpus``, the Oracle's ``build_vault_index``.

Synthetic data only.
"""

import copy
import inspect
import re
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.keyword_ledger import (
    assert_claimable_backed,
    profile_literal_corpus,
    reevaluate_gap_ledger_against_vault,
)

CLAUSE = "aber zu Snowflake, Microsoft Azure und Apache Kafka steht in meinem Profil noch nichts"
CHIP = (
    "Bei der Orvelin Analytik GmbH habe ich die Integrationsplattform auf MuleSoft betrieben "
    "und die Schnittstellen-Governance eingeführt, " + CLAUSE + "."
)
CLAUSE_TERMS = ["Snowflake", "Microsoft Azure", "Apache Kafka"]
LIVE_BULLET = "Integrationsplattform auf MuleSoft betrieben und Schnittstellen-Governance eingeführt"


def _row(concept, statement=CHIP, **extra):
    return {
        "concept": concept, "statement": statement, "source": "interview",
        "date": "2026-11-02", "denial_level": "direct", "probe_asked": False, **extra,
    }


def _vault(*, withdrawn=(), denied=(), responsibilities=(), skills=None, pending=None,
           answered=None, history=None):
    return {
        "personal_info": {"full_name": "Jana Frehse", "email": "jana@example.invalid"},
        "work_experience": [{
            "id": "w1", "company": "Orvelin Analytik GmbH", "role": "Leiterin Integration",
            "start_date": "2020-03", "is_current": True,
            "responsibilities": list(responsibilities),
        }],
        "skills": skills or [{"id": "s-ms", "name": "MuleSoft", "category": "technical", "status": "confirmed"}],
        "metadata": {
            "completeness_score": 0.0, "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z", "last_updated": "2020-01-01T00:00:00Z",
            "denied_concepts": copy.deepcopy(list(denied)),
            "withdrawn_denials": copy.deepcopy(list(withdrawn)),
            "pending_confirmations": copy.deepcopy(pending or []),
            "denial_scope_answered": list(answered or []),
            "enrichment_history": copy.deepcopy(history or []),
        },
    }


def _withdrawn_vault(**kw):
    return _vault(withdrawn=[_row(t, withdrawn_at="2026-11-05") for t in CLAUSE_TERMS], **kw)


def _entry(concept, *, status="partial", claimable=True, evidence=CLAUSE):
    return {
        "concept": concept, "surface_forms": [concept], "sources": ["required"],
        "fit_weight": 1.0, "status": status, "evidence": evidence, "claimable": claimable,
    }


# ---------------------------------------------------------------------------
# 0. The corpus itself
# ---------------------------------------------------------------------------


def test_a_withdrawn_statement_is_stripped_exactly_like_a_live_one():
    from applire.services.ats_audit import _norm

    for vault in (_withdrawn_vault(), _vault(denied=[_row(t) for t in CLAUSE_TERMS])):
        corpus = profile_literal_corpus(vault)
        for term in CLAUSE_TERMS:
            assert _norm(term) not in corpus, term
        assert _norm("schnittstellen-governance eingeführt") not in corpus, "the statement's prose is gone too"
        assert "mulesoft" in corpus and "orvelin analytik gmbh" in corpus, "real content stays"


def test_the_systems_own_questions_about_a_denial_are_not_evidence():
    from applire.services.profile.reconcile.stance import (
        offered_choice_denial_confirmation,
        offered_choice_denial_hits,
    )

    seed = _vault(denied=[_row(t) for t in CLAUSE_TERMS])
    (hit,) = offered_choice_denial_hits(seed)
    question = offered_choice_denial_confirmation(hit).model_dump(mode="json")
    assert "Microsoft Azure" in question["question"], "precondition: the question names it"
    vault = _withdrawn_vault(
        pending=[question],
        answered=["offered_choice|0123456789abcdef", "microsoft azure|azure"],
        history=[{
            "id": "r1", "timestamp": "2026-11-05T10:00:00Z", "source": "manual_edit",
            "changes": [
                {"section": "metadata", "field": "pending_confirmations", "action": "updated",
                 "old_value": question["question"], "new_value": "Ja, zurücknehmen",
                 "rationale": "Recorded your answer to a confirmation question."},
                {"section": "skills", "field": "skills", "action": "added",
                 "old_value": None, "new_value": "Grafana", "rationale": "added"},
            ],
        }],
    )
    corpus = profile_literal_corpus(vault)
    assert "azure" not in corpus and "kafka" not in corpus and "snowflake" not in corpus
    assert "grafana" in corpus, "every OTHER receipt passes untouched"


# ---------------------------------------------------------------------------
# 1. assert_claimable_backed — the D-4 site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("concept", ["Microsoft Azure", "Apache Kafka"])
def test_a_withdrawn_clause_never_heals_a_row_to_an_adjacent_partial(concept):
    healed, violations = assert_claimable_backed([_entry(concept)], _withdrawn_vault(), seam="cv generation")
    assert [v["reason"] for v in violations] == ["no_vault_evidence_unit"]
    (row,) = healed
    assert (row["status"], row["claimable"]) == ("gap", False)
    assert "adjacent_evidence" not in row and row["evidence"] == ""


def test_control_the_same_words_in_a_real_bullet_still_heal_adjacent():
    vault = _withdrawn_vault(responsibilities=[LIVE_BULLET])
    healed, _ = assert_claimable_backed([_entry("Microsoft Azure", evidence=LIVE_BULLET)], vault)
    (row,) = healed
    assert (row["status"], row["claimable"]) == ("partial", True)
    assert row["adjacent_evidence"] == LIVE_BULLET


# ---------------------------------------------------------------------------
# 2. reevaluate_gap_ledger_against_vault — the gap-heal presence corpus
# ---------------------------------------------------------------------------


def test_a_withdrawn_clause_never_heals_a_gap_row_as_present():
    ledger = [_entry("Microsoft Azure", status="gap", claimable=False, evidence="")]
    new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, _withdrawn_vault())
    assert changed is False
    assert (new_ledger[0]["status"], new_ledger[0]["claimable"]) == ("gap", False)


def test_control_a_real_bullet_naming_the_term_still_heals_the_gap_row():
    vault = _withdrawn_vault(responsibilities=["Migration der Datenstrecken nach Microsoft Azure geleitet"])
    ledger = [_entry("Microsoft Azure", status="gap", claimable=False, evidence="")]
    new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, vault)
    assert changed is True and new_ledger[0]["claimable"] is True


# ---------------------------------------------------------------------------
# 3. compute_cv_profile_diff — the US147 pre-download diff
# ---------------------------------------------------------------------------


def test_a_cv_skill_only_a_withdrawn_clause_names_is_still_reported():
    from applire.services.cv_diff import compute_cv_profile_diff

    changes = compute_cv_profile_diff({"skills": ["Microsoft Azure", "MuleSoft"]}, _withdrawn_vault())
    assert [c.new_value for c in changes] == ["Microsoft Azure"]


def test_control_a_cv_skill_a_real_bullet_names_is_not_reported():
    from applire.services.cv_diff import compute_cv_profile_diff

    vault = _withdrawn_vault(responsibilities=["Migration der Datenstrecken nach Microsoft Azure geleitet"])
    assert compute_cv_profile_diff({"skills": ["Microsoft Azure"]}, vault) == []


# ---------------------------------------------------------------------------
# 4. The ATS present_unsupported guard and its four doors
# ---------------------------------------------------------------------------


def test_a_term_only_a_withdrawn_clause_names_stays_present_unsupported():
    from applire.services.ats_audit import _keyword_coverage, _norm

    ledger = [_entry("Microsoft Azure", status="gap", claimable=False, evidence="")]
    text = _norm("Leiterin Integration. Betrieb von Integrationen auf Microsoft Azure.")
    cov = _keyword_coverage(text, ["Microsoft Azure"], ledger, profile_literal_corpus(_withdrawn_vault()))
    assert cov.present_unsupported == ["Microsoft Azure"]
    # control: the same audit against a vault whose real bullet names it
    grounded = profile_literal_corpus(
        _withdrawn_vault(responsibilities=["Migration der Datenstrecken nach Microsoft Azure geleitet"])
    )
    assert _keyword_coverage(text, ["Microsoft Azure"], ledger, grounded).present_unsupported == []


@pytest.mark.parametrize(
    ("module", "function", "variable"),
    [
        ("applire.services.cv", "_update_ats_report", "vault_text_norm"),
        ("applire.services.cv", "_update_ats_report", "docx_vault_text_norm"),
        ("applire.services.cover_letter", "_update_ats_report_letter", "vault_text_norm"),
        ("applire.services.cover_letter", "_update_ats_report_letter", "docx_vault_text_norm"),
    ],
)
def test_every_ats_door_reads_the_stripped_literal_corpus(module, function, variable):
    import importlib

    src = inspect.getsource(getattr(importlib.import_module(module), function))
    assert re.search(rf"\b{variable}\s*=\s*\(?\s*profile_literal_corpus\(", src), (
        f"{module}.{function}: {variable} must come from profile_literal_corpus"
    )
    assert re.search(rf"vault_text_norm\s*=\s*{variable}\b", src)


# ---------------------------------------------------------------------------
# 5. The audited corpora that never read a withdrawn statement
# ---------------------------------------------------------------------------


def test_the_release_corpus_and_the_oracle_index_read_no_withdrawn_statement():
    from applire.services.oracle.matchers.vault import build_vault_index
    from applire.services.profile.reconcile.stance import denial_release_corpus

    vault = _withdrawn_vault()
    assert "azure" not in denial_release_corpus(vault)
    index = build_vault_index(vault)
    assert "azure" not in index.all_text_norm
    assert index.denial_units == [] and not [u for u in index.units if "azure" in u.text_norm]


# ---------------------------------------------------------------------------
# 6. The real path: "Ja, zurücknehmen" through resolve_confirmation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_after_take_it_back_the_withdrawn_clause_backs_nothing(db_session):
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.services.profile import resolve_confirmation
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import UpsertSkill

    with authorized_profile_write():
        record = MasterProfile(profile_json=_vault(denied=[_row(t) for t in CLAUSE_TERMS]))
    db_session.add(record)
    await db_session.commit()
    await commit_ops(
        db_session, [UpsertSkill(name="Terraform", category="technical")],
        CommitProvenance(source="manual_edit", intake="skill_edit", session_id=str(uuid.uuid4())),
        record=record,
    )
    await db_session.commit()
    await db_session.refresh(record)
    (item,) = [c for c in record.profile_json["metadata"]["pending_confirmations"]
               if (c.get("context") or {}).get("conflict_kind") == "offered_choice"]
    await resolve_confirmation(item["confirmation_id"], "Ja, zurücknehmen", db_session)
    await db_session.refresh(record)

    vault = record.profile_json
    assert sorted(d["concept"] for d in vault["metadata"]["withdrawn_denials"]) == sorted(CLAUSE_TERMS)
    assert vault["metadata"]["denied_concepts"] == []
    assert "Microsoft Azure" in str(vault["metadata"]), "precondition: the raw vault still names it"
    corpus = profile_literal_corpus(vault)
    assert "azure" not in corpus and "kafka" not in corpus and "snowflake" not in corpus
    healed, _ = assert_claimable_backed([_entry("Microsoft Azure")], vault, seam="cv generation")
    assert (healed[0]["status"], healed[0]["claimable"]) == ("gap", False)
