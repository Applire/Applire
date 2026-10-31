# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 6/9 (UAT-2 fix round, WP-B): the delivery actions WP-C's
resolvers call. Each answer → model edit of one sentence → the REAL re-audit
(Oracle self-audit over the section-editor overrides) → the finding key is gone
(T-A at the service seam; WP-C pins the endpoints). The fixtures are the
dead-end shapes of U-13: a fact rendered under the wrong station whose evidence
already exists elsewhere, and a figure the profile does not hold.

The review_actions plumbing (owner-keyed load, section write, re-audit) is
replaced by an in-memory twin that uses the real section-editor override
application and the real Oracle, so "recompute" is the production predicate.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services import delivery_actions as da
# Bound BEFORE any test patches review_actions.load_document: review_signals binds
# review_actions names at import, and delivery_actions imports it lazily — a first
# import under the monkeypatch would leak the twin into every later test.
from applire.services import review_signals as _review_signals  # noqa: F401
from applire.services import review_state as rs
from applire.services.cv_section_editor import apply_overrides_to_tailored, build_content_snapshot
from applire.services.oracle.selfaudit import build_self_audit_report

A, B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
VAULT = {
    "professional_summary": {"de": "Finanzbuchhalterin.", "en": ""},
    "work_experience": [
        {"id": A, "company": "Muster Logistik GmbH", "title": "Finanzbuchhalterin",
         "start_date": "2019-01", "end_date": None,
         "responsibilities": ["Monatsabschlüsse nach HGB vorbereitet"]},
        {"id": B, "company": "Nord Spedition AG", "title": "Buchhalterin",
         "start_date": "2014-01", "end_date": "2018-12",
         "responsibilities": ["Kreditorenbuchhaltung für 3 Gesellschaften mit DATEV geführt"]},
    ],
    "education": [], "skills": [{"name": "DATEV", "level": None, "category": "technical"}],
    "languages": [], "certifications": [],
    "contact": {"first_name": "Erika", "last_name": "Muster", "email": "erika@example.com"},
}
WRONG_PLACE = "Kreditorenbuchhaltung für 3 Gesellschaften mit DATEV geführt"
FIGURE = "Monatsabschlüsse für 12 Gesellschaften nach HGB vorbereitet"


def _tailored(a_bullets, b_bullets):
    return TailoredCVData.model_validate({
        "contact": {"name": "Erika Muster", "email": "erika@example.com"},
        "summary": "Finanzbuchhalterin.",
        "work_history": [
            {"id": A, "company": "Muster Logistik GmbH", "role": "Finanzbuchhalterin",
             "start_date": "2019-01", "bullets": a_bullets},
            {"id": B, "company": "Nord Spedition AG", "role": "Buchhalterin",
             "start_date": "2014-01", "end_date": "2018-12", "bullets": b_bullets},
        ],
        "skills": ["DATEV"],
    })


class _Twin:
    """In-memory review_actions: real overrides, real Oracle re-audit."""

    def __init__(self, tailored: TailoredCVData):
        self.record = SimpleNamespace(
            id=uuid.uuid4(), user_id=None, profile_id=None, document_language="de",
            tailored_data=tailored.model_dump(), content_snapshot=build_content_snapshot(tailored),
            section_overrides={}, ats_report={"checks": [], "keywords": {}}, truthfulness_report=None,
            letter_data=None,
        )
        self.audits = 0

    def current(self) -> TailoredCVData:
        r = self.record
        return apply_overrides_to_tailored(TailoredCVData.model_validate(r.tailored_data),
                                           r.content_snapshot, r.section_overrides)

    async def audit(self):
        self.audits += 1
        self.record.truthfulness_report = await build_self_audit_report(
            VAULT, tailored_data=self.current().model_dump(mode="json"), document_language="de")

    def install(self, monkeypatch):
        import applire.services.review_actions as ra

        async def load_document(kind, doc_id, db, *, user_id=None):
            return self.record

        async def patchable_sections(kind, record, db, *, user_id=None):
            out = []
            for p in record.content_snapshot["positions"]:
                sid = f"position::{p['id']}"
                out.append((sid, record.section_overrides.get(sid, "\n".join(p["bullets"]))))
            return out

        async def write_section(kind, record, section_id, content, db, *, user_id=None):
            record.section_overrides = {**record.section_overrides, section_id: content}

        async def reaudit(kind, record, db, *, user_id=None):
            await self.audit()

        async def _document_language(kind, record, db):
            return "de"

        async def _profile_json(record, db):
            return VAULT

        monkeypatch.setattr(ra, "load_document", load_document)
        monkeypatch.setattr(ra, "patchable_sections", patchable_sections)
        monkeypatch.setattr(ra, "write_section", write_section)
        monkeypatch.setattr(ra, "reaudit", reaudit)
        monkeypatch.setattr(ra, "_document_language", _document_language)
        monkeypatch.setattr(da, "_profile_json", _profile_json)


class _Model:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    async def acomplete(self, prompt, **kw):
        self.prompts.append((prompt, kw))
        return self.answer(prompt) if callable(self.answer) else self.answer


def _key(twin, verdict):
    for c in twin.record.truthfulness_report["claims"]:
        if c["verdict"]["verdict"] == verdict:
            return rs.finding_key("oracle", c["claim"]["text"])
    raise AssertionError(f"no {verdict} claim: {[c['verdict'] for c in twin.record.truthfulness_report['claims']]}")


def _listed(twin, key):
    from applire.services.review_actions import findings_of

    return rs.find_listed(findings_of(twin.record), key) is not None


@pytest.mark.asyncio
async def test_wohin_gehoert_das_the_owning_station_is_the_answer_and_the_move_clears_it(monkeypatch):
    twin = _Twin(_tailored(["Monatsabschlüsse nach HGB vorbereitet", WRONG_PLACE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "misattributed")
    stations = await da.owning_stations("cv", twin.record.id, key, None)
    b_pos = next(p for p in twin.record.content_snapshot["positions"] if p["work_id"] == B)
    assert [(s.station_id, s.experience_id, s.organization) for s in stations] == [
        (f"position::{b_pos['id']}", B, "Nord Spedition AG")
    ]
    model = _Model(WRONG_PLACE)  # the fact itself unchanged
    res = await da.move_sentence("cv", twin.record.id, key, stations[0].station_id, None, model)
    assert res.llm_calls == 1 and not res.still_listed and not _listed(twin, key)
    cur = twin.current()
    assert WRONG_PLACE not in cur.work_history[0].bullets and WRONG_PLACE in cur.work_history[1].bullets
    assert {c["section_id"] for c in res.changes} == {f"position::{p['id']}" for p in twin.record.content_snapshot["positions"]}
    assert "STATION:\nBuchhalterin, Nord Spedition AG" in model.prompts[0][0]


@pytest.mark.asyncio
async def test_a_station_that_does_not_own_the_evidence_is_refused(monkeypatch):
    twin = _Twin(_tailored(["Monatsabschlüsse nach HGB vorbereitet", WRONG_PLACE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "misattributed")
    a_pos = next(p for p in twin.record.content_snapshot["positions"] if p["work_id"] == A)
    with pytest.raises(da.ActionNotApplicable):
        await da.move_sentence("cv", twin.record.id, key, f"position::{a_pos['id']}", None, _Model("x"))


@pytest.mark.asyncio
async def test_stimmt_das_nein_removes_only_the_unbacked_figure(monkeypatch):
    twin = _Twin(_tailored([FIGURE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "unbacked")
    model = _Model("Monatsabschlüsse für die Gesellschaften nach HGB vorbereitet")
    res = await da.narrow_or_remove("cv", twin.record.id, key, None, model, mode="remove")
    assert not res.still_listed and not _listed(twin, key)
    assert twin.current().work_history[0].bullets == ["Monatsabschlüsse für die Gesellschaften nach HGB vorbereitet"]


@pytest.mark.asyncio
async def test_narrow_rewrites_to_what_the_profile_states_and_clears(monkeypatch):
    twin = _Twin(_tailored([FIGURE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "unbacked")
    model = _Model("Monatsabschlüsse nach HGB vorbereitet")
    res = await da.narrow_or_remove("cv", twin.record.id, key, None, model, mode="narrow")
    assert not res.still_listed
    assert "PROFILE EXCERPT:\nFinanzbuchhalterin, Muster Logistik GmbH" in model.prompts[0][0]


@pytest.mark.asyncio
async def test_narrow_that_adds_a_number_is_refused_and_writes_nothing(monkeypatch):
    twin = _Twin(_tailored([FIGURE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "unbacked")
    with pytest.raises(da.RewriteFailed):
        await da.narrow_or_remove("cv", twin.record.id, key, None,
                                  _Model("Monatsabschlüsse für 14 Gesellschaften nach HGB vorbereitet"), mode="narrow")
    assert twin.record.section_overrides == {} and twin.audits == 1


@pytest.mark.asyncio
async def test_narrow_with_nothing_backed_takes_the_line_out(monkeypatch):
    twin = _Twin(_tailored(["Monatsabschlüsse nach HGB vorbereitet", FIGURE], []))
    twin.install(monkeypatch)
    await twin.audit()
    key = _key(twin, "unbacked")
    res = await da.narrow_or_remove("cv", twin.record.id, key, None, _Model("<EMPTY>"), mode="narrow")
    assert not res.still_listed
    assert twin.current().work_history[0].bullets == ["Monatsabschlüsse nach HGB vorbereitet"]


@pytest.mark.asyncio
async def test_an_unlisted_key_is_not_applicable(monkeypatch):
    twin = _Twin(_tailored(["Monatsabschlüsse nach HGB vorbereitet"], []))
    twin.install(monkeypatch)
    await twin.audit()
    with pytest.raises(da.ActionNotApplicable):
        await da.narrow_or_remove("cv", twin.record.id, "oracle:nicht da", None, _Model("x"), mode="narrow")
    with pytest.raises(da.ActionNotApplicable):
        await da.narrow_or_remove("cv", twin.record.id, "oracle:nicht da", None, _Model("x"), mode="as_goal")


@pytest.mark.asyncio
async def test_diese_stellen_uebersetzen_translates_only_the_foreign_items(monkeypatch):
    english = "Prepared the monthly closing for all entities under German GAAP"
    twin = _Twin(_tailored(["Monatsabschlüsse nach HGB vorbereitet", english], []))
    twin.install(monkeypatch)
    await twin.audit()
    model = _Model("Monatsabschlüsse für alle Gesellschaften nach HGB vorbereitet")
    res = await da.translate_spans("cv", twin.record.id, None, model)
    assert res.llm_calls == 1 and not res.still_listed
    assert twin.current().work_history[0].bullets == [
        "Monatsabschlüsse nach HGB vorbereitet", "Monatsabschlüsse für alle Gesellschaften nach HGB vorbereitet",
    ]
    assert "TRANSLATE" in model.prompts[0][0] and english in model.prompts[0][0]


@pytest.mark.asyncio
async def test_a_translation_that_changes_a_figure_is_refused(monkeypatch):
    english = "Prepared the monthly closing for 3 entities under German GAAP"
    twin = _Twin(_tailored([english], []))
    twin.install(monkeypatch)
    await twin.audit()
    with pytest.raises(da.RewriteFailed):
        await da.translate_spans("cv", twin.record.id, None, _Model("Monatsabschlüsse für 4 Gesellschaften"))


def test_splice_removes_a_bullet_line_cleanly():
    full = "eins\nzwei\ndrei"
    s = full.index("zwei")
    assert da._splice(full, s, s + 4, "") == "eins\ndrei"
    assert da._splice(full, 0, 4, "") == "zwei\ndrei"
    e = full.index("drei")
    assert da._splice(full, e, e + 4, "") == "eins\nzwei\n" or da._splice(full, e, e + 4, "").startswith("eins\nzwei")
