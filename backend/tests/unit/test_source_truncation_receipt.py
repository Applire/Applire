# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build-2 adversarial F-F (founder ruling R3-b) — a CV longer than the
extraction input cap is recorded as such on the import receipt.

Pinned on the adversarial import (job 5db7ad88): a 133 KB text CV with 140
positions was cut DETERMINISTICALLY before the model
(``_MAX_CV_TEXT_CHARS = 25_000``, 133,272 → 24,870 chars, ending inside
position 26); the model extracted all 27 positions it was shown, the review saw
the same cut source and approved, and the receipt said ``not_applied_loss_count:
0`` and ``approved`` — the cut existed only as a log WARNING. The fact now rides
the extraction-review receipt (every door, the history record, the agent
summary): the review covered ``source_chars_read`` of ``source_chars_total``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from applire.schemas.profile import EnrichmentRecord, ExtractionReview

from .test_extraction_review_receipt import _review_settling_on
from .test_615_import_door_adapters import (  # noqa: F401  (fixture re-export)
    _partial_merge_result,
    _seed_first_profile,
    sqlite_session,
)

_LINE = "- Verantwortete Aufgabe: Steigerung der Kennzahl um 30 Prozent im Bereich Logistik.\n"


def _cv(lines: int) -> str:
    return "Katrin Hoffmann\nBerufserfahrung\n" + _LINE * lines


async def _upload(sqlite_session, tmp_path, text):
    from applire.services.profile import upload_cv
    from applire.storage.local import LocalStorageProvider

    await _seed_first_profile(sqlite_session)
    extract = AsyncMock(return_value={"personal_info": {"name": "Katrin Hoffmann"},
                                      "skills": [{"name": "Kubernetes"}]})
    with (
        patch("applire.services.cv_parser.extract_text", new=AsyncMock(return_value=text)),
        patch("applire.services.profile.extract_with_fallback", new=extract),
        patch("applire.services.profile.review_and_refine", new=_review_settling_on("approved")),
        patch("applire.services.profile.annotate_expected_fields", new=AsyncMock(return_value=None)),
        patch("applire.services.profile.enrich_skills", new=AsyncMock(side_effect=lambda p, _: p)),
        patch("applire.services.profile.reconcile_import", new=AsyncMock(return_value=_partial_merge_result())),
    ):
        response = await upload_cv(
            file_bytes=text.encode(), filename="cv.txt", content_type="text/plain",
            db=sqlite_session, provider=AsyncMock(), storage=LocalStorageProvider(str(tmp_path)),
            ocr_extractor=AsyncMock(),
        )
    return response, extract.call_args.args[0]


@pytest.mark.asyncio
async def test_a_cut_source_is_on_the_receipt_and_the_history_record(sqlite_session, tmp_path):
    text = _cv(400)  # ≈ 34,000 chars
    response, seen = await _upload(sqlite_session, tmp_path, text)
    review = response.extraction_review
    assert review is not None and review.source_truncated is True
    assert review.source_chars_total == len(text)
    assert review.source_chars_read == len(seen) < len(text)

    from applire.services.profile import _get_latest

    record = await _get_latest(sqlite_session, None)
    history = [EnrichmentRecord.model_validate(r) for r in record.profile_json["metadata"]["enrichment_history"]]
    assert history[-1].extraction_review.source_truncated is True
    assert history[-1].extraction_review.source_chars_total == len(text)


@pytest.mark.asyncio
async def test_a_source_under_the_cap_says_nothing_was_cut(sqlite_session, tmp_path):
    response, seen = await _upload(sqlite_session, tmp_path, _cv(20))
    assert seen == _cv(20)
    assert response.extraction_review.source_truncated is False
    assert response.extraction_review.source_chars_read is None


def test_a_receipt_persisted_before_the_field_loads_unchanged():
    old = ExtractionReview.model_validate({"status": "approved", "path": "approved", "rounds": 1})
    assert old.source_truncated is False and old.source_chars_total is None


def test_the_agent_summary_states_the_cut():
    import uuid
    from datetime import datetime, timezone

    from applire.mcp.server import _profile_summary
    from applire.schemas.profile import MasterProfileData, ProfileImportResponse, ProfileStats

    def summary(review):
        return _profile_summary(ProfileImportResponse(
            id=uuid.uuid4(), profile=MasterProfileData(), completeness=0.7,
            stats=ProfileStats(positions=27), created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc), extraction_review=review,
        ))

    cut = ExtractionReview(status="approved", path="approved", rounds=1, source_truncated=True,
                           source_chars_read=24870, source_chars_total=133272)
    assert summary(cut)["source_truncated"] == {"chars_read": 24870, "chars_total": 133272}
    assert summary(ExtractionReview(status="approved"))["source_truncated"] is None
    assert summary(None)["source_truncated"] is None
