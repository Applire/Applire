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

"""US293 — structured edits keep the write-seam contract (E055, ADR-063).

Two claims, two instruments:

**1. Receipt parity.** A structured edit through today's editors produces the
SAME enrichment-trail receipt as the equivalent pre-epic whole-section PATCH
(ADR-063 amended 2026-08-09 clause 8 — one `FieldChange` per entry that
appeared, changed or disappeared). The pre-epic form was recorded ONCE from
main-before-US290 (`1b410de9`) into ``tests/files/us293_receipt_parity/``;
this file drives BOTH doors (REST `PATCH /api/profile/{section}`, MCP
`update_profile`) with today's editor payloads — lists that round-trip ids,
merge-patches of the changed keys — and pins the receipt to the recording.
`projects` had no pre-epic door (422, recorded), so it is pinned against the
section-generic form its list siblings share.

**2. Write witness.** Every `profile_json` write that happens during a door
call is made by `applire/services/profile/commit.py`, and made while holding
the clause-6 token. The witness is a test-local listener on the same `set`
event the ADR-063 clause-6 guard uses, attributing each write to the frame
that assigned. The guard alone cannot tell "routed through `commit_ops`" from
"a twelfth writer holding the token" — the witness can, which is what makes
the mutation "replace the `commit_ops` call with a tokened direct assignment
in `patch_profile_section`" turn these tests red (JF-F-H0.1 (D)).

Plus JF-F-H0.4, provoked at unit tier for the first time: the committer's
invariant-8 reload gate answers a normal 200 with the untouched vault.

Fixture-gated? No — the fixture is committed; a missing fixture is a failure,
not a skip (feedback_fixture_gated_tests_are_not_gates).
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
import pytest_asyncio

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_RECORD_PY = Path(__file__).parent.parent / "files" / "us293_receipt_parity" / "record.py"


def _load_shared():
    spec = importlib.util.spec_from_file_location("us293_receipt_parity_record", _RECORD_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


rec = _load_shared()
FIXTURE = rec.load_fixture()

LIST_SECTIONS = sorted(c["section"] for c in rec.CASES if c["kind"] == "list" and c["op"] == "add")
OBJECT_SECTIONS = sorted(c["section"] for c in rec.CASES if c["kind"] == "object" and c["op"] == "add")
RECORDED_CASES = [c for c in rec.CASES if FIXTURE["cases"][c["id"]]["status"] == 200]
REFUSED_CASES = [c for c in rec.CASES if FIXTURE["cases"][c["id"]]["status"] != 200]
_COMMITTER = re.compile(r"/applire/services/profile/commit\.py:\d+$")


# ── Harness (a file-backed vault, read back over a separate connection) ───────


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    engine, factory = await rec._fresh_db(tmp_path / "vault.sqlite")
    yield engine, factory
    await engine.dispose()


async def _patch(factory, section, body, basis_updated_at=None):
    """The REST door — the route function itself, as the profile page calls it."""
    from applire.routers.profile import patch_section

    async with factory() as request_session:
        return await patch_section(section, rec.fake_request(body), request_session, None, None, basis_updated_at)


def _mcp_db(factory):
    @asynccontextmanager
    async def _cm():
        async with factory() as session:
            yield session

    return _cm


async def _mcp_update(factory, monkeypatch, section, body):
    """The agent door — the MCP tool function itself."""
    import applire.mcp.server as server

    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    monkeypatch.setattr(server, "get_provider", lambda: None)
    return await server.update_profile(section=section, data=body)


def _editor_body(case):
    return rec.editor_body(case, rec.loaded_section(rec.SEED_PROFILE, case["section"]))


def _latest_receipt(stored: dict) -> dict:
    history = stored["metadata"]["enrichment_history"]
    assert history, "the door left no enrichment record — the write did not go through commit_ops"
    return history[-1]


# ── The fixture's own claims (test the fixture too) ───────────────────────────


def test_the_fixture_was_recorded_from_the_pre_epic_commit_with_this_seed_and_case_table():
    """The pin compares against a recording — prove the recording is of the
    pre-epic tree and of the SAME inputs this file uses. Editing the seed or
    the case table without re-recording fails here, by name, instead of as
    an unexplained value mismatch further down."""
    assert FIXTURE["recorded_from"]["commit"] == rec.PRE_EPIC_COMMIT
    assert FIXTURE["seed_sha256"] == rec.seed_sha256()
    assert FIXTURE["cases_sha256"] == rec.cases_sha256()
    assert set(FIXTURE["recorded_from"]["sha256"]) == set(rec.PROVENANCE_FILES)


def test_the_fixture_covers_the_write_doors_section_vocabulary_in_both_directions():
    """Refute the scope list against the registry, both ways
    (feedback_refute_scope_lists_against_the_registry): a section the doors
    accept without a recorded receipt form, or a recorded section the doors
    no longer accept, is a red test — not a silent gap."""
    from applire.schemas.profile import OBJECT_SECTIONS as REGISTRY_OBJECT
    from applire.schemas.profile import VAULT_SECTIONS

    recorded = {c["section"] for c in FIXTURE["cases"].values()}
    assert recorded == set(VAULT_SECTIONS)
    accepted_pre_epic = {c["section"] for c in FIXTURE["cases"].values() if c["status"] == 200}
    refused_pre_epic = {c["section"]: c["status"] for c in FIXTURE["cases"].values() if c["status"] != 200}
    assert accepted_pre_epic == set(VAULT_SECTIONS) - {"projects"}
    assert refused_pre_epic == {"projects": 422}
    assert set(OBJECT_SECTIONS) == set(REGISTRY_OBJECT)
    assert set(LIST_SECTIONS) == set(VAULT_SECTIONS) - set(REGISTRY_OBJECT)


def test_every_section_has_every_op_recorded():
    for section in LIST_SECTIONS:
        assert {c["op"] for c in rec.CASES if c["section"] == section} == set(rec.LIST_OPS), section
    for section in OBJECT_SECTIONS:
        assert {c["op"] for c in rec.CASES if c["section"] == section} == set(rec.OBJECT_OPS), section


@pytest.mark.parametrize("case", RECORDED_CASES, ids=lambda c: c["id"])
def test_the_pre_epic_edit_left_exactly_one_named_receipt_change(case):
    """What the recording says the pre-epic door did: one `FieldChange` per
    edit, carrying the expected action and the entry's label (or the key)."""
    shape = FIXTURE["cases"][case["id"]]["shape"]
    assert shape["source"] == "manual_edit"
    assert len(shape["changes"]) == 1
    change = shape["changes"][0]
    assert change["section"] == case["section"]
    expected_action = {"add": "added", "update": "updated", "rename": "updated", "remove": "removed"}[case["op"]]
    assert change["action"] == expected_action
    assert change["rationale_key"] == f"manual_section_{expected_action}"
    if case["kind"] == "object":
        assert change["field"] == next(iter(case["set"]))
    if case["kind"] == "list" and case["op"] in ("update", "rename"):
        assert change["same_id"] is True, "an edited entry must keep its identity (ADR-077 clause 1)"


@pytest.mark.parametrize("case", RECORDED_CASES, ids=lambda c: c["id"])
def test_todays_editor_body_differs_from_the_pre_epic_body_where_the_epic_changed_the_shape(case):
    """The parity claim is only worth pinning because the INPUTS differ: an
    object edit is now a merge-patch of the changed keys, a new list entry is
    the editor's full draft. Prove the two bodies are not the same thing."""
    loaded = rec.loaded_section(rec.SEED_PROFILE, case["section"])
    pre_epic = rec.pre_epic_body(case, loaded)
    editor = rec.editor_body(case, loaded)
    assert pre_epic == FIXTURE["cases"][case["id"]]["pre_epic_body"]
    if case["kind"] == "object":
        assert set(editor) < set(pre_epic), "the merge-patch carries fewer keys than the whole object"
        if case["section"] == "personal_info":
            # The whole-object body carried the key the adapter now refuses.
            assert "photo_url" in pre_epic and "photo_url" not in editor
    elif case["op"] == "add" and case["section"] != "signature_stories":
        # The draft spells every form field (a superset of what was typed —
        # equal only where the form has no more fields than the typed entry,
        # e.g. languages) and never an id.
        assert set(editor[-1]) >= set(pre_epic[-1]), "the editor draft spells every form field"
        assert "id" not in editor[-1]
    else:
        assert editor == pre_epic, "update/rename/remove send the complete list on both eras"


def test_the_skeleton_abstracts_the_label_span_not_a_substring():
    """Found by the US293 browser pass: the summary editor's receipt names the
    key `de`, which is also a substring of "Added". A substring replace turned
    "Added de in professional_summary" into "Ad<label>d <label> in <section>"
    and the live receipt read as a different form. The abstraction must take
    the exact ` {field} in {section} ` span the committer writes."""
    record = {
        "source": "manual_edit",
        "changes": [
            {
                "section": "professional_summary",
                "field": "de",
                "action": "added",
                "rationale": "Added de in professional_summary (manual section edit).",
                "rationale_key": "manual_section_added",
                "old_value": None,
                "new_value": "Deutsch.",
            }
        ],
    }
    change = rec.skeleton(rec.receipt_shape(record))["changes"][0]
    assert change["rationale"] == "Added <label> in <section> (manual section edit)."


def test_the_list_sections_share_one_receipt_form_per_op():
    """The section-generic form `projects` is pinned against must BE one
    form: if any recorded list section diverged, the sibling comparison
    below would be ambiguous."""
    for op in rec.LIST_OPS:
        forms = {
            section: rec.skeleton(FIXTURE["cases"][f"{section}.{op}"]["shape"])
            for section in LIST_SECTIONS
            if FIXTURE["cases"][f"{section}.{op}"]["status"] == 200
        }
        distinct = {repr(sorted(f.items(), key=repr)) for f in forms.values()}
        assert len(distinct) == 1, f"{op}: {forms}"


# ── Claim 1: receipt parity through both doors ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case", RECORDED_CASES, ids=lambda c: c["id"])
async def test_the_rest_door_yields_the_pre_epic_receipt_for_todays_editor_payload(durable_db, case):
    engine, factory = durable_db
    profile_id = await rec._seed(factory)

    await _patch(factory, case["section"], _editor_body(case))

    stored = await rec._read_back(engine, profile_id)
    assert rec.receipt_shape(_latest_receipt(stored)) == FIXTURE["cases"][case["id"]]["shape"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", RECORDED_CASES, ids=lambda c: c["id"])
async def test_the_agent_door_yields_the_pre_epic_receipt_for_the_same_payload(durable_db, monkeypatch, case):
    """ADR-058 clause 2 — the same edit through the agent door leaves the
    same receipt; the two doors share one implementation, so this is a pin
    on that sharing, not a coincidence."""
    engine, factory = durable_db
    profile_id = await rec._seed(factory)

    await _mcp_update(factory, monkeypatch, case["section"], _editor_body(case))

    stored = await rec._read_back(engine, profile_id)
    assert rec.receipt_shape(_latest_receipt(stored)) == FIXTURE["cases"][case["id"]]["shape"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REFUSED_CASES, ids=lambda c: c["id"])
async def test_projects_was_refused_pre_epic_and_now_takes_the_list_siblings_form(durable_db, case):
    """`projects` joined `VAULT_SECTIONS` with US292 (ADR-063 amended
    2026-08-25 (1)). The recording holds the pre-epic 422; today's receipt
    must have the form every other list section already had — no new form
    came in with the new door."""
    assert case["section"] == "projects"
    assert FIXTURE["cases"][case["id"]]["status"] == 422
    engine, factory = durable_db
    profile_id = await rec._seed(factory)

    await _patch(factory, "projects", _editor_body(case))

    stored = await rec._read_back(engine, profile_id)
    today = rec.skeleton(rec.receipt_shape(_latest_receipt(stored)))
    siblings = [s for s in LIST_SECTIONS if s != "projects"]
    for sibling in siblings:
        assert today == rec.skeleton(FIXTURE["cases"][f"{sibling}.{case['op']}"]["shape"]), sibling


# ── Claim 2: the write witness ────────────────────────────────────────────────


@contextmanager
def _profile_write_witness():
    """Every `profile_json` assignment, attributed to the frame that made it.

    Rides the same attribute `set` event as the clause-6 guard and walks the
    same greenlet-aware frame chain (`_guard_frames`), skipping only
    SQLAlchemy's plumbing, the guard module, the declarative `<string>`
    constructor and the witness itself — so the first frame left IS the
    assigner: `commit.py` for a routed write, whoever else for a bypass.
    """
    from sqlalchemy import event

    from applire.models import profile as pm

    writes: list[dict] = []

    def _assigner() -> str:
        for frame in pm._guard_frames(sys._getframe(0)):
            if frame.f_code in (_assigner.__code__, _listen.__code__):
                continue
            filename = frame.f_code.co_filename.replace("\\", "/")
            if "/sqlalchemy/" in filename or filename.endswith("/applire/models/profile.py") or filename.startswith("<"):
                continue
            return f"{filename}:{frame.f_lineno}"
        return "<unknown>"

    def _listen(target, value, oldvalue, initiator):  # noqa: ANN001
        writes.append({"assigner": _assigner(), "token_held": pm._PROFILE_WRITE_TOKEN.get() is not None})

    event.listen(pm.MasterProfile.profile_json, "set", _listen, propagate=True)
    try:
        yield writes
    finally:
        event.remove(pm.MasterProfile.profile_json, "set", _listen)


def _assert_only_the_committer_wrote(writes: list[dict]) -> None:
    assert writes, "no vault write observed during the door call — the witness is not wired"
    for write in writes:
        assert _COMMITTER.search(write["assigner"]), f"vault written by {write['assigner']}, not the committer"
        assert write["token_held"], f"the committer wrote without the clause-6 token at {write['assigner']}"


def test_the_witness_attributes_a_write_to_the_frame_that_made_it():
    """Prove the instrument fires and names the assigner — a control that
    cannot fire proves nothing (feedback_control_that_never_fires). A tokened
    write from THIS file must be attributed to this file, not to the
    committer by construction."""
    from applire.models.profile import MasterProfile, authorized_profile_write

    with _profile_write_witness() as writes:
        with authorized_profile_write():
            record = MasterProfile(profile_json={"metadata": {}})  # keyword construction fires the setter
            record.profile_json = {"metadata": {"second": True}}  # plain assignment

    assert len(writes) == 2
    for write in writes:
        assert write["assigner"].endswith(f"{Path(__file__).name}:{write['assigner'].rsplit(':', 1)[1]}")
        assert Path(__file__).name in write["assigner"]
        assert write["token_held"] is True
        assert not _COMMITTER.search(write["assigner"])


@pytest.mark.asyncio
async def test_a_refused_section_edit_writes_nothing_at_all(durable_db):
    """The witness stays silent on a door call that must not write: the
    pure adapter refuses `metadata` before anything touches the vault."""
    from fastapi import HTTPException

    engine, factory = durable_db
    await rec._seed(factory)

    with _profile_write_witness() as writes:
        with pytest.raises(HTTPException) as exc:
            await _patch(factory, "metadata", {"denied_concepts": []})

    assert exc.value.status_code == 422
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id",
    ["work_experience.add", "skills.remove", "languages.rename", "projects.update", "personal_info.update", "professional_summary.remove"],
)
async def test_every_vault_write_during_a_rest_door_call_is_the_committers(durable_db, case_id):
    engine, factory = durable_db
    await rec._seed(factory)
    case = rec.CASE_BY_ID[case_id]

    with _profile_write_witness() as writes:
        await _patch(factory, case["section"], _editor_body(case))

    _assert_only_the_committer_wrote(writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["education.update", "certifications.add", "personal_info.remove"])
async def test_every_vault_write_during_an_agent_door_call_is_the_committers(durable_db, monkeypatch, case_id):
    engine, factory = durable_db
    await rec._seed(factory)
    case = rec.CASE_BY_ID[case_id]

    with _profile_write_witness() as writes:
        await _mcp_update(factory, monkeypatch, case["section"], _editor_body(case))

    _assert_only_the_committer_wrote(writes)


@pytest.mark.asyncio
async def test_a_stale_basis_is_refused_before_any_vault_write(durable_db):
    """The opt-in stale-edit refusal (ADR-063 amended 2026-08-25 (2)) fires
    BEFORE the committer assigns: a 409 leaves the witness empty."""
    from datetime import datetime, timedelta, timezone

    from fastapi import HTTPException

    engine, factory = durable_db
    await rec._seed(factory)
    case = rec.CASE_BY_ID["languages.update"]
    stale = datetime.now(timezone.utc) - timedelta(days=1)

    with _profile_write_witness() as writes:
        with pytest.raises(HTTPException) as exc:
            await _patch(factory, "languages", _editor_body(case), basis_updated_at=stale)

    assert exc.value.status_code == 409
    assert writes == []


# ── JF-F-H0.4: the reload gate answers 200 with the untouched vault ───────────


@pytest.mark.asyncio
async def test_h0_4_a_failed_reload_gate_answers_a_normal_200_with_the_untouched_vault(durable_db, monkeypatch, caplog):
    """The committer's invariant-8 gate: if the post-op profile fails its
    load round-trip, `commit_ops` persists NOTHING and returns the untouched
    profile — and `patch_profile_section` then answers with a normal
    `MasterProfileResponse`. Provoked here for the first time by making
    `_ensure_loadable` hand back its fallback. The facts this pins are the
    ones the frontend's H0.4 mismatch detector (`sectionSave.ts`) relies on:
    the response is a success, and the sent entry is NOT in it."""
    import applire.services.profile.commit as commit

    engine, factory = durable_db
    profile_id = await rec._seed(factory)
    before = await rec._read_back(engine, profile_id)
    case = rec.CASE_BY_ID["languages.add"]
    monkeypatch.setattr(commit, "_ensure_loadable", lambda candidate, fallback: fallback)
    caplog.set_level(logging.ERROR, logger="applire.services.profile.commit")

    response = await _patch(factory, "languages", _editor_body(case))

    # A success, by every signal the HTTP layer offers…
    assert [lang.language for lang in response.profile.languages] == ["German", "English"]
    assert "French" not in [lang.language for lang in response.profile.languages]
    # …and the vault did not move: no entry, no receipt.
    stored = await rec._read_back(engine, profile_id)
    assert [lang["language"] for lang in stored["languages"]] == [lang["language"] for lang in before["languages"]]
    assert stored["metadata"]["enrichment_history"] == []
    # The only signal is operator-side.
    assert any("failed its load round-trip" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_h0_4_the_gate_does_not_fire_on_an_ordinary_edit(durable_db, caplog):
    """The control's negative: on the happy path the gate is silent and the
    entry lands — so the previous test's silence is the gate, not the door."""
    engine, factory = durable_db
    profile_id = await rec._seed(factory)
    case = rec.CASE_BY_ID["languages.add"]
    caplog.set_level(logging.ERROR, logger="applire.services.profile.commit")

    response = await _patch(factory, "languages", _editor_body(case))

    assert "French" in [lang.language for lang in response.profile.languages]
    stored = await rec._read_back(engine, profile_id)
    assert "French" in [lang["language"] for lang in stored["languages"]]
    assert not any("failed its load round-trip" in r.getMessage() for r in caplog.records)
