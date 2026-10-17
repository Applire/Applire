# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seed one throwaway database from a loaded fixture (E066 harness).

Creates exactly the rows the real CV and cover-letter pipelines read, and
nothing else. Every row below is annotated with the product reader that
consumes it (file:line at the time of writing; full table in
``tests/files/quality_bench/FIXTURE_FORMAT.md``):

* ``User``            — owner of everything (ADR-092): ``resolve_owner`` demands one
* ``MasterProfile``   — the vault: ``get_profile_for_user`` (services/cv.py:3295,
  services/cover_letter.py:208), then ``profile.profile_json`` (cv.py:4083)
* ``JobAnalysis``     — the posting + analysis: ``job_for_user`` (cv.py:3292),
  ``db.get(JobAnalysis)`` (cv.py:4040), cover_letter.py:1233
* ``Application``     — the user's link to the posting: ``job_for_user`` reaches the
  posting only through it (ADR-092 cl. 5c), and it carries the pinned
  ``language_override`` ``resolve_document_language`` reads
  (utils/language_detection.py:119)
* ``GapAnalysis``     — ``keyword_ledger`` (+ ``keyword_gaps``, ``category_c``): cv.py:4062-4093,
  cover_letter.py:1297-1330
* ``FlowSession``     — ``generate_cover_letter`` resolves the CV, the template and the
  caller's flow through it (cover_letter.py:176-196); its ``generated_cv_id`` is
  set by the harness once the CV is ready, as the flow orchestrator would

Inserts run inside an owner context so the ADR-092 statement guard, which the
harness installs on its own engine, sees the same discipline as production.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from quality_bench.fixture import GAP_COLUMNS, JOB_COLUMNS, Fixture

#: Synthetic owner. example.com is reserved for documentation (RFC 2606).
BENCH_USER_EMAIL = "bench-user@example.com"


@dataclass
class Seeded:
    user_id: uuid.UUID
    profile_id: uuid.UUID
    job_id: uuid.UUID
    application_id: uuid.UUID
    gap_id: uuid.UUID
    flow_id: uuid.UUID


def _pick(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: source[k] for k in keys if k in source}


async def seed_user(session_factory) -> uuid.UUID:
    """Only the synthetic owner — the JD-analysis seam (#766) creates the posting itself."""
    from applire import ownership
    from applire.models.user import User

    user_id = uuid.uuid4()
    async with session_factory() as db:
        with ownership.owner_context(user_id):
            db.add(User(id=user_id, email=BENCH_USER_EMAIL))
            await db.commit()
    return user_id


async def seed_fixture(session_factory, fx: Fixture, *, with_gap: bool = True) -> Seeded:
    """Insert the fixture's rows and return their ids. Commits.

    ``with_gap=False`` (the gap-analysis seam, #766) seeds no ``GapAnalysis``:
    ``analyze_gaps`` reuses a stored row for unchanged inputs, so a seeded one
    would turn the measured call into a cache hit. The flow then points at none."""
    from applire import ownership
    from applire.models.application import Application
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.models.user import User

    user_id, profile_id, job_id, app_id, gap_id, flow_id = (uuid.uuid4() for _ in range(6))
    now = datetime.now(timezone.utc)

    job_fields = _pick(fx.job, JOB_COLUMNS)
    job_fields.setdefault("language_requirement", "")
    job_fields.setdefault("jd_language", fx.document_language)

    gap_fields = _pick(fx.gap, GAP_COLUMNS)
    gap_fields["keyword_ledger"] = fx.ledger

    async with session_factory() as db:
        with ownership.owner_context(user_id):
            db.add(User(id=user_id, email=BENCH_USER_EMAIL))
            await db.flush()
            db.add(
                JobAnalysis(
                    id=job_id,
                    raw_text=fx.posting,
                    raw_text_hash=hashlib.sha256(fx.posting.encode("utf-8")).hexdigest(),
                    source_url=None,  # see fixture.JOB_COLUMNS: a URL triggers network colour detection
                    **job_fields,
                )
            )
            await db.flush()
            # The document language is PINNED on the application, exactly as a
            # user's override would pin it — resolve_document_language() gives
            # ``language_override`` precedence over JD detection.
            db.add(
                Application(
                    id=app_id,
                    user_id=user_id,
                    job_analysis_id=job_id,
                    role_title=job_fields.get("role_title"),
                    company_name=job_fields.get("company_name"),
                    language_override=fx.document_language,
                )
            )
            # ``authorized_profile_write`` is the production door the ADR-063
            # clause-6 guard offers; the harness opens it for the one write.
            with authorized_profile_write():
                db.add(
                    MasterProfile(
                        id=profile_id,
                        user_id=user_id,
                        profile_json=fx.vault,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await db.flush()
            if with_gap:
                db.add(
                    GapAnalysis(
                        id=gap_id,
                        job_analysis_id=job_id,
                        profile_id=profile_id,
                        user_id=user_id,
                        created_at=now,
                        **gap_fields,
                    )
                )
                await db.flush()
            db.add(
                FlowSession(
                    id=flow_id,
                    user_id=user_id,
                    job_id=job_id,
                    current_step="cv_generation",
                    user_type="returning",
                    gap_analysis_id=gap_id if with_gap else None,
                    application_id=app_id,
                )
            )
            await db.commit()

    return Seeded(user_id, profile_id, job_id, app_id, gap_id, flow_id)
