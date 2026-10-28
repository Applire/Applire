# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 T-A (UAT-2 fix round, WP-C) — every class-A resolver of the review
surface, and EACH answer, on the dead-end shapes: action endpoint → recompute →
the finding key is absent, or the declared residual.

Drives the REAL review router + ``services/review_actions.py`` over an
in-memory SQLite DB (the ``test_review_endpoints`` harness). Faked seams:

* ``review_actions.reaudit`` — the real one renders and audits; the fake
  applies the report the recompute would produce (``FakeReaudit``);
* WP-B's ``services/delivery_actions`` corrector calls — faked to do what the
  contract says (load through OUR ``load_document``, write through OUR
  ``write_section``, re-audit through OUR ``reaudit``), so the decision/undo
  plumbing and the B→C helper names are exercised for real;
* ``submit_testimony`` — canned ``TestimonyResult``.

The U-13 shape (founder, build 4): a letter/CV sentence the Oracle marks
``misattributed`` — the evidence EXISTS under another station — where the old
card offered testimony, the reconcile found nothing new and the card came back.
Here the answer is the station; the recompute must drop the key.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

import applire.services.delivery_actions as da
import applire.services.review_actions as ra
import applire.services.review_state as rs
from tests.unit.test_review_endpoints import (  # noqa: F401 — `db` is a fixture
    FakeReaudit,
    FakeRewrite,
    _ats_report,
    _canned_testimony,
    _client,
    db,
    seed_cv,
    seed_letter,
)

_SENTENCE = "Automatisierte Unit-, Build- und Integrationstests für 40 Microservices."
_ORACLE_KEY = rs.finding_key("oracle", _SENTENCE)


def _truth(text: str, verdict: str, detail_key: str, resolver: str, location: str = "work_history[0].bullets[0]") -> dict:
    """A truthfulness report with ONE flagged claim carrying the registry fields."""
    return {
        "version": "2",
        "document_kind": "cv",
        "claims": [{
            "claim": {"text": text, "location": location, "kind": "achievement"},
            "verdict": {
                "verdict": verdict, "checker": "attribution",
                "evidence": [{"kind": "profile_path", "ref": "work_experience[1].achievements[0]",
                              "excerpt": text}],
                "detail": "x", "detail_key": detail_key, "detail_params": {},
                "delivery_class": "A", "resolver": resolver,
            },
        }],
        "counts": {},
    }


def _misattributed() -> dict:
    return _truth(_SENTENCE, "misattributed", "misattributed", "ask_station")


def _clean_truth() -> dict:
    return {"version": "2", "document_kind": "cv", "claims": [], "counts": {}}


class FakeDelivery:
    """WP-B's corrector actions, faithful to the contract: the caller holds the
    lock; the action loads the document, writes ONE section through
    ``review_actions.write_section`` and re-audits through ``review_actions.reaudit``."""

    def __init__(self, section_id: str, after: str, *, stations=None, raise_exc=None):
        self.section_id = section_id
        self.after = after
        self.stations = stations or []
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    async def owning_stations(self, kind, doc_id, finding_key, db, *, user_id=None):
        self.calls.append(("owning_stations", finding_key))
        if self.raise_exc:
            raise self.raise_exc
        return self.stations

    async def _edit(self, name, kind, doc_id, finding_key, db, user_id):
        self.calls.append((name, finding_key))
        if self.raise_exc:
            raise self.raise_exc
        record = await ra.load_document(kind, doc_id, db, user_id=user_id)
        before = dict(await ra.patchable_sections(kind, record, db))[self.section_id]
        await ra.write_section(kind, record, self.section_id, self.after, db)
        await ra.reaudit(kind, record, db)
        still = rs.find_listed(ra.findings_of(record), finding_key) is not None if finding_key else False
        return da.DeliveryActionResult(
            record=record, report=record.ats_report, truthfulness=record.truthfulness_report,
            changes=[{"section_id": self.section_id, "before": before, "after": self.after}],
            still_listed=still, llm_calls=1,
        )

    async def move_sentence(self, kind, doc_id, finding_key, station_id, db, provider, *, user_id=None):
        if station_id not in {s.station_id for s in self.stations}:
            raise da.ActionNotApplicable(f"{station_id!r} owns no evidence of {finding_key!r}")
        return await self._edit("move_sentence", kind, doc_id, finding_key, db, user_id)

    async def narrow_or_remove(self, kind, doc_id, finding_key, db, provider, *, mode="remove", user_id=None):
        return await self._edit(f"narrow_or_remove:{mode}", kind, doc_id, finding_key, db, user_id)

    async def translate_spans(self, kind, doc_id, db, provider, *, spans=None, user_id=None):
        return await self._edit("translate_spans", kind, doc_id, None, db, user_id)

    def patches(self):
        return [
            patch.object(da, "owning_stations", self.owning_stations),
            patch.object(da, "move_sentence", self.move_sentence),
            patch.object(da, "narrow_or_remove", self.narrow_or_remove),
            patch.object(da, "translate_spans", self.translate_spans),
        ]


def _enter(patches):
    for p in patches:
        p.start()


def _exit(patches):
    for p in patches:
        p.stop()


_STATION = da.StationOption(
    station_id="position::kranich", experience_id="w-kranich",
    title="Backend-Entwicklerin", organization="Kranich Systems AG",
)


# ── ask_station: "Wohin gehört das?" ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_station_stations_lists_the_owning_stations(db):
    cv_id = await seed_cv(db, position_bullets=[_SENTENCE], ats_report=_ats_report("cv", []),
                          truthfulness_report=_misattributed())
    fake = FakeDelivery("position::x", "", stations=[_STATION])
    ps = fake.patches()
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/stations", json={"finding_key": _ORACLE_KEY})
    finally:
        _exit(ps)
    assert r.status_code == 200, r.text
    assert r.json()["stations"] == [{
        "station_id": "position::kranich", "experience_id": "w-kranich",
        "title": "Backend-Entwicklerin", "organization": "Kranich Systems AG",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_ask_station_answer_station_moves_and_the_recompute_drops_the_key(db, kind):
    """U-13 shape: misattributed with the evidence under another station. The
    station answer → corrector move → re-audit → the key is gone, decision
    ``moved`` with the undo text."""
    if kind == "cv":
        doc_id = await seed_cv(db, position_bullets=[_SENTENCE], ats_report=_ats_report("cv", []),
                               truthfulness_report=_misattributed())
        section, prefix = "introduction", "/api/cv"
    else:
        doc_id = await seed_letter(db, paragraphs=[_SENTENCE], ats_report=_ats_report("cover_letter", []),
                                   truthfulness_report=_misattributed())
        section, prefix = "body", "/api/cover-letter"
    fake = FakeDelivery(section, "Bei Kranich Systems: automatisierte Tests.", stations=[_STATION])
    reaudit = FakeReaudit([(_ats_report(kind, []), _clean_truth())])
    ps = fake.patches() + [patch.object(ra, "reaudit", new=reaudit)]
    _enter(ps)
    try:
        r = _client(db).post(f"{prefix}/{doc_id}/review/move",
                             json={"finding_key": _ORACLE_KEY, "station_id": "position::kranich"})
    finally:
        _exit(ps)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["still_listed"] is False
    assert reaudit.calls == 1
    assert ("move_sentence", _ORACLE_KEY) in fake.calls
    # recompute: the finding key is absent from the live derivation
    record = await ra.load_document(kind, doc_id, db)
    derived = rs.derive_review(record.review_state, record.ats_report, record.truthfulness_report)
    assert derived["open_count"] == 0
    d = rs.get_decision(rs.load_state(record.review_state), _ORACLE_KEY)
    assert d["action"] == "moved" and d["undo"]["sections"][0]["section_id"] == section


@pytest.mark.asyncio
async def test_ask_station_answer_remove_takes_it_out_and_the_recompute_drops_the_key(db):
    """"Entfernen" on the same card = the existing take-out."""
    cv_id = await seed_cv(db, introduction=_SENTENCE, ats_report=_ats_report("cv", []),
                          truthfulness_report=_misattributed())
    rewrite = FakeRewrite({"introduction": (True, "Erfahrene Entwicklerin.")})
    reaudit = FakeReaudit([(_ats_report("cv", []), _clean_truth())])
    with patch.object(ra, "_rewriter", lambda: rewrite), patch.object(ra, "reaudit", new=reaudit):
        r = _client(db).post(f"/api/cv/{cv_id}/review/take-out", json={"finding_key": _ORACLE_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False


@pytest.mark.asyncio
async def test_ask_station_foreign_station_is_409_and_writes_nothing(db):
    cv_id = await seed_cv(db, position_bullets=[_SENTENCE], ats_report=_ats_report("cv", []),
                          truthfulness_report=_misattributed())
    fake = FakeDelivery("introduction", "changed", stations=[_STATION])
    reaudit = FakeReaudit()
    ps = fake.patches() + [patch.object(ra, "reaudit", new=reaudit)]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/move",
                             json={"finding_key": _ORACLE_KEY, "station_id": "position::nope"})
    finally:
        _exit(ps)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "action_not_applicable"
    assert reaudit.calls == 0
    record = await ra.load_document("cv", cv_id, db)
    assert not (record.review_state or {}).get("decisions")


@pytest.mark.asyncio
async def test_resolver_before_wp_b_lands_is_503_not_a_crash(db):
    """B's stubs raise NotImplementedError until integration: the surface gets a
    clean 503 and nothing is written."""
    cv_id = await seed_cv(db, position_bullets=[_SENTENCE], ats_report=_ats_report("cv", []),
                          truthfulness_report=_misattributed())
    fake = FakeDelivery("introduction", "x", raise_exc=NotImplementedError())
    ps = fake.patches()
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/stations", json={"finding_key": _ORACLE_KEY})
    finally:
        _exit(ps)
    assert r.status_code == 503 and r.json()["detail"]["error"] == "resolver_unavailable"


@pytest.mark.asyncio
async def test_rewrite_failed_is_502_and_records_nothing(db):
    cv_id = await seed_cv(db, position_bullets=[_SENTENCE], ats_report=_ats_report("cv", []),
                          truthfulness_report=_misattributed())
    fake = FakeDelivery("introduction", "x", stations=[_STATION], raise_exc=da.RewriteFailed("fact check"))

    async def owning(*a, **k):
        return [_STATION]

    ps = fake.patches() + [patch.object(da, "owning_stations", owning)]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/move",
                             json={"finding_key": _ORACLE_KEY, "station_id": "position::kranich"})
    finally:
        _exit(ps)
    assert r.status_code == 502
    record = await ra.load_document("cv", cv_id, db)
    assert not (record.review_state or {}).get("decisions")


# ── ask_true: "Stimmt das?" ──────────────────────────────────────────────────

_CRED = "Kubernetes-Zertifizierung (CKA)"
_CRED_KEY = rs.finding_key("oracle", _CRED)


def _unbacked() -> dict:
    return _truth(_CRED, "unbacked", "credentialUnbacked", "ask_true", location="certifications[0]")


@pytest.mark.asyncio
async def test_ask_true_yes_with_evidence_clears_on_recompute(db):
    cv_id = await seed_cv(db, introduction=_CRED, ats_report=_ats_report("cv", []), truthfulness_report=_unbacked())
    reaudit = FakeReaudit([(_ats_report("cv", []), _clean_truth())])
    with patch.object(ra, "reaudit", new=reaudit), patch(
        "applire.services.profile.reconcile.testimony_bridge.submit_testimony"
    ) as submit:
        submit.return_value = _canned_testimony("applied")
        r = _client(db).post(f"/api/cv/{cv_id}/review/add-evidence",
                             json={"finding_key": _CRED_KEY, "text": "Bei Nordwind, 2023 abgelegt."})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["still_listed"] is False and data["remove_only"] is False
    record = await ra.load_document("cv", cv_id, db)
    assert rs.derive_review(record.review_state, record.ats_report, record.truthfulness_report)["open_count"] == 0


@pytest.mark.asyncio
async def test_ask_true_yes_without_vault_change_leaves_only_remove(db):
    """The dead end U-13 fell into: the testimony changes nothing. The finding
    stays OPEN (no label closure) and the card's only exit is "Entfernen" —
    persisted, so a reload shows the same end state."""
    cv_id = await seed_cv(db, introduction=_CRED, ats_report=_ats_report("cv", []), truthfulness_report=_unbacked())
    reaudit = FakeReaudit([(_ats_report("cv", []), _unbacked())])
    with patch.object(ra, "reaudit", new=reaudit), patch(
        "applire.services.profile.reconcile.testimony_bridge.submit_testimony"
    ) as submit:
        submit.return_value = _canned_testimony("no_change")
        r = _client(db).post(f"/api/cv/{cv_id}/review/add-evidence",
                             json={"finding_key": _CRED_KEY, "text": "Stimmt schon."})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["still_listed"] is True and data["remove_only"] is True
    record = await ra.load_document("cv", cv_id, db)
    derived = rs.derive_review(record.review_state, record.ats_report, record.truthfulness_report)
    assert derived["open_count"] == 1 and derived["decided_count"] == 0
    assert ra.remove_only(record, _CRED_KEY) is True


@pytest.mark.asyncio
async def test_ask_true_then_remove_clears_on_recompute(db):
    """"Nein, entfernen" — or the remove-only end state — is the take-out."""
    cv_id = await seed_cv(db, introduction=_CRED, ats_report=_ats_report("cv", []), truthfulness_report=_unbacked(),
                          review_state=rs.with_decision(rs.load_state(None), _CRED_KEY, _CRED, "no_change"))
    rewrite = FakeRewrite({"introduction": (True, "Erfahrene Entwicklerin.")})
    reaudit = FakeReaudit([(_ats_report("cv", []), _clean_truth())])
    with patch.object(ra, "_rewriter", lambda: rewrite), patch.object(ra, "reaudit", new=reaudit):
        r = _client(db).post(f"/api/cv/{cv_id}/review/take-out", json={"finding_key": _CRED_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    record = await ra.load_document("cv", cv_id, db)
    assert rs.get_decision(rs.load_state(record.review_state), _CRED_KEY)["action"] == "taken_out"


def test_supported_no_longer_closes_a_listed_finding():
    """ADR-099 cl. 6: "Stimmt" without a vault change no longer closes a row
    (D2-5's label closure retired). Mutation guard: re-adding ``supported`` to
    ``CLOSES_LISTED`` makes this fail."""
    truth = _unbacked()
    state = rs.with_decision(rs.load_state(None), _CRED_KEY, _CRED, "supported", receipt=[{"path": "p", "text": "t"}])
    out = rs.derive_review(state, _ats_report("cv", []), truth)
    assert out["open_count"] == 1 and out["decided_count"] == 0


def test_no_change_marker_never_labels_a_gone_finding_decided():
    state = rs.with_decision(rs.load_state(None), _CRED_KEY, _CRED, "no_change")
    out = rs.derive_review(state, _ats_report("cv", []), _clean_truth())
    assert out["open_count"] == 0 and out["decided_count"] == 0


# ── ask_achieved: "Ist das erreicht?" ────────────────────────────────────────

_GOAL = "Steigerte die Effizienz der Plattform-Teams um über 60 %."
_GOAL_KEY = rs.finding_key("oracle", _GOAL)


def _inflated() -> dict:
    return _truth(_GOAL, "inflated", "inflatedAspirational", "ask_achieved")


@pytest.mark.asyncio
async def test_ask_achieved_no_rewrites_as_goal_and_clears_on_recompute(db):
    cv_id = await seed_cv(db, introduction=_GOAL, ats_report=_ats_report("cv", []), truthfulness_report=_inflated())
    fake = FakeDelivery("introduction", "Ziel: die Effizienz der Plattform-Teams um über 60 % steigern.")
    reaudit = FakeReaudit([(_ats_report("cv", []), _clean_truth())])
    ps = fake.patches() + [patch.object(ra, "reaudit", new=reaudit)]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/as-goal", json={"finding_key": _GOAL_KEY})
    finally:
        _exit(ps)
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    assert ("narrow_or_remove:as_goal", _GOAL_KEY) in fake.calls
    record = await ra.load_document("cv", cv_id, db)
    assert rs.get_decision(rs.load_state(record.review_state), _GOAL_KEY)["action"] == "as_goal"
    # …and undo restores the sentence
    with patch.object(ra, "reaudit", new=FakeReaudit([(_ats_report("cv", []), _inflated())])):
        u = _client(db).post(f"/api/cv/{cv_id}/review/undo", json={"finding_key": _GOAL_KEY})
    assert u.status_code == 200, u.text
    record = await ra.load_document("cv", cv_id, db)
    assert record.section_overrides["introduction"] == _GOAL


@pytest.mark.asyncio
async def test_ask_achieved_yes_with_evidence_clears_on_recompute(db):
    cv_id = await seed_cv(db, introduction=_GOAL, ats_report=_ats_report("cv", []), truthfulness_report=_inflated())
    reaudit = FakeReaudit([(_ats_report("cv", []), _clean_truth())])
    with patch.object(ra, "reaudit", new=reaudit), patch(
        "applire.services.profile.reconcile.testimony_bridge.submit_testimony"
    ) as submit:
        submit.return_value = _canned_testimony("applied")
        r = _client(db).post(f"/api/cv/{cv_id}/review/add-evidence",
                             json={"finding_key": _GOAL_KEY, "text": "Erreicht 2024, gemessen an der Durchlaufzeit."})
    assert r.status_code == 200 and r.json()["still_listed"] is False


# ── translate: "Diese Stellen übersetzen" ────────────────────────────────────


def _lang_report(kind: str, failing: bool) -> dict:
    rep = _ats_report(kind, [])
    rep["checks"] = [{
        "id": "document-language", "label": "Document language", "status": "fail" if failing else "pass",
        "severity": "warning", "details": "", "delivery_class": "A" if failing else None,
        "resolver": "translate" if failing else None,
    }]
    return rep


@pytest.mark.asyncio
async def test_translate_rewrites_and_the_check_passes_on_recompute_then_undo(db):
    cv_id = await seed_cv(db, introduction="Drove the cloud cost review.", ats_report=_lang_report("cv", True))
    fake = FakeDelivery("introduction", "Leitete die Überprüfung der Cloud-Kosten.")
    reaudit = FakeReaudit([(_lang_report("cv", False), None)])
    ps = fake.patches() + [patch.object(ra, "reaudit", new=reaudit)]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cv/{cv_id}/review/translate", json={})
    finally:
        _exit(ps)
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    record = await ra.load_document("cv", cv_id, db)
    d = rs.get_decision(rs.load_state(record.review_state), ra.SEND_LANGUAGE_KEY)
    assert d["action"] == "translated"
    # a send decision never becomes a group-1 row
    assert rs.derive_review(record.review_state, record.ats_report, None)["decided_count"] == 0
    with patch.object(ra, "reaudit", new=FakeReaudit([(_lang_report("cv", True), None)])):
        u = _client(db).post(f"/api/cv/{cv_id}/review/undo", json={"finding_key": ra.SEND_LANGUAGE_KEY})
    assert u.status_code == 200, u.text
    record = await ra.load_document("cv", cv_id, db)
    assert record.section_overrides["introduction"] == "Drove the cloud cost review."


@pytest.mark.asyncio
async def test_translate_when_the_check_passes_is_409(db):
    cv_id = await seed_cv(db, ats_report=_lang_report("cv", False))
    r = _client(db).post(f"/api/cv/{cv_id}/review/translate", json={})
    assert r.status_code == 409


# ── recheck: "Jetzt prüfen" (class U) ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_recheck_reruns_the_full_report_on_this_version(db, kind):
    """A producer that did not run (no truthfulness report) → "Jetzt prüfen" →
    the re-audit runs once on the same version and the answer carries it."""
    if kind == "cv":
        doc_id = await seed_cv(db, ats_report=_ats_report("cv", []), truthfulness_report=None)
        prefix = "/api/cv"
    else:
        doc_id = await seed_letter(db, ats_report=_ats_report("cover_letter", []), truthfulness_report=None)
        prefix = "/api/cover-letter"
    reaudit = FakeReaudit([(_ats_report(kind, []), _clean_truth())])
    with patch.object(ra, "reaudit", new=reaudit):
        r = _client(db).post(f"{prefix}/{doc_id}/review/recheck")
    assert r.status_code == 200, r.text
    assert reaudit.calls == 1
    assert r.json()["truthfulness"] is not None


# ── removal line: "Doch, stimmt – wo?" ───────────────────────────────────────


def _with_removal(kind: str, text: str) -> dict:
    rep = _ats_report(kind, [])
    rep["delivery"] = {"loop_clean": True, "removals": [
        {"text": text, "section": "work_history", "kind": "oracle:unbacked", "pinned": False},
    ]}
    return rep


@pytest.mark.asyncio
async def test_removal_evidence_records_the_testimony_per_item(db):
    removed = "Mentorin für 15 Nachwuchskräfte."
    cv_id = await seed_cv(db, ats_report=_with_removal("cv", removed))
    with patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as submit:
        submit.return_value = _canned_testimony("applied")
        r = _client(db).post(f"/api/cv/{cv_id}/review/removal-evidence",
                             json={"removal_text": removed, "text": "Bei Kranich, Trainee-Programm 2019–2021."})
    assert r.status_code == 200, r.text
    answers = r.json()["review_state"]["removal_answers"]
    assert answers[rs.norm_quote(removed)]["status"] == "applied"
    submit.assert_called_once()


@pytest.mark.asyncio
async def test_removal_evidence_for_an_unlisted_removal_is_409(db):
    cv_id = await seed_cv(db, ats_report=_with_removal("cv", "Etwas anderes."))
    with patch("applire.services.profile.reconcile.testimony_bridge.submit_testimony") as submit:
        r = _client(db).post(f"/api/cv/{cv_id}/review/removal-evidence",
                             json={"removal_text": "Nicht da.", "text": "x"})
    assert r.status_code == 409
    submit.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_document_is_404_on_every_resolver(db):
    missing = uuid.uuid4()
    client = _client(db)
    for path, body in (
        ("stations", {"finding_key": _ORACLE_KEY}),
        ("move", {"finding_key": _ORACLE_KEY, "station_id": "s"}),
        ("as-goal", {"finding_key": _GOAL_KEY}),
        ("translate", {}),
        ("recheck", None),
        ("removal-evidence", {"removal_text": "a", "text": "b"}),
    ):
        r = client.post(f"/api/cv/{missing}/review/{path}", json=body) if body is not None else client.post(
            f"/api/cv/{missing}/review/{path}")
        assert r.status_code == 404, (path, r.status_code, r.text)


# ── page_length (letter, INTERFACE B-2): "kürzen" / "behalten" ───────────────


def _letter_pages(over: bool) -> dict:
    rep = _ats_report("cover_letter", [])
    rep["checks"] = [{
        "id": "page-length", "label": "Page length", "status": "fail" if over else "pass",
        "severity": "warning", "details": "", "details_key": "page-length-letter" if over else "page-length-kept",
        "details_params": {"pages": 2, "letterPages": 1} if over else {},
        "delivery_class": "A" if over else "S", "resolver": "page_length" if over else None,
    }]
    return rep


@pytest.mark.asyncio
async def test_letter_shorten_rewrites_and_the_card_leaves_on_recompute_then_undo(db):
    paragraphs = ["Erster Absatz.", "Zweiter, sehr langer Absatz.", "Dritter Absatz."]
    cl_id = await seed_letter(db, paragraphs=paragraphs, ats_report=_letter_pages(True))
    fake = FakeDelivery("body", "Erster Absatz.\n\nDritter Absatz.")

    async def shorten_letter(kind, doc_id, db_, provider, *, user_id=None):
        return await fake._edit("shorten_letter", kind, doc_id, None, db_, user_id)

    reaudit = FakeReaudit([(_letter_pages(False), None)])
    ps = [patch.object(da, "shorten_letter", shorten_letter, create=True), patch.object(ra, "reaudit", new=reaudit)]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/shorten")
    finally:
        _exit(ps)
    assert r.status_code == 200, r.text
    assert r.json()["still_listed"] is False
    record = await ra.load_document("cover_letter", cl_id, db)
    assert rs.get_decision(rs.load_state(record.review_state), ra.PAGE_LENGTH_KEY)["action"] == "shortened"
    with patch.object(ra, "reaudit", new=FakeReaudit([(_letter_pages(True), None)])):
        u = _client(db).post(f"/api/cover-letter/{cl_id}/review/undo", json={"finding_key": ra.PAGE_LENGTH_KEY})
    assert u.status_code == 200, u.text


@pytest.mark.asyncio
async def test_letter_shorten_still_over_keeps_the_card(db):
    cl_id = await seed_letter(db, paragraphs=["A.", "B."], ats_report=_letter_pages(True))
    fake = FakeDelivery("body", "A.")

    async def shorten_letter(kind, doc_id, db_, provider, *, user_id=None):
        return await fake._edit("shorten_letter", kind, doc_id, None, db_, user_id)

    ps = [patch.object(da, "shorten_letter", shorten_letter, create=True),
          patch.object(ra, "reaudit", new=FakeReaudit([(_letter_pages(True), None)]))]
    _enter(ps)
    try:
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/shorten")
    finally:
        _exit(ps)
    assert r.status_code == 200 and r.json()["still_listed"] is True


@pytest.mark.asyncio
async def test_letter_keep_length_records_kept_and_reaudits(db):
    cl_id = await seed_letter(db, paragraphs=["A."], ats_report=_letter_pages(True))
    reaudit = FakeReaudit([(_letter_pages(False), None)])  # B's audit decides the row on `kept`
    with patch.object(ra, "reaudit", new=reaudit):
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/keep-length")
    assert r.status_code == 200, r.text
    assert reaudit.calls == 1
    record = await ra.load_document("cover_letter", cl_id, db)
    assert rs.get_decision(rs.load_state(record.review_state), ra.PAGE_LENGTH_KEY)["action"] == "kept"


@pytest.mark.asyncio
async def test_page_card_actions_on_the_cv_are_409(db):
    cv_id = await seed_cv(db, ats_report=_ats_report("cv", []))
    assert _client(db).post(f"/api/cv/{cv_id}/review/keep-length").status_code == 409
    assert _client(db).post(f"/api/cv/{cv_id}/review/shorten").status_code == 409


@pytest.mark.asyncio
async def test_page_card_actions_on_a_letter_in_target_are_409(db):
    cl_id = await seed_letter(db, paragraphs=["A."], ats_report=_letter_pages(False))
    assert _client(db).post(f"/api/cover-letter/{cl_id}/review/keep-length").status_code == 409


@pytest.mark.asyncio
async def test_letter_shorten_before_wp_b_lands_is_503(db):
    cl_id = await seed_letter(db, paragraphs=["A."], ats_report=_letter_pages(True))
    if hasattr(da, "shorten_letter"):
        with patch.object(da, "shorten_letter", new=None):
            r = _client(db).post(f"/api/cover-letter/{cl_id}/review/shorten")
    else:
        r = _client(db).post(f"/api/cover-letter/{cl_id}/review/shorten")
    assert r.status_code == 503
