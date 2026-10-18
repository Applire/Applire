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

"""Hazelnut F2: a denial names an ITEM, never missing evidence (ADR-059 contract).

The delivery run of 2026-10-18 persisted five `denied_concepts` rows whose
statements only withheld MORE detail ("Mehr Detail … kann ich dazu nicht
nennen", "Eine konkrete Investitionssumme dazu kann ich nicht nennen"). ADR-059
persists what the person disclaimed, so a row for a withheld detail breaks its
contract. ADR-062 forbids a code gate on that judgement, so the fix is rule 9's
"A DENIAL NAMES AN ITEM, NEVER MISSING EVIDENCE" paragraph, measured on the
captured calls (see the prompt module's version history).

These tests pin the paragraph and its contrast pair. They also pin the seam the
paragraph acts through: the engine sends this system prompt, and a model that
follows it (empty `denials`, facts as ops) writes the facts and no
`denied_concepts` row through the real interview committer.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn


def _rule9() -> str:
    start = RECONCILE_SYSTEM_PROMPT.index("9. STANCE (ADR-040).")
    end = RECONCILE_SYSTEM_PROMPT.index("10. CERTIFICATIONS.")
    return " ".join(RECONCILE_SYSTEM_PROMPT[start:end].split())


def test_rule9_says_a_denial_names_an_item_not_missing_evidence():
    rule = _rule9()
    assert "A DENIAL NAMES AN ITEM, NEVER MISSING EVIDENCE." in rule
    # The item kinds the measured controls rely on (IFS/BRC standards, a domain,
    # a scope of responsibility) stay named as denials.
    for kind in ("skill", "tool", "standard", "certification", "language",
                 "industry or domain", "scope of responsibility"):
        assert kind in rule, kind
    # Withheld evidence kinds that denied nothing in the delivery run.
    for kind in ("example", "project", "figure", "sum", "detail"):
        assert kind in rule, kind
    assert "denies nothing" in rule
    assert "still record every fact the rest of the answer states" in rule


def test_rule9_contrast_pair_is_present_and_points_both_ways():
    rule = _rule9()
    assert '"mit SAP MM habe ich nicht gearbeitet" → "denials": ["SAP MM"]' in rule
    assert '"konkrete Zahlen zum Projekt kann ich nicht nennen" → no denial.' in rule


def test_rule9_examples_are_held_out_from_the_replay_inputs():
    """The replay measured the paragraph on the captured answers. Their exact
    phrases must not be in the prompt, so the measurement tests the shape and
    not a memorised string."""
    rule = _rule9()
    for captured in (
        "Mehr Detail zur klassischen Arbeitsvorbereitung",
        "Weitere eigene Digitalisierungsprojekte",
        "konkrete Investitionssumme",
        "Konkrete Entwicklungsbeispiele",
        "IFS", "BRC",
    ):
        assert captured not in rule, captured


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
    """A model double that does what rule 9 asks on the #33 delivery shape: the
    facts become ops, the withheld sum becomes nothing. It records the system
    prompt the engine sent, so the test pins the seam as well as the outcome."""

    def __init__(self) -> None:
        self.system: str | None = None

    async def aparse_json(self, prompt, **kw):
        self.system = kw.get("system")
        return {
            "ops": [
                {"op": "upsert_work", "ref": "w1", "company": "Weberit",
                 "role": "Produktionsleiter", "budget_managed": "ca. 6 Mio. €"},
            ],
            "ambiguities": [],
            "denials": [],
        }


@pytest.mark.asyncio
async def test_seam_withheld_detail_writes_facts_and_no_denied_concept(db_session):
    answer = (
        "Bei Weberit verantworte ich ein Budget von ca. 6 Mio. €. "
        "Eine genaue Projektsumme kann ich nicht nennen."
    )
    with authorized_profile_write():
        record = MasterProfile(profile_json={})
    db_session.add(record)
    await db_session.commit()
    provider = _RuleFollower()

    out = await reconcile_interview_turn(
        db_session, profile_record=record, gap="Investitionen und Budget",
        question="Hast du ein Budget verantwortet?", answer=answer,
        provider=provider, session_id="f2",
    )
    await db_session.commit()

    assert provider.system == RECONCILE_SYSTEM_PROMPT
    assert "A DENIAL NAMES AN ITEM, NEVER MISSING EVIDENCE." in provider.system
    assert out.denial_recorded is False
    assert out.denied_concepts == []
    assert out.profile_dict.get("metadata", {}).get("denied_concepts", []) == []
    work = out.profile_dict["work_experience"]
    assert [w["company"] for w in work] == ["Weberit"]
    assert work[0]["budget_managed"] == "ca. 6 Mio. €"
