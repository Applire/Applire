# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:L41 (ADR-021 / ADR-063 amended 2026-10-20) — a CV import whose extraction
review could not run is recorded on the import receipt and the import response.

Reproduction (before): ``ingest_cv`` called ``review_and_refine`` without an
``on_settle`` hook, so a ``reviewer_call_failed`` settle (founder's 0.42.0b0,
``glm-5.3-flash``: verdict-budget truncation on both attempts) reached a log line
and nothing else — no ``EnrichmentRecord`` field, no response field.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from applire.schemas.profile import EnrichmentRecord, extraction_review_from_settle
from applire.services.review_issues import ReviewSettle

from .test_615_import_door_adapters import (  # noqa: F401  (fixture re-export)
    _partial_merge_result,
    _seed_first_profile,
    sqlite_session,
)


def _review_settling_on(path: str | None, rounds: int = 1, blocking: tuple = ()):
    async def fake(**kw):
        kw["on_settle"](
            ReviewSettle(
                path=path, approved=False, blocking_issues=blocking, minor_issues=(),
                rounds=rounds, settled=kw["draft"],
            )
        )
        return kw["draft"]

    return fake


async def _upload(sqlite_session, tmp_path, review_fake):
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider

    await _seed_first_profile(sqlite_session)
    provider = AsyncMock()
    with (
        patch("applire.services.cv_parser.extract_text", new=AsyncMock(return_value="Katrin Hoffmann")),
        patch("applire.services.profile.extract_with_fallback",
              new=AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"},
                                          "skills": [{"name": "Kubernetes"}]})),
        patch("applire.services.profile.review_and_refine", new=review_fake),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
        patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())),
    ):
        return await upload_cv(
            file_bytes=b"cv-bytes", filename="cv.pdf", content_type="application/pdf",
            db=sqlite_session, provider=provider, storage=LocalStorageProvider(str(tmp_path)),
            ocr_extractor=AsyncMock(),
        )


@pytest.mark.parametrize("path", ["reviewer_call_failed", "review_malformed", "generator_call_failed"])
@pytest.mark.asyncio
async def test_a_review_that_could_not_run_holds_the_import(sqlite_session, tmp_path, path):
    """Founder ruling V-2 = C: the import is HELD behind the US167 staged gate."""
    response = await _upload(sqlite_session, tmp_path, _review_settling_on(path, rounds=0))
    assert response.status == "GATED"
    assert response.gate == "review_not_run"
    assert response.staged_id is not None
    assert response.extraction_review is not None and response.extraction_review.path == path

    from applire.services.profile import _get_latest, list_open_gates

    record = await _get_latest(sqlite_session, None)
    # nothing merged: the seeded vault has no enrichment history yet
    assert not (record.profile_json.get("metadata") or {}).get("enrichment_history")
    open_gates = await list_open_gates(sqlite_session, user_id=None)
    assert [g.gate_status for g in open_gates] == ["review_not_run"]


@pytest.mark.asyncio
async def test_an_exhausted_review_is_not_held_but_receipted(sqlite_session, tmp_path):
    response = await _upload(
        sqlite_session, tmp_path, _review_settling_on("exhausted", rounds=5, blocking=("x",))
    )
    assert response.status != "GATED"
    assert response.extraction_review.status == "open_findings"
    assert response.extraction_review.open_blocking == 1

    from applire.services.profile import _get_latest

    record = await _get_latest(sqlite_session, None)
    history = [EnrichmentRecord.model_validate(r) for r in record.profile_json["metadata"]["enrichment_history"]]
    assert history[-1].extraction_review.status == "open_findings"


@pytest.mark.asyncio
async def test_resolving_a_held_import_merges_with_a_not_reviewed_receipt(sqlite_session, tmp_path):
    response = await _upload(sqlite_session, tmp_path, _review_settling_on("reviewer_call_failed", rounds=0))
    from applire.services.profile import _get_latest, resolve_staged_extraction

    with patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())):
        await resolve_staged_extraction(
            sqlite_session, response.staged_id, action="merge", user_id=None, provider=AsyncMock()
        )
    record = await _get_latest(sqlite_session, None)
    history = [EnrichmentRecord.model_validate(r) for r in record.profile_json["metadata"]["enrichment_history"]]
    assert history[-1].extraction_review is not None
    assert history[-1].extraction_review.status == "not_reviewed"


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_interview_gate_question_names_the_review(lang):
    from applire.services.interview_graph import gate_question

    q = gate_question("review_not_run", "Katrin Hoffmann", "Katrin Hoffmann", lang)["question"]
    assert ("automatic check" in q) if lang == "en" else ("automatische Prüfung" in q)


@pytest.mark.asyncio
async def test_an_approved_review_is_recorded_as_approved(sqlite_session, tmp_path):
    response = await _upload(sqlite_session, tmp_path, _review_settling_on("approved", rounds=2))
    assert response.extraction_review.status == "approved"
    assert response.extraction_review.rounds == 2


@pytest.mark.parametrize(
    "path,status",
    [
        ("approved", "approved"),
        ("minor_only", "approved"),
        ("exhausted", "open_findings"),
        ("cycle_detected", "open_findings"),
        ("generator_call_failed", "open_findings"),
        ("reviewer_call_failed", "not_reviewed"),
        ("review_malformed", "not_reviewed"),
        (None, "disabled"),
        ("some_future_path", "not_reviewed"),
    ],
)
def test_settle_path_mapping(path, status):
    assert extraction_review_from_settle(path, 1, 0).status == status


def test_a_record_persisted_before_the_field_loads_unchanged():
    old = {"id": "r1", "timestamp": "2026-09-16T10:00:00Z", "source": "cv_upload", "changes": []}
    assert EnrichmentRecord.model_validate(old).extraction_review is None
