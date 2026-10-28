# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-5: one certificate, held under its German title, stood in
the profile a second time under its English title after a second-source import.

Two paths led there; both are pinned here.

1. The reconciler said "already there" (``match_existing``). The deterministic
   certification union (#190) did not read that receipt. It asked only the
   lexical instrument, which calls a translated title DISTINCT, and appended
   the certificate again.
2. The reconciler said nothing, or upserted the English title. The applier and
   the union both used the same lexical instrument. Whether two titles name one
   credential is a judgement (ADR-062), so ONE model call now judges the
   certificates an import added against the held ones. A match is applied like
   ``match_existing``.

Synthetic titles only (the founder pair stays in the scratch bench).
"""
import pytest

from applire.schemas.profile import Certification, MasterProfileData, MatchReceipt
from applire.services.profile.reconcile.apply import ApplyResult, _added
from applire.services.profile.reconcile.certificate_identity import bind_translated_certificates
from applire.services.profile.reconcile.dedupe import classify_certification_dupe
from applire.services.profile.reconcile.import_bridge import _union_certifications
from applire.services.profile.reconcile.ops import MatchExisting

HELD = "Fachkraft für Qualitätssicherung im Labor"
ADDED = "Laboratory Quality Assurance Specialist"


def _profile(*certs: Certification) -> MasterProfileData:
    return MasterProfileData(certifications=list(certs))


def test_the_lexical_instrument_calls_the_translated_pair_distinct():
    """The baseline both fixes stand on: the fold cannot see a translation."""
    verdict = classify_certification_dupe(
        name=ADDED, issuing_organization=None, credential_id=None,
        existing=[Certification(id="c1", name=HELD)],
        name_getter=lambda c: c.name, org_getter=lambda c: c.issuing_organization,
        credential_id_getter=lambda c: c.credential_id,
    )
    assert verdict.match is None


def test_union_respects_the_reconcilers_match_receipt():
    merged = _profile(Certification(id="c1", name=HELD))
    incoming = _profile(Certification(name=ADDED, date_obtained="2019-05"))
    changes: list = []
    receipt = MatchReceipt(section="certifications", entity_id="c1", incoming=ADDED, existing=HELD)

    _union_certifications(merged, incoming, changes, [receipt])

    assert [c.name for c in merged.certifications] == [HELD]
    assert merged.certifications[0].date_obtained == "2019-05"  # empties filled
    assert all(c.action != "added" for c in changes)


def test_union_without_the_receipt_still_appends():
    """Mutation guard for the test above: the receipt is what keeps it out."""
    merged = _profile(Certification(id="c1", name=HELD))
    incoming = _profile(Certification(name=ADDED))
    changes: list = []

    _union_certifications(merged, incoming, changes, [])

    assert [c.name for c in merged.certifications] == [HELD, ADDED]


def test_an_undone_receipt_binds_nothing():
    merged = _profile(Certification(id="c1", name=HELD))
    incoming = _profile(Certification(name=ADDED))
    receipt = MatchReceipt(section="certifications", entity_id="c1", incoming=ADDED, existing=HELD)
    receipt.undone_at = __import__("datetime").datetime(2026, 10, 28)

    _union_certifications(merged, incoming, [], [receipt])

    assert len(merged.certifications) == 2


class _Judge:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0
        self.system = None

    async def aparse_json(self, prompt, **kw):
        self.calls += 1
        self.system = kw.get("system")
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _applied(existing: MasterProfileData) -> ApplyResult:
    merged = existing.model_copy(deep=True)
    new = Certification(id="c2", name=ADDED, issuing_organization="Kammer Nord")
    merged.certifications.append(new)
    return ApplyResult(profile=merged, changes=[_added("certifications", "name", ADDED)])


@pytest.mark.asyncio
async def test_a_judged_translation_folds_into_the_held_certificate():
    existing = _profile(Certification(id="c1", name=HELD))
    applied = _applied(existing)
    judge = _Judge({"matches": [{"item": "n0", "existing": "c1"}]})

    ops = await bind_translated_certificates(existing, applied, judge)

    assert [c.name for c in applied.profile.certifications] == [HELD]
    assert applied.profile.certifications[0].issuing_organization == "Kammer Nord"
    assert not [c for c in applied.changes if c.action == "added"]
    [receipt] = applied.matched
    assert (receipt.section, receipt.entity_id, receipt.incoming, receipt.existing) == (
        "certifications", "c1", ADDED, HELD)
    assert ops == [MatchExisting(target="c1", incoming=ADDED, existing=HELD)]
    assert "you compare the certificates of one person's profile" in judge.system.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"matches": [{"item": "n0", "existing": None}]},
    {"matches": [{"item": "n0", "existing": "c9"}]},  # an id it was not offered
    {"matches": [{"item": "n7", "existing": "c1"}]},  # an item it was not asked
    RuntimeError("provider down"),
])
async def test_no_clear_match_keeps_both(answer):
    existing = _profile(Certification(id="c1", name=HELD))
    applied = _applied(existing)

    ops = await bind_translated_certificates(existing, applied, _Judge(answer))

    assert [c.name for c in applied.profile.certifications] == [HELD, ADDED]
    assert ops == [] and applied.matched == []


@pytest.mark.asyncio
async def test_no_call_when_the_import_added_no_certificate():
    existing = _profile(Certification(id="c1", name=HELD))
    applied = ApplyResult(profile=existing.model_copy(deep=True), changes=[])
    judge = _Judge({"matches": []})

    assert await bind_translated_certificates(existing, applied, judge) == []
    assert judge.calls == 0


class _ImportDouble:
    """The reconciler upserts the translated title, or says nothing about it
    (the union then adds it); the certificate judge says it is the held one;
    every other judge finds nothing."""

    def __init__(self, cert_ops):
        self.cert_ops = cert_ops
        self.systems: list[str] = []

    async def aparse_json(self, prompt, **kw):
        system = (kw.get("system") or "").lower()
        self.systems.append(system)
        if "profile reconciler" in system:
            return {"ops": self.cert_ops, "ambiguities": [], "denials": []}
        if "you compare the certificates of one person's profile" in system:
            return {"matches": [{"item": "n0", "existing": "c1"}]}
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("cert_ops", [
    [{"op": "upsert_certification", "name": ADDED}],
    [],  # the union adds it; only the returned binding keeps the witness quiet
], ids=["upserted", "silent"])
async def test_seam_an_import_of_the_translated_title_leaves_one_certificate(cert_ops):
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    existing = _profile(Certification(id="c1", name=HELD))
    incoming = _profile(Certification(name=ADDED))
    provider = _ImportDouble(cert_ops)

    result = await reconcile_import(existing, incoming, source="cv_upload", provider=provider)

    assert [c.name for c in result.merged_profile.certifications] == [HELD]
    assert [(m.section, m.incoming) for m in result.matched] == [("certifications", ADDED)]
    assert not [i for i in result.not_applied if i.section == "certifications"]
    assert any("you compare the certificates" in s for s in provider.systems)
