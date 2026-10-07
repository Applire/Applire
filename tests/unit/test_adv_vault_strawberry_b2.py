# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial review of WP-V (Strawberry build 2): alternate names, recognised
entries and the "Nicht dasselbe" undo (#709 #715 #716 #717).

Each ``test_adv_vault_<n>_*`` test PROVES one finding and FAILS on the reviewed
tree (``a96007bb``). They are not fixes and change no product code. Findings,
severities and suggested fixes are written up in
``Documents/Runs/Strawberry/build-2/adv-vault/findings.md``.

The ``test_adv_vault_sound_*`` tests are probes that PASS on the reviewed
tree. They guard the parts the review found sound, including the cross-user
scoping of the new door, which the isolation suite does not enumerate because
it has no id in its path.

Synthetic data only. No provider calls: the import path runs with a stub
provider that returns a canned reconcile payload.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EducationEntry,
    EnrichmentRecord,
    Language,
    MasterProfileData,
    MatchReceipt,
    ProfileMetadata,
    SignatureStory,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.alias_writer import record_bound_aliases  # noqa: E402
from applire.services.profile.reconcile.apply import (  # noqa: E402
    MatchNotSeparableError,
    apply_ops,
)
from applire.services.profile.reconcile.import_bridge import reconcile_import  # noqa: E402
from applire.services.profile.reconcile.import_witness import compute_import_not_applied  # noqa: E402
from applire.services.profile.reconcile.ops import (  # noqa: E402
    MatchExisting,
    ReplaceSection,
    SeparateMatch,
    UpsertEducation,
    UpsertSkill,
    UpsertWork,
)


# ── helpers ───────────────────────────────────────────────────────────────────


class _Stub:
    """LLMProvider stub: every structured call returns the canned payload."""

    def __init__(self, payload):
        self.payload = payload

    async def aparse_json(self, prompt, **kwargs):
        return self.payload


def _stub(ops: list[dict]) -> _Stub:
    return _Stub({"ops": ops, "ambiguities": []})


def _import_once(vault: MasterProfileData, incoming: MasterProfileData, ops) -> MasterProfileData:
    """The import bridge's own deterministic sequence (import_bridge.py:322-385),
    plus the history record the committer appends: apply, record aliases, and
    keep the receipts on a new `EnrichmentRecord`."""
    applied = apply_ops(vault, ops, "cv_upload")
    record_bound_aliases(incoming, applied.profile, ops, applied.matched, applied.changes)
    history = list(applied.profile.metadata.enrichment_history) if applied.profile.metadata else []
    history.append(EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="cv_upload",
        matched=applied.matched, changes=applied.changes,
    ))
    applied.profile.metadata = (applied.profile.metadata or ProfileMetadata()).model_copy(
        update={"enrichment_history": history}
    )
    return applied.profile


def _labels(items) -> list[str]:
    return [i.label for i in items]


# ── finding 1: an education alias turns the guard's question into a silent merge ─


def _leipzig_vault(with_alias: bool = True) -> MasterProfileData:
    # The shape of V's own case-B fixture: a LinkedIn export writes the generic
    # German degree word "Diplom" and the field of study separately.
    return MasterProfileData(education=[EducationEntry(
        id="e1", institution="Universität Leipzig", degree="German Diploma",
        field="Computer Science", start_date="2002-10", end_date="2007-09",
        degree_aliases=["Diplom"] if with_alias else [],
    )])


_SECOND_DIPLOM = dict(
    institution="Universität Leipzig", degree="Diplom", field="Mathematik",
    start_date="2008-10", end_date="2012-03",
)


def test_adv_vault_1_control_without_the_alias_the_guard_asks():
    """Control (passes): the same second Diplom without the recorded alias is a
    question to the candidate, never a silent merge."""
    applied = apply_ops(_leipzig_vault(with_alias=False), [UpsertEducation(**_SECOND_DIPLOM)], "linkedin_import")
    assert len(applied.profile.education) == 1
    assert len(applied.pending_confirmations) == 1


def test_adv_vault_1_education_alias_overrides_the_guards_question():
    """ADR-046 am. 2026-10-07 cl. 4: "an alias never turns a question into a
    silent merge". The education applier runs the alias reading whenever
    ``verdict.match is None`` — including when the guard returned an AMBIGUOUS
    verdict — and replaces it with a match (apply.py:2858-2870). A second,
    different Diplom (another field, another decade) is folded into the first
    with no question."""
    applied = apply_ops(_leipzig_vault(), [UpsertEducation(**_SECOND_DIPLOM)], "linkedin_import")
    folded_silently = len(applied.profile.education) == 1 and not applied.pending_confirmations
    assert not folded_silently, (
        "second Diplom (Mathematik 2008-2012) merged into the 2002 Diplom with no question; "
        f"matched={[(m.basis, m.incoming) for m in applied.matched]}"
    )


def test_adv_vault_1_witness_carries_a_second_degree_behind_a_degree_alias():
    """The witness's alias reading (import_witness.py:485-508) compares names
    only. Education has no date in its key, so the second Diplom counts as
    "already in the vault" and nothing is listed as not carried."""
    incoming = MasterProfileData(education=[
        EducationEntry(institution="Universität Leipzig", degree="Diplom", field="Informatik",
                       start_date="2002-10", end_date="2007-09"),
        EducationEntry(**_SECOND_DIPLOM),
    ])
    items = compute_import_not_applied(incoming, _leipzig_vault(), [])
    # The 2002 Diplom IS the vault entry; the 2008 Diplom is not in the vault.
    assert len(items) == 1, f"the 2008 Diplom is not in the vault but reads as carried: {_labels(items)}"


def test_adv_vault_1_unstated_degree_at_an_aliased_institution_is_folded():
    """``unique_entry_by_names`` drops EMPTY incoming values (aliases.py:101),
    so an incoming entry with no degree matches on the institution alias alone.
    A doctorate or exchange semester written without a degree is folded into
    the M.Sc. entry. Without the alias it is added as its own entry."""
    def vault(alias: bool) -> MasterProfileData:
        return MasterProfileData(education=[EducationEntry(
            id="e1", institution="Technische Universität München", degree="M.Sc. Informatik",
            start_date="2014-10", end_date="2017-03",
            institution_aliases=["TU München"] if alias else [],
        )])

    op = UpsertEducation(institution="TU München", degree="", start_date="2018-01", end_date="2022-06")
    control = apply_ops(vault(False), [op], "cv_upload")
    assert len(control.profile.education) == 2  # baseline: its own entry

    applied = apply_ops(vault(True), [op], "cv_upload")
    assert len(applied.profile.education) == 2, (
        "the 2018-2022 entry was folded into the 2014 M.Sc. through the institution alias alone"
    )


def test_adv_vault_1_single_second_diplom_with_other_years_is_not_carried():
    """Ruling adv-vault-1 = B (MD2-15): for education the alias counts only when
    both stated start years (or end years) agree, or both are unknown. The
    remaining case after the incoming-side exactly-one rule: the 2008 Diplom
    imported ALONE (no 2002 Diplom beside it) still reads as carried."""
    incoming = MasterProfileData(education=[EducationEntry(**_SECOND_DIPLOM)])
    items = compute_import_not_applied(incoming, _leipzig_vault(), [])
    assert _labels(items) == ["Universität Leipzig / Diplom"], (
        f"2008-2012 Diplom carried through the 2002 entry's alias: {_labels(items)}"
    )
    applied = apply_ops(_leipzig_vault(), [UpsertEducation(**_SECOND_DIPLOM)], "linkedin_import")
    assert not any(m.basis == "alias" for m in applied.matched), "alias matched across other years"


def test_adv_vault_1_control_same_years_or_unknown_still_carry_under_b():
    """Control for ruling B (passes now, must keep passing after the fix): the
    alias still carries when the stated years agree, and when both sides
    state none."""
    same = MasterProfileData(education=[EducationEntry(
        institution="Universität Leipzig", degree="Diplom", start_date="2002-10", end_date="2007-09",
    )])
    assert compute_import_not_applied(same, _leipzig_vault(), []) == []
    undated_vault = MasterProfileData(education=[EducationEntry(
        id="e1", institution="Universität Leipzig", degree="German Diploma", degree_aliases=["Diplom"],
    )])
    undated = MasterProfileData(education=[EducationEntry(institution="Universität Leipzig", degree="Diplom")])
    assert compute_import_not_applied(undated, undated_vault, []) == []


# ── finding 2: an alias carry leaves no receipt on the import summary ─────────


@pytest.mark.asyncio
async def test_adv_vault_2_witness_only_alias_carry_has_no_receipt():
    """FMEA delta, new row, control (D): "the summary line (basis="alias") with
    'Nicht dasselbe' on the import that would hide the entry". When the
    model emits NO op for an entry whose name is a recorded alias (the likeliest
    reaction, since the vault view it reads shows the alias), the witness
    carries it (import_witness.py:537) and nothing writes a receipt. The import
    result says: nothing lost, nothing recognised. The candidate has no line to
    click."""
    vault = MasterProfileData(skills=[Skill(id="s1", name="Testautomatisierung", aliases=["Testing"])])
    incoming = MasterProfileData(skills=[Skill(name="Testing")])
    result = await reconcile_import(vault, incoming, "cv_upload", _stub([]))
    names = [s.name for s in result.merged_profile.skills]
    assert names == ["Testautomatisierung"]  # "Testing" is not in the vault as its own entry
    assert result.not_applied == []  # ...and the witness says it was carried
    assert result.matched, "carried through an alias with no MatchReceipt: no summary line, no undo"


@pytest.mark.asyncio
async def test_adv_vault_2_engagement_alias_merge_has_no_receipt():
    """``_apply_upsert_work`` turns a recorded company alias plus the same month
    into a match (apply.py:2059-2069) but appends no MatchReceipt, unlike the
    skill, language and education alias paths (ADR-046 am. cl. 7: "An
    alias-driven applier match records basis="alias""). The merge never
    reaches the "Schon in deinem Profil" card."""
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Roche Diagnostics GmbH", role="Data Scientist", start_date="2019-03",
        company_aliases=["Roche"],
    )])
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Roche", role="Werkstudent Bioinformatik", start_date="2019-03",
    )])
    ops = [{"op": "upsert_work", "ref": "w", "company": "Roche",
            "role": "Werkstudent Bioinformatik", "start_date": "2019-03"}]
    result = await reconcile_import(vault, incoming, "cv_upload", _stub(ops))
    assert len(result.merged_profile.work_experience) == 1  # merged through the alias
    assert any(m.basis == "alias" and m.section == "work_experience" for m in result.matched), (
        f"alias-driven engagement merge left no receipt: matched={result.matched}"
    )


# ── finding 3: the undo does not take back the alias that caused the match ────


def _two_imports_of_testing() -> MasterProfileData:
    incoming = MasterProfileData(skills=[Skill(name="Testing")])
    vault = MasterProfileData(skills=[Skill(id="s1", name="Testautomatisierung")])
    first = _import_once(vault, incoming, [MatchExisting(target="s1", incoming="Testing")])
    assert first.skills[0].aliases == ["Testing"]
    # Second import of a document with "Testing": the applier matches it
    # through the recorded alias (basis "alias", aliases_added empty).
    return _import_once(first, incoming, [UpsertSkill(name="Testing")])


def test_adv_vault_3_undo_on_the_latest_import_leaves_the_wrong_alias():
    """Ruling V-2 = A: "one click adds the entry and removes the alias". The
    door undoes the NEWEST receipt for the pair (apply.py:3041-3059) and
    removes only that receipt's ``aliases_added``. A receipt that matched
    THROUGH the alias never recorded it, so the alias stays. This covers every
    basis="alias" receipt, i.e. the new FMEA row's own failure mode."""
    vault = _two_imports_of_testing()
    after = apply_ops(vault, [SeparateMatch(entity_id="s1", incoming="Testing")], "manual_edit").profile
    assert [s.name for s in after.skills] == ["Testautomatisierung", "Testing"]
    assert after.skills[0].aliases == [], (
        f"'Nicht dasselbe' left the alias on the entry: {after.skills[0].aliases}"
    )


def test_adv_vault_3_second_undo_of_the_same_pair_appends_a_duplicate():
    """Replay or double click: the first undo stamps the newest receipt, and the
    second walks on to the OLDER receipt for the same pair and appends the
    entry AGAIN (find_separable_receipt skips undone receipts with ``continue``).
    ``already_undone`` is unreachable while an older receipt exists."""
    vault = _two_imports_of_testing()
    once = apply_ops(vault, [SeparateMatch(entity_id="s1", incoming="Testing")], "manual_edit").profile
    with pytest.raises(MatchNotSeparableError):
        twice = apply_ops(once, [SeparateMatch(entity_id="s1", incoming="Testing")], "manual_edit").profile
        pytest.fail(f"second undo succeeded: {[s.name for s in twice.skills]}")


# ── finding 4: the undo re-adds an entry the vault already holds ──────────────


def test_adv_vault_4_alias_equal_to_another_entrys_own_name_and_undo_duplicates_it():
    """``add_alias`` checks the target's own names only (aliases.py:116-134),
    not the other entries of the section. A wrong binding of incoming "Java" to
    JavaScript records "Java" as JavaScript's alias although "Java" is an entry
    of its own. "Nicht dasselbe" then appends a SECOND "Java", because the
    undo bypasses every identity check (apply.py:3100-3105)."""
    vault = MasterProfileData(skills=[Skill(id="j", name="Java"), Skill(id="js", name="JavaScript")])
    incoming = MasterProfileData(skills=[Skill(name="Java")])
    after_import = _import_once(vault, incoming, [MatchExisting(target="js", incoming="Java")])
    js = next(s for s in after_import.skills if s.id == "js")
    assert js.aliases == [], f"another entry's own name recorded as an alias: {js.aliases}"


def test_adv_vault_4_undo_appends_an_exact_duplicate_of_an_existing_entry():
    vault = MasterProfileData(skills=[Skill(id="j", name="Java"), Skill(id="js", name="JavaScript")])
    incoming = MasterProfileData(skills=[Skill(name="Java")])
    after_import = _import_once(vault, incoming, [MatchExisting(target="js", incoming="Java")])
    after = apply_ops(after_import, [SeparateMatch(entity_id="js", incoming="Java")], "manual_edit").profile
    names = [s.name for s in after.skills]
    assert names.count("Java") == 1, f"undo appended a duplicate: {names}"


# ── finding 5: a language-table pair is undoable when the model matched it ────


def test_adv_vault_5_model_matched_table_pair_is_still_undoable():
    """Ruling V-2 scope note: "language-table matches get no undo". The door
    refuses by ``basis == "name_table"`` only (apply.py:3049). When the MODEL
    emitted ``match_existing`` for English -> Englisch, the receipt says
    basis "model", the alias writer records "English" as an alias (although
    ADR-046 am. cl. 5 says the table matches and does not alias), and
    "Nicht dasselbe" adds a second row for the same language."""
    vault = MasterProfileData(languages=[Language(id="l1", language="Englisch", level="C1")])
    incoming = MasterProfileData(languages=[Language(language="English", level="C1")])
    after_import = _import_once(vault, incoming, [MatchExisting(target="l1", incoming="English")])
    with pytest.raises(MatchNotSeparableError) as exc:
        after = apply_ops(after_import, [SeparateMatch(entity_id="l1", incoming="English")], "manual_edit").profile
        pytest.fail(f"table pair undone: {[l.language for l in after.languages]}")
    assert exc.value.code == "name_table"


# ── finding 6: writer (b) records the model's own spelling, on every door ─────


def test_adv_vault_6_targeted_upsert_records_a_name_no_document_states():
    """aliases.py's doctrine: the alias TEXT is "the incoming document's own
    wording - never [...] the model's free-text". Writer (b) in
    ``_apply_upsert_work`` (apply.py:2172-2178) copies ``op.company`` verbatim.
    A translation the model produced in the op becomes an alternate name of
    the employer, and the AGENT_GUIDE tells agents to pick the alias "that fits
    the document's language"."""
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Bayerischer Blutspendedienst gGmbH", role="Systementwickler", start_date="2012-08",
    )])
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="BSD Bayern", role="Systementwickler", start_date="2012-08",
    )])
    ops = [UpsertWork(ref="w", target="w1", company="Bavarian Red Cross Blood Service",
                      role="Systementwickler", start_date="2012-08")]
    after = _import_once(vault, incoming, ops)
    document_names = {w.company for w in incoming.work_experience}
    for alias in after.work_experience[0].company_aliases:
        assert alias in document_names, f"alias {alias!r} is in no incoming document entry"


def test_adv_vault_6_turn_door_restatement_now_reads_as_a_write():
    """fmea-delta "SF-VAULT.13 ... turn doors write no alias ... unchanged".
    Writer (b) is in the shared applier and runs on interview, testimony and
    agent turns too. A turn that only restates the employer under another
    spelling now returns a non-empty ``changes``, and six readers take
    ``bool(changes)`` to mean "the gap is addressed"."""
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Roche Diagnostics GmbH", role="Data Scientist", start_date="2019-03",
    )])
    applied = apply_ops(vault, [UpsertWork(ref="w", target="w1", company="Roche", role="Data Scientist")], "interview")
    assert applied.changes == [], (
        f"a pure restatement on a turn door wrote: {[(c.field, c.new_value) for c in applied.changes]}"
    )


# ── finding 7: an undo of a receipt without incoming_entry can crash the door ─


def test_adv_vault_7_undo_without_incoming_entry_on_a_story_raises_validation_error():
    """``_minimal_entry`` builds ``{title: incoming}`` for a signature story
    (apply.py:3062-3074). SignatureStory requires challenge, mechanism and
    outcome, so ``model_validate`` raises a pydantic ValidationError. The router
    catches only LookupError and MatchNotSeparable, so the door answers 500,
    not 409. Turn doors write such receipts (no ``incoming_entry``)."""
    receipt = MatchReceipt(section="signature_stories", entity_id="st1",
                           incoming="LIMS-Migration", existing="Migration")
    vault = MasterProfileData(
        signature_stories=[SignatureStory(id="st1", title="Migration", challenge="c", mechanism="m", outcome="o")],
        metadata=ProfileMetadata(enrichment_history=[EnrichmentRecord(
            timestamp=datetime.now(timezone.utc), source="interview", matched=[receipt],
        )]),
    )
    try:
        apply_ops(vault, [SeparateMatch(entity_id="st1", incoming="LIMS-Migration")], "manual_edit")
    except MatchNotSeparableError:
        return  # a clean refusal is acceptable
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"undo raised {type(exc).__name__}, which the door turns into a 500")


# ── finding 8: the section-replace door is a fourth alias writer ──────────────


def test_adv_vault_8_section_replace_accepts_a_new_free_text_alias():
    """ADR-046 am. cl. 2: exactly three writers. (c) the candidate may REMOVE an
    alias, and "the editor does NOT offer free-text alias entry". The
    committer's ``ReplaceSection``, used by ``PATCH /api/profile/{section}`` and
    the MCP ``update_profile`` tool, takes any new alias as sent. An alias
    added there then silently carries a later entry of that name at the
    witness."""
    vault = MasterProfileData(skills=[Skill(id="s1", name="Docker")])
    sent = [{"id": "s1", "name": "Docker", "aliases": ["Kubernetes"]}]
    applied = apply_ops(vault, [ReplaceSection(section="skills", value=sent)], "manual_edit")
    assert applied.profile.skills[0].aliases == [], (
        f"a free-text alias entered through the section door: {applied.profile.skills[0].aliases}"
    )


# ── sound probes (pass on the reviewed tree) ──────────────────────────────────


def test_adv_vault_sound_repeat_stint_is_never_bound_or_carried():
    """A repeat stint (same employer and title, another start month) is not
    carried by the binder, the alias reading, the applier, or the alias writer."""
    vault = MasterProfileData(work_experience=[WorkEntry(
        id="w1", company="Roche Diagnostics GmbH", role="Data Scientist", start_date="2019-03",
        company_aliases=["Roche"],
    )])
    incoming = MasterProfileData(work_experience=[WorkEntry(
        company="Roche", role="Data Scientist", start_date="2023-05",
    )])
    ops = [MatchExisting(target="w1", incoming="Roche / Data Scientist")]
    assert _labels(compute_import_not_applied(incoming, vault, ops)) == ["Roche / Data Scientist / 2023-05"]
    assert _labels(compute_import_not_applied(incoming, vault, [])) == ["Roche / Data Scientist / 2023-05"]
    applied = apply_ops(vault, [UpsertWork(ref="w", company="Roche", role="Data Scientist",
                                           start_date="2023-05")], "cv_upload")
    assert len(applied.profile.work_experience) == 2 or applied.pending_confirmations
    after = _import_once(vault.model_copy(deep=True), incoming, ops)
    assert after.work_experience[0].company_aliases == ["Roche"]


# ── the door: cross-user scoping and the history record ───────────────────────


@pytest_asyncio.fixture
async def two_users():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import applire.models  # noqa: F401
    from applire import ownership
    from applire.db.session import Base
    from applire.models.user import User
    from tests.support.profile_factory import make_master_profile

    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    a = User(id=uuid.uuid4(), email="adv-a@example.org", role="user")
    b = User(id=uuid.uuid4(), email="adv-b@example.org", role="user")
    vault = _two_imports_of_testing()
    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add_all([a, b])
            await s.flush()
            s.add(make_master_profile(user_id=a.id, profile_json=vault.model_dump(mode="json")))
            await s.commit()
    yield factory, a, b
    await eng.dispose()


async def _post_separate(factory, user, body: dict):
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.main import app

    class _AsUser:
        async def get_current_user(self, request, db=None):  # noqa: ANN001
            return user

    async def _db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsUser()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.wait_for(c.post("/api/profile/matches/separate", json=body), timeout=20)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)


async def _profile_json(factory, user_id):
    from sqlalchemy import select

    from applire import ownership
    from applire.models.profile import MasterProfile

    with ownership.unscoped("tooling"):
        async with factory() as s:
            row = (await s.execute(select(MasterProfile).where(MasterProfile.user_id == user_id))).scalar_one()
            return row.profile_json


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_adv_vault_sound_cross_user_separate_is_404_and_changes_nothing(two_users):
    factory, a, b = two_users
    before = await _profile_json(factory, a.id)
    resp = await _post_separate(factory, b, {"entity_id": "s1", "incoming": "Testing"})
    assert resp.status_code == 404, resp.text
    assert await _profile_json(factory, a.id) == before
    # positive control: the owner reaches the same pair
    resp = await _post_separate(factory, a, {"entity_id": "s1", "incoming": "Testing"})
    assert resp.status_code == 200, resp.text


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_adv_vault_3_door_replay_appends_a_second_entry(two_users):
    """Finding 3 at the door: the same POST twice. The second answer should be
    409 ``already_undone``; it is 200 and the vault holds two "Testing" rows."""
    factory, a, _b = two_users
    first = await _post_separate(factory, a, {"entity_id": "s1", "incoming": "Testing"})
    assert first.status_code == 200, first.text
    second = await _post_separate(factory, a, {"entity_id": "s1", "incoming": "Testing"})
    names = [s["name"] for s in (await _profile_json(factory, a.id))["skills"]]
    assert second.status_code == 409, f"replay answered {second.status_code}; skills now {names}"
