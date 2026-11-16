# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Q3-4 (Sorbet wave 2b; founder rulings Q3-4 and Q8-3 = A + dismiss) — an
interview answer that only ADDS detail to a held bullet replaces it, with an
undo. Code replaces only on the restatement model's explicit statement
(``covers`` + ``dropped: ""`` + non-empty ``added``) and only on the safe side
(the entry's own bullet, one-to-one, every figure / digit run / name-like token
of the old bullet kept). Any doubt keeps both bullets."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.prompts.restatement import RESTATEMENT_REPLACE_ADDENDUM, RESTATEMENT_SYSTEM_PROMPT
from applire.schemas.profile import MasterProfileData
from applire.services.profile.bullet_replace import keep_replaced_bullets
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    AddBullets,
    DismissBulletReplace,
    ReplaceBullet,
    ReplaceSection,
    SetField,
    UndoBulletReplace,
)
from applire.services.profile.reconcile.restatement import (
    hold_back_restatements,
    keeps_every_fact_marker,
)
from applire.services.prompt_view import prompt_profile_view

W1 = "7d1c4e2a-5b6f-4a3e-9c8d-0e1f2a3b4c5d"
P1 = "4b3a2c1d-0000-4000-8000-00000000p001"
HELD = "Einführung von KVP-Routinen"
OTHER = "Leitung von 38 Mitarbeitenden und Budget von 6 Mio. €"
DETAIL = "KVP-Routinen in drei Werken eingeführt, Ausschuss um 12 % gesenkt"
FRESH = "Zwei Auszubildende bis zur Kammerprüfung betreut"


def _profile(**over) -> MasterProfileData:
    work = {
        "id": W1, "company": "Weberit GmbH", "role": "Produktionsleiter",
        "responsibilities": [HELD, OTHER], "achievements": [],
    }
    work.update(over)
    return MasterProfileData.model_validate({
        "work_experience": [work],
        "projects": [{"id": P1, "name": "MES-Einführung", "associated_experience": W1,
                      "responsibilities": ["MES eingeführt"]}],
    })


class _Provider:
    def __init__(self, items):
        self.items = items
        self.systems: list[str] = []

    async def aparse_json(self, prompt, system=None, **kw):
        self.systems.append(system or "")
        return {"items": self.items}


def _add(*bullets, target=W1):
    return AddBullets(target=target, responsibilities=list(bullets))


def _item(new="e1.n1", added="drei Werke; 12 %", held=None, covers="e1.h1", dropped=""):
    return {"new": new, "added": added, "held": held, "covers": covers, "dropped": dropped}


async def _check(items, *ops, profile=None, replace=True):
    provider = _Provider(items)
    out, receipts = await hold_back_restatements(profile or _profile(), list(ops), provider, replace=replace)
    return out, receipts, provider


# ── the decision: only on the model's explicit statement, safe side only ────

@pytest.mark.asyncio
async def test_a_bullet_that_states_every_held_fact_and_adds_detail_replaces_it():
    out, receipts, provider = await _check([_item()], _add(DETAIL))
    assert out == [ReplaceBullet(entry_id=W1, field="responsibilities", old=HELD, new=DETAIL)]
    assert receipts == []
    assert provider.systems == [RESTATEMENT_SYSTEM_PROMPT + RESTATEMENT_REPLACE_ADDENDUM]


@pytest.mark.asyncio
async def test_the_import_door_is_unchanged():
    out, _, provider = await _check([_item()], _add(DETAIL), replace=False)
    assert out == [_add(DETAIL)]
    assert provider.systems == [RESTATEMENT_SYSTEM_PROMPT]


@pytest.mark.parametrize("item", [
    _item(dropped="drei Werke"),          # the model names a lost fact
    _item(dropped=None),                  # no statement
    {"new": "e1.n1", "added": "x", "held": None, "covers": "e1.h1"},  # key missing
    _item(covers=None, dropped=None),     # covers nothing
    _item(added=""),                      # adds nothing: a restatement, not a replace
    _item(covers="e2.h1"),                # another entry's bullet
    _item(covers="e1.h3"),                # a kin (project) bullet, not the entry's own
    _item(covers="e1.h9"),                # out of range
    _item(new="e1.h1"),                   # malformed new id
])
@pytest.mark.asyncio
async def test_any_doubt_keeps_both_bullets(item):
    out, _, _ = await _check([item], _add(DETAIL))
    assert not any(isinstance(o, ReplaceBullet) for o in out)


@pytest.mark.asyncio
async def test_two_new_bullets_claiming_one_held_bullet_replace_nothing():
    other = "KVP-Routinen eingeführt und Shopfloor-Boards in drei Werken aufgebaut"
    out, _, _ = await _check([_item("e1.n1"), _item("e1.n2")], _add(DETAIL, other))
    assert out == [_add(DETAIL, other)]


@pytest.mark.parametrize("old,new,ok", [
    (OTHER, "Leitung von 38 Mitarbeitenden im Zweischichtbetrieb", False),            # budget figure lost
    (OTHER, "Leitung von 38 Mitarbeitenden, 6 Mio. € Budget, zwei Schichten", True),
    ("ISO-45001-Zertifizierung vorbereitet", "Zertifizierung für die Produktion vorbereitet", False),  # name lost
    ("SAP PP eingeführt", "SAP-Modul PP in zwei Werken eingeführt", True),
    ("ISO 45001 vorbereitet", "ISO-Zertifizierung vorbereitet, Audit bestanden", False),            # digit run lost
    (HELD, DETAIL, True),
])
def test_the_figure_and_name_blockers(old, new, ok):
    assert keeps_every_fact_marker(old, new) is ok


@pytest.mark.asyncio
async def test_a_blocker_keeps_both_bullets_even_when_the_model_says_replace():
    lost = "Leitung von 38 Mitarbeitenden im Zweischichtbetrieb"
    out, _, _ = await _check([_item(covers="e1.h2")], _add(lost))
    assert out == [_add(lost)]


@pytest.mark.asyncio
async def test_only_work_entries_are_replaced():
    out, _, _ = await _check([_item()], _add("MES in zwei Werken eingeführt", target=P1))
    assert not any(isinstance(o, ReplaceBullet) for o in out)


@pytest.mark.asyncio
async def test_a_restated_bullet_is_still_held_back_beside_a_replace():
    restated = "KVP eingeführt."
    items = [_item("e1.n1"), {"new": "e1.n2", "added": "", "held": "e1.h1", "covers": None, "dropped": None},
             {"new": "e1.n3", "added": "Kammerprüfung", "held": None, "covers": None, "dropped": None}]
    out, receipts, _ = await _check(items, _add(DETAIL, restated, FRESH))
    assert out == [_add(FRESH), ReplaceBullet(entry_id=W1, field="responsibilities", old=HELD, new=DETAIL)]
    assert [r.text for r in receipts] == [restated]


# ── the write: replace in place, record, undo beside, dismiss ────────────────

def _apply(profile, *ops, source="interview"):
    return apply_ops(profile, list(ops), source=source)


def test_replace_writes_in_place_records_and_receipts():
    res = _apply(_profile(), ReplaceBullet(entry_id=W1, field="responsibilities", old=HELD, new=DETAIL))
    w = res.profile.work_experience[0]
    assert w.responsibilities == [DETAIL, OTHER]
    [rec] = w.replaced_bullets
    assert (rec.text, rec.replaced, rec.field) == (DETAIL, HELD, "responsibilities")
    [ch] = [c for c in res.changes if c.rationale_key == "bullet_replaced"]
    assert (ch.old_value, ch.new_value, ch.entity_id) == (HELD, DETAIL, W1)


def test_a_vanished_held_bullet_means_the_new_text_is_added():
    res = _apply(_profile(responsibilities=[OTHER]),
                 ReplaceBullet(entry_id=W1, field="responsibilities", old=HELD, new=DETAIL))
    w = res.profile.work_experience[0]
    assert w.responsibilities == [OTHER, DETAIL]
    assert w.replaced_bullets == []


def _replaced() -> MasterProfileData:
    return _apply(_profile(), ReplaceBullet(entry_id=W1, field="responsibilities", old=HELD, new=DETAIL)).profile


def test_undo_puts_the_old_bullet_back_beside_the_new_one():
    res = _apply(_replaced(), UndoBulletReplace(entry_id=W1, text=DETAIL), source="manual_edit")
    w = res.profile.work_experience[0]
    assert w.responsibilities == [DETAIL, HELD, OTHER]
    assert w.replaced_bullets == []
    assert any(c.rationale_key == "bullet_replace_undone" for c in res.changes)


def test_dismiss_hides_the_line_and_keeps_the_new_bullet():
    res = _apply(_replaced(), DismissBulletReplace(entry_id=W1, text=DETAIL), source="manual_edit")
    w = res.profile.work_experience[0]
    assert w.responsibilities == [DETAIL, OTHER]
    assert w.replaced_bullets == []


def test_undo_of_an_unknown_record_is_refused():
    with pytest.raises(LookupError):
        _apply(_replaced(), UndoBulletReplace(entry_id=W1, text="nie gesagt"), source="manual_edit")


def test_editing_the_new_bullet_ends_the_record():
    p = _replaced()
    work = [e.model_dump(mode="json") for e in p.work_experience]
    work[0]["responsibilities"] = ["KVP in drei Werken", OTHER]
    res = _apply(p, ReplaceSection(section="work_experience", value=work), source="manual_edit")
    assert res.profile.work_experience[0].replaced_bullets == []


def test_the_section_door_carries_the_records_and_refuses_a_forged_one():
    p = _replaced()
    work = [e.model_dump(mode="json") for e in p.work_experience]
    work[0].pop("replaced_bullets")  # an editor that does not round-trip the field
    res = _apply(p, ReplaceSection(section="work_experience", value=work), source="manual_edit")
    assert [r.text for r in res.profile.work_experience[0].replaced_bullets] == [DETAIL]

    clean = _profile()
    forged = [e.model_dump(mode="json") for e in clean.work_experience]
    forged[0]["replaced_bullets"] = [{"text": HELD, "replaced": "Ich war Werksleiter", "field": "responsibilities"}]
    res = _apply(clean, ReplaceSection(section="work_experience", value=forged), source="manual_edit")
    assert res.profile.work_experience[0].replaced_bullets == []


def test_keep_replaced_bullets_drops_a_payload_record_on_a_new_entry():
    after = [{"id": "new", "replaced_bullets": [{"text": "x", "replaced": "y"}]}]
    assert keep_replaced_bullets("work_experience", [], after) == [{"id": "new"}]


def test_a_model_set_field_cannot_forge_a_record():
    res = _apply(_profile(), SetField(target=W1, field="replaced_bullets", value=[
        {"text": HELD, "replaced": "Werksleiter seit 2010", "field": "responsibilities"}]))
    assert res.profile.work_experience[0].replaced_bullets == []
    assert not any(c.field == "replaced_bullets" for c in res.changes)


def test_the_record_never_reaches_a_prompt():
    p = _replaced()
    view = prompt_profile_view(p.model_dump(mode="json"))
    assert "replaced_bullets" not in view["work_experience"][0]
    assert HELD not in str(view)


def test_an_unreplaced_profile_dumps_without_the_field():
    assert "replaced_bullets" not in _profile().model_dump(mode="json")["work_experience"][0]


# ── seam: the interview door turns the model's statement into the replace ───

@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _TurnProvider:
    def __init__(self, items):
        self.items = items

    async def aparse_json(self, prompt, system=None, **kw):
        if (system or "").startswith(RESTATEMENT_SYSTEM_PROMPT):
            return {"items": self.items}
        return {"ops": [{"op": "add_bullets", "target": "id_w1", "responsibilities": [DETAIL]}],
                "ambiguities": [], "denials": []}


async def _turn(db, items):
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    with authorized_profile_write():
        record = MasterProfile(profile_json=_profile().model_dump(mode="json"))
    db.add(record)
    await db.commit()
    out = await reconcile_interview_turn(
        db, profile_record=record, gap="KVP", question="Wie lief die KVP-Einführung?",
        answer="Ich habe die KVP-Routinen in drei Werken eingeführt, der Ausschuss sank um 12 %.",
        provider=_TurnProvider(items), session_id="s1", lang="de",
    )
    await db.commit()
    return out


@pytest.mark.asyncio
async def test_seam_an_interview_answer_that_adds_detail_replaces_the_bullet(db):
    out = await _turn(db, [_item()])
    w = out.profile_dict["work_experience"][0]
    assert w["responsibilities"] == [DETAIL, OTHER]
    assert w["replaced_bullets"][0]["replaced"] == HELD
    assert out.addressed is True


@pytest.mark.asyncio
async def test_seam_an_answer_with_a_different_fact_keeps_both_bullets(db):
    out = await _turn(db, [_item(covers=None, dropped=None)])
    w = out.profile_dict["work_experience"][0]
    assert w["responsibilities"] == [HELD, OTHER, DETAIL]
    assert "replaced_bullets" not in w


# ── seam: the profile's "Rückgängig" / "Ausblenden" doors (Q8-3) ────────────

async def _stored_replaced(db):
    with authorized_profile_write():
        record = MasterProfile(profile_json=_replaced().model_dump(mode="json"))
    db.add(record)
    await db.commit()
    return record


@pytest.mark.asyncio
async def test_seam_undo_door(db):
    from applire.services.profile.bullet_replace import undo_bullet_replace

    await _stored_replaced(db)
    resp = await undo_bullet_replace(db, entry_id=W1, text=DETAIL)
    w = resp.profile.work_experience[0]
    assert w.responsibilities == [DETAIL, HELD, OTHER] and w.replaced_bullets == []


@pytest.mark.asyncio
async def test_seam_dismiss_door_and_unknown_record(db):
    from applire.services.profile.bullet_replace import dismiss_bullet_replace

    await _stored_replaced(db)
    with pytest.raises(LookupError):
        await dismiss_bullet_replace(db, entry_id=W1, text="nie gesagt")
    resp = await dismiss_bullet_replace(db, entry_id=W1, text=DETAIL)
    w = resp.profile.work_experience[0]
    assert w.responsibilities == [DETAIL, OTHER] and w.replaced_bullets == []


@pytest.mark.asyncio
async def test_a_blocked_replace_logs_no_bullet_text(caplog):
    """Coordinator (log hygiene): the application log never carries the
    person's bullet — only the entry id, the position, the length, the check."""
    import logging

    lost = "Leitung von 38 Mitarbeitenden im Zweischichtbetrieb"
    with caplog.at_level(logging.INFO, logger="applire.services.profile.reconcile.restatement"):
        await _check([_item(covers="e1.h2")], _add(lost))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "blocked by the figure check" in text and W1 in text
    for words in (OTHER, lost, "Mitarbeitenden", "Mio"):
        assert words not in text
