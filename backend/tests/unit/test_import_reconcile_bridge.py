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

import pytest
from applire.schemas.profile import Certification, MasterProfileData, WorkEntry
from applire.services.profile.merge import MergeResult
from applire.services.profile.reconcile.import_bridge import reconcile_import


class _Stub:
    def __init__(self, payload): self.payload = payload
    async def aparse_json(self, prompt, **kw): return self.payload


# #190 — certifications must survive CV import end-to-end. They were dropped
# because the extractor/reconciler LLM misroutes cert names (ITIL, CPSA, CSV)
# into `skills`; the durable guarantee is a DETERMINISTIC passthrough in
# reconcile_import that unions incoming.certifications into the merged profile
# regardless of what op batch the LLM emits.


@pytest.mark.asyncio
async def test_import_certifications_from_upsert_op_land_in_profile():
    """Baseline: when the reconciler DOES emit upsert_certification, the cert lands."""
    incoming = MasterProfileData(certifications=[Certification(name="ITIL Foundation")])
    stub = _Stub({"ops": [
        {"op": "upsert_certification", "name": "ITIL Foundation",
         "issuing_organization": "AXELOS"},
    ], "ambiguities": []})
    result = await reconcile_import(MasterProfileData(), incoming, "cv_upload", stub)
    assert [c.name for c in result.merged_profile.certifications] == ["ITIL Foundation"]


@pytest.mark.asyncio
async def test_import_certifications_survive_empty_ops_first_import():
    """Fix C — the deterministic union guarantees certs even when the LLM emits an
    EMPTY op batch (proving the guarantee is code-side, not the LLM op). First
    import (no existing profile)."""
    incoming = MasterProfileData(certifications=[
        Certification(name="ITIL Foundation", issuing_organization="AXELOS"),
        Certification(name="CPSA Foundation Level", issuing_organization="iSAQB"),
        Certification(name="Expert for Computersystemvalidation"),
    ])
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(MasterProfileData(), incoming, "cv_upload", stub)
    names = [c.name for c in result.merged_profile.certifications]
    assert names == [
        "ITIL Foundation",
        "CPSA Foundation Level",
        "Expert for Computersystemvalidation",
    ]
    # issuer preserved verbatim
    assert result.merged_profile.certifications[0].issuing_organization == "AXELOS"


@pytest.mark.asyncio
async def test_import_certifications_survive_skills_only_ops_into_existing():
    """Fix C — a skills-only op batch (the exact misroute in #190) must NOT drop
    incoming certifications. Reconcile-into-existing path."""
    existing = MasterProfileData(
        work_experience=[WorkEntry(company="Acme", role="Engineer")]
    )
    incoming = MasterProfileData(certifications=[Certification(name="ITIL Foundation")])
    stub = _Stub({"ops": [
        {"op": "upsert_skill", "name": "ITIL", "category": "domain",
         "proficiency": "advanced", "evidence": []},
    ], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert "ITIL Foundation" in [c.name for c in result.merged_profile.certifications]
    # existing work preserved
    assert len(result.merged_profile.work_experience) == 1


@pytest.mark.asyncio
async def test_import_certifications_no_double_add_when_already_present():
    """Fix C — the near-dupe guard means an incoming cert already on the profile
    (whether from the existing profile or a co-emitted upsert op) is not appended
    twice; empty fields are filled instead."""
    existing = MasterProfileData(certifications=[Certification(name="ITIL Foundation")])
    incoming = MasterProfileData(certifications=[
        Certification(name="ITIL Foundation", issuing_organization="AXELOS"),
    ])
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 1                         # no duplicate
    assert certs[0].issuing_organization == "AXELOS"  # empty issuer filled from incoming


# ── #618: _union_certifications' identity instrument (three real pairs from a
# FlowCV + LinkedIn two-source import). Before the fix, `_union_certifications`
# called the section-agnostic `classify_dupe` on name alone (0/3 MATCH on these
# pairs); it now calls `classify_certification_dupe` — the same cert-aware
# instrument `_apply_upsert_certification` (apply.py) and
# `import_witness.compute_import_not_applied` already used, so all three
# readers of certification identity agree (ADR-066).


@pytest.mark.asyncio
async def test_import_certifications_union_matches_en_de_cross_language_pair():
    """#618 pair 1: EN name from one source, DE translation from the other."""
    existing = MasterProfileData(
        certifications=[Certification(name="Expert for Computersystemvalidation")]
    )
    incoming = MasterProfileData(
        certifications=[Certification(name="Experte für Computervalidierung")]
    )
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 1                          # no duplicate across languages


@pytest.mark.asyncio
async def test_import_certifications_union_matches_trademark_symbol_pair():
    """#618 pair 2: a trailing '® Foundation' vs 'Foundation Level' variant —
    the ® fuses onto the adjacent token under the generic tokeniser, which
    that instrument never strips."""
    existing = MasterProfileData(
        certifications=[Certification(name="ITIL Foundation Level")]
    )
    incoming = MasterProfileData(
        certifications=[Certification(name="ITIL® Foundation")]
    )
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 1                          # no duplicate across the ® variant


@pytest.mark.asyncio
async def test_import_certifications_union_matches_cognate_stem_pair():
    """#618 pair 3: 'Software Architect' vs 'Software Architecture' — a
    cognate-stem variant just under the generic near-dupe Jaccard threshold."""
    existing = MasterProfileData(certifications=[
        Certification(name="Certified Professional Software Architect Foundation Level"),
    ])
    incoming = MasterProfileData(certifications=[
        Certification(name="Certified Professional for Software Architecture Foundation Level"),
    ])
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 1                          # no duplicate across the cognate stem


@pytest.mark.asyncio
async def test_import_certifications_union_keeps_both_on_confirmed_org_conflict():
    """#618 org-conflict question: the instrument swap alone does NOT collapse a
    same-name pair whose two sources report a genuinely different (non-overlapping)
    issuing_organization — `classify_certification_dupe` rules that AMBIGUOUS
    (a name match against a *confirmed different* issuer is 'unsure', never a
    silent merge), and `_union_certifications` has no confirmation channel, so
    its append-on-non-MATCH trade fires: both entries survive. Unchanged
    behaviour from before the fix — this is not a regression, it is the
    documented trade against silent data loss."""
    existing = MasterProfileData(certifications=[
        Certification(name="ITIL Foundation Level", issuing_organization="AXELOS"),
    ])
    incoming = MasterProfileData(certifications=[
        Certification(name="ITIL® Foundation", issuing_organization="PeopleCert"),
    ])
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 2                          # kept apart, not silently merged
    assert {c.issuing_organization for c in certs} == {"AXELOS", "PeopleCert"}


@pytest.mark.asyncio
async def test_import_certifications_union_keeps_distinct_certs_from_same_issuer_separate():
    """Safety net on the stronger instrument: two genuinely DIFFERENT
    certifications from the SAME issuer (two AWS certs) must not collapse into
    one just because they share an issuer and several name tokens."""
    existing = MasterProfileData(certifications=[
        Certification(name="AWS Certified Solutions Architect - Associate",
                      issuing_organization="Amazon Web Services"),
    ])
    incoming = MasterProfileData(certifications=[
        Certification(name="AWS Certified Solutions Architect - Professional",
                      issuing_organization="Amazon Web Services"),
    ])
    stub = _Stub({"ops": [], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    certs = result.merged_profile.certifications
    assert len(certs) == 2                          # two real certs, not one
    names = {c.name for c in certs}
    assert names == {
        "AWS Certified Solutions Architect - Associate",
        "AWS Certified Solutions Architect - Professional",
    }


@pytest.mark.asyncio
async def test_import_folds_synonym_role_into_existing():
    existing = MasterProfileData(work_experience=[WorkEntry(company="Applire", role="Founder & Lead Developer")])
    wid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="applire", role="Owner")])
    stub = _Stub({"ops":[{"op":"upsert_work","ref":"w1","target":wid,"company":"applire","role":"Owner"}],"ambiguities":[]})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    assert isinstance(result, MergeResult)
    assert len(result.merged_profile.work_experience) == 1            # no duplicate
    assert "Owner" in result.merged_profile.work_experience[0].role_aliases
    assert result.changes
    assert isinstance(result.reconciliation, dict)


@pytest.mark.asyncio
async def test_import_ambiguity_becomes_confirmation_not_conflict():
    """E037 PQ #4 — a RequestConfirmation ambiguity must surface through the
    confirmation channel (question + each option as its own option), NOT be
    force-coerced into the 2-value Conflict shape (which garbled the dialog:
    section='', the whole question swallowed into `field`, and the option list
    comma-joined into `incoming_value`)."""
    stub = _Stub({"ops": [], "ambiguities": [{
        "op": "request_confirmation",
        "question": "Is 'Lead Developer' at applire the same role as your existing 'Founder' entry?",
        "options": ["Keep as separate roles", "Merge into existing role", "Replace existing role"],
    }]})
    result = await reconcile_import(MasterProfileData(), MasterProfileData(), "cv_upload", stub)

    # The ambiguity rides the confirmation channel, intact.
    assert len(result.pending_confirmations) == 1
    pc = result.pending_confirmations[0]
    assert pc.question == (
        "Is 'Lead Developer' at applire the same role as your existing 'Founder' entry?"
    )
    # Each option is preserved as its own selectable option (3 distinct buttons),
    # never comma-joined into one string.
    assert pc.options == [
        "Keep as separate roles", "Merge into existing role", "Replace existing role"
    ]
    assert pc.source == "cv_upload"

    # The old garble path is gone: no Conflict is manufactured from the ambiguity.
    assert result.conflicts == []
    # Belt-and-braces: there is no Conflict with the empty-section / list-valued
    # incoming_value signature the malformed coercion produced.
    for c in result.conflicts:
        assert c.section != ""
        assert not isinstance(c.incoming_value, list)


@pytest.mark.asyncio
async def test_import_real_flag_conflict_still_surfaces_as_conflict():
    """A genuine two-value FlagConflict (existing vs incoming) is unaffected — it
    still surfaces on the conflict channel; only RequestConfirmation ambiguities
    move to confirmations."""
    existing = MasterProfileData(work_experience=[WorkEntry(company="Acme", role="Engineer", start_date="2020-01")])
    wid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Acme", role="Engineer", start_date="2019-06")])
    stub = _Stub({"ops": [
        {"op": "upsert_work", "ref": "w1", "target": wid, "company": "Acme", "role": "Engineer"},
        {"op": "flag_conflict", "target": wid, "field": "start_date",
         "existing": "2020-01", "incoming": "2019-06"},
    ], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].field == "start_date"
    assert result.conflicts[0].incoming_value == "2019-06"
    assert result.pending_confirmations == []


@pytest.mark.asyncio
async def test_import_de_en_employer_fold():
    existing = MasterProfileData(work_experience=[WorkEntry(company="Roche Diagnostics GmbH", role="System Analyst")])
    rid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker")])
    stub = _Stub({"ops":[{"op":"upsert_work","ref":"w1","target":rid,"company":"Roche","role":"Systemanalytiker"}],"ambiguities":[]})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert len(result.merged_profile.work_experience) == 1            # DE/EN fold, no dup
