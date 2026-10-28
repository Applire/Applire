# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-4b: a review card asked "Stimmt das?" about a term in the
document. The candidate answered that the term is the name of the employer's
team, taken from the job posting, and that they do not claim it about
themselves. The testimony door persisted that reply as a DENIAL of the term.
A denial then blocks the word in every later document.

Prompt-first. Rule 9 named what a denial is, but had nothing on a reply that
says where a word comes from. The fix is the paragraph "A WORD THAT IS SOMEONE
ELSE'S NAME IS NOT DENIED", measured on the persisted reconcile input (see the
version header in prompts/reconcile.py). These tests pin:

- the paragraph and its contrast;
- that its examples are held out of the measured replies;
- the seam it acts through. The review card's answer goes through
  ``submit_testimony``, which sends this system prompt. A model that follows the
  rule (no denial, no op) leaves the profile without a ``denied_concepts`` row
  and reports ``no_change``.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

_LEAD = "A WORD THAT IS SOMEONE ELSE'S NAME IS NOT DENIED."


def _rule9() -> str:
    start = RECONCILE_SYSTEM_PROMPT.index("9. STANCE (ADR-040).")
    end = RECONCILE_SYSTEM_PROMPT.index("10. CERTIFICATIONS.")
    return " ".join(RECONCILE_SYSTEM_PROMPT[start:end].split())


def _paragraph() -> str:
    rule = _rule9()
    return rule[rule.index(_LEAD):]


def test_rule9_says_a_word_naming_someone_elses_thing_denies_nothing():
    para = _paragraph()
    for owner in ("employer's team", "department", "product or programme",
                  "a phrase from the job posting"):
        assert owner in para, owner
    assert "do not claim it about themselves" in para
    assert 'put nothing in "denials" for it' in para


def test_the_paragraph_keeps_a_real_denial_a_denial():
    para = _paragraph()
    assert "Only a statement that they have not done, used or known X" in para
    assert "denies X." in para


def test_the_examples_are_held_out_of_the_measured_replies():
    para = _paragraph().lower()
    for measured in ("developer experience", "site reliability", "digital foundations",
                     "ausschreibung", "über mich", "backstage"):
        assert measured not in para, measured


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _RuleFollower:
    """Does what the paragraph asks on the D-4b shape: no denial, no op."""

    def __init__(self) -> None:
        self.system: str | None = None
        self.prompt: str | None = None

    async def aparse_json(self, prompt, **kw):
        self.system = kw.get("system")
        self.prompt = prompt
        return {"ops": [], "ambiguities": [], "denials": [], "empty_reason": "nothing_actionable"}


@pytest.mark.asyncio
async def test_seam_a_name_reply_through_the_testimony_door_writes_no_denial(db_session, monkeypatch):
    from applire.services.profile.reconcile import testimony_bridge

    async def _de(*_a, **_k):
        return "de"

    monkeypatch.setattr("applire.services.session.get_ui_language", _de)
    with authorized_profile_write():
        record = MasterProfile(profile_json={
            "personal_info": {"full_name": "Kim Muster"},
            "work_experience": [{"id": "w1", "company": "Nordwerk", "role": "Engineering Lead"}],
        })
    db_session.add(record)
    await db_session.commit()
    provider = _RuleFollower()
    reply = ("Platform Craft heißt beim Arbeitgeber das Team aus der Anzeige, "
             "das ist keine Aussage über meine Erfahrung.")

    out = await testimony_bridge.submit_testimony(reply, db_session, provider)
    await db_session.commit()
    await db_session.refresh(record)

    assert provider.system == RECONCILE_SYSTEM_PROMPT
    assert _LEAD in " ".join(provider.system.split())
    assert reply in provider.prompt
    assert out.status == "no_change"
    assert not ((record.profile_json.get("metadata") or {}).get("denied_concepts"))
