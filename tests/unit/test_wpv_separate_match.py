# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WP-V — #717 "Nicht dasselbe": `SeparateMatch` through the committer
(ADR-063 amended 2026-10-07 cl. 4, founder ruling V-2 = A).

Acceptance (#717): undoing a match adds the entry and records both steps in the
profile history. Plus: a language-name-table pair is not undoable, a receipt is
undone once, only the names THAT binding recorded come off, the op is not
model-emittable.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EnrichmentRecord,
    MasterProfileData,
    MatchReceipt,
    ProfileMetadata,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import (  # noqa: E402
    MatchNotSeparableError,
    apply_ops,
)
from applire.services.profile.reconcile.ops import SeparateMatch  # noqa: E402

from tests.support.profile_factory import make_master_profile  # noqa: E402


def _profile_with_receipts() -> MasterProfileData:
    receipts = [
        MatchReceipt(
            section="skills", entity_id="s1", incoming="Machine Learning",
            existing="Maschinelles Lernen", basis="model",
            aliases_added={"aliases": "Machine Learning"},
            incoming_entry={"name": "Machine Learning", "category": "technical", "proficiency": "advanced"},
        ),
        MatchReceipt(
            section="languages", entity_id="l1", incoming="English", existing="Englisch",
            basis="name_table",
        ),
        MatchReceipt(
            section="work_experience", entity_id="w1", incoming="Roche / Systemanalytiker",
            existing="Roche Diagnostics GmbH / System Analyst", basis="model",
            aliases_added={"company_aliases": "Roche"},
        ),
    ]
    return MasterProfileData(
        skills=[Skill(id="s1", name="Maschinelles Lernen", aliases=["Machine Learning", "ML"])],
        languages=[{"id": "l1", "language": "Englisch"}],
        work_experience=[WorkEntry(
            id="w1", company="Roche Diagnostics GmbH", role="System Analyst", start_date="2011-06",
            company_aliases=["Roche"], role_aliases=["Systemanalytiker"],
        )],
        metadata=ProfileMetadata(enrichment_history=[EnrichmentRecord(
            timestamp=datetime(2026, 10, 7, tzinfo=timezone.utc), source="linkedin_import",
            matched=receipts,
        )]),
    )


def test_separate_adds_the_incoming_entry_and_takes_back_only_its_own_alias():
    applied = apply_ops(_profile_with_receipts(), [SeparateMatch(entity_id="s1", incoming="machine learning")], "manual_edit")
    skills = applied.profile.skills
    assert [s.name for s in skills] == ["Maschinelles Lernen", "Machine Learning"]
    assert skills[0].aliases == ["ML"]  # a name another binding recorded stays
    assert skills[1].proficiency == "advanced"  # the document's own data came back
    assert skills[1].id != "s1"
    actions = [(c.action, c.field, c.rationale_key) for c in applied.changes]
    assert ("removed", "aliases", "match_separated") in actions
    assert ("added", "name", "match_separated") in actions
    receipt = applied.profile.metadata.enrichment_history[0].matched[0]
    assert receipt.undone_at is not None


def test_separate_twice_is_refused():
    once = apply_ops(_profile_with_receipts(), [SeparateMatch(entity_id="s1", incoming="Machine Learning")], "manual_edit")
    with pytest.raises(MatchNotSeparableError) as exc:
        apply_ops(once.profile, [SeparateMatch(entity_id="s1", incoming="Machine Learning")], "manual_edit")
    assert exc.value.code == "already_undone"


def test_a_name_table_pair_is_not_undoable():
    with pytest.raises(MatchNotSeparableError) as exc:
        apply_ops(_profile_with_receipts(), [SeparateMatch(entity_id="l1", incoming="English")], "manual_edit")
    assert exc.value.code == "name_table"


def test_an_unknown_pair_is_refused():
    with pytest.raises(MatchNotSeparableError) as exc:
        apply_ops(_profile_with_receipts(), [SeparateMatch(entity_id="s1", incoming="Deep Learning")], "manual_edit")
    assert exc.value.code == "unknown"


def test_engagement_without_an_incoming_entry_is_rebuilt_from_the_pair():
    applied = apply_ops(
        _profile_with_receipts(),
        [SeparateMatch(entity_id="w1", incoming="Roche / Systemanalytiker")],
        "manual_edit",
    )
    work = applied.profile.work_experience
    assert [(w.company, w.role) for w in work] == [
        ("Roche Diagnostics GmbH", "System Analyst"), ("Roche", "Systemanalytiker"),
    ]
    assert work[0].company_aliases == []
    assert work[0].role_aliases == ["Systemanalytiker"]  # not recorded by this binding


def test_separate_match_is_not_model_emittable():
    from pydantic import TypeAdapter, ValidationError

    from applire.services.profile.reconcile.ops import ReconcileOp
    from applire.services.profile.reconcile.schema_out import reconcile_response_schema

    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python({"op": "separate_match", "entity_id": "s1", "incoming": "x"})
    assert "separate_match" not in str(reconcile_response_schema())


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base  # noqa: F401
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_door_writes_both_steps_into_one_history_record(sqlite_session):
    from applire.services.profile import MatchNotSeparable, separate_match

    record = make_master_profile(profile_json=_profile_with_receipts().model_dump(mode="json"))
    sqlite_session.add(record)
    await sqlite_session.commit()

    response = await separate_match(sqlite_session, entity_id="s1", incoming="Machine Learning")
    names = [s.name for s in response.profile.skills]
    assert names == ["Maschinelles Lernen", "Machine Learning"]

    await sqlite_session.refresh(record)
    history = MasterProfileData.model_validate(record.profile_json).metadata.enrichment_history
    head = history[-1]
    assert head.source == "manual_edit"
    assert {c.action for c in head.changes} >= {"removed", "added"}
    assert history[0].matched[0].undone_at is not None

    with pytest.raises(MatchNotSeparable) as exc:
        await separate_match(sqlite_session, entity_id="s1", incoming="Machine Learning")
    assert exc.value.code == "already_undone"
