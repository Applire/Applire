# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Token-cost aggregations (US313, ADR-086 clause 7).

The AC asks for three groupings — per day, per document, per application — and
one honesty property: an aggregate that contains estimated rows must **say so**
(``SF-OPS.8``). That last assertion is the one worth having; the sums are
arithmetic.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.db.session import Base
from applire.models.llm_usage import LlmUsage
from applire.models.retention_run import RetentionRun
from applire.services.ops.usage_report import usage_summary

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(_SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[RetentionRun.__table__, LlmUsage.__table__],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _add(db, **kwargs):
    defaults = dict(
        created_at=datetime.now(timezone.utc),
        provider="openrouter",
        model="openai/gpt-5.6-luna",
        stage="cv_tailoring",
        method="aparse_json",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated=False,
        document_kind="cv",
        duration_ms=1200,
        ok=True,
    )
    defaults.update(kwargs)
    db.add(LlmUsage(**defaults))
    await db.commit()


@pytest.mark.asyncio
async def test_an_empty_table_reports_zeroes_not_an_error(db):
    summary = await usage_summary(db)
    assert summary["today"]["total_tokens"] == 0
    assert summary["today"]["calls"] == 0
    assert summary["by_document"] == []


@pytest.mark.asyncio
async def test_today_and_the_window_are_counted_separately(db):
    await _add(db)
    await _add(db, created_at=datetime.now(timezone.utc) - timedelta(days=3))
    await _add(db, created_at=datetime.now(timezone.utc) - timedelta(days=30))

    summary = await usage_summary(db, days=7)
    assert summary["today"]["calls"] == 1
    assert summary["today"]["total_tokens"] == 150
    assert summary["window"]["calls"] == 2
    assert summary["window"]["total_tokens"] == 300
    assert summary["window_days"] == 7


@pytest.mark.asyncio
async def test_an_aggregate_containing_an_estimate_says_so(db):
    """SF-OPS.8 — the operator is reconciling an invoice against this number."""
    await _add(db, estimated=False)
    await _add(db, estimated=True)
    summary = await usage_summary(db)
    assert summary["today"]["estimated_calls"] == 1
    assert summary["today"]["fully_measured"] is False


@pytest.mark.asyncio
async def test_a_fully_measured_aggregate_says_that_too(db):
    await _add(db, estimated=False)
    summary = await usage_summary(db)
    assert summary["today"]["estimated_calls"] == 0
    assert summary["today"]["fully_measured"] is True


@pytest.mark.asyncio
async def test_spend_is_grouped_per_document_and_per_application(db):
    cv_id, letter_id = uuid.uuid4(), uuid.uuid4()
    application_id = uuid.uuid4()
    await _add(db, document_id=cv_id, application_id=application_id, total_tokens=900)
    await _add(db, document_id=cv_id, application_id=application_id, total_tokens=100)
    await _add(
        db,
        document_id=letter_id,
        application_id=application_id,
        document_kind="cover_letter",
        total_tokens=300,
    )

    summary = await usage_summary(db)
    documents = {row["id"]: row for row in summary["by_document"]}
    assert documents[str(cv_id)]["total_tokens"] == 1000
    assert documents[str(cv_id)]["calls"] == 2
    assert documents[str(cv_id)]["kind"] == "cv"
    assert documents[str(letter_id)]["kind"] == "cover_letter"
    assert summary["by_application"][0]["total_tokens"] == 1300


@pytest.mark.asyncio
async def test_unattributed_rows_count_in_the_totals_but_not_the_breakdown(db):
    """SF-OPS.7 — a partial breakdown beside a complete total, never a wrong one."""
    await _add(db, document_id=None, application_id=None,
               prompt_tokens=400, completion_tokens=100, total_tokens=500)
    await _add(db, document_id=uuid.uuid4(),
               prompt_tokens=150, completion_tokens=50, total_tokens=200)
    summary = await usage_summary(db)
    assert summary["today"]["total_tokens"] == 700
    assert len(summary["by_document"]) == 1


@pytest.mark.asyncio
async def test_the_breakdown_is_ordered_by_spend(db):
    small, large = uuid.uuid4(), uuid.uuid4()
    await _add(db, document_id=small, total_tokens=10)
    await _add(db, document_id=large, total_tokens=999)
    summary = await usage_summary(db)
    assert summary["by_document"][0]["id"] == str(large)


@pytest.mark.asyncio
async def test_a_failed_call_still_counts_toward_the_bill(db):
    """A failed call cost input tokens on most providers — hiding it flatters us."""
    await _add(db, ok=False, total_tokens=80)
    assert (await usage_summary(db))["today"]["calls"] == 1
