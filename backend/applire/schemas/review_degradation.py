# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The door shape of a document's review-degradation record (ADR-081 cl. 9 amended
2026-10-22, #786). Producer: ``services/review_degradation.py``."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DegradedReviewStep(BaseModel):
    """One review step of the writing loop that did not do its job.

    ``check`` is the loop's chain id (``cv_tailoring``, ``cv_language``,
    ``cv_language_recheck``, ``cv_terminal_review``, ``cover_letter``,
    ``letter_terminal_review``); ``stage`` ``review`` = the reviewer gave no
    verdict (the draft shipped un-reviewed), ``correction`` = the verdict's
    findings were not acted on (the last known-good draft shipped); ``reason`` is
    the failure class; ``attempt`` the first loop attempt it happened on; ``count``
    how often it happened in this generation.
    """

    check: str
    stage: Literal["review", "correction"]
    reason: Literal["timeout", "truncated", "malformed", "shape", "error"]
    attempt: int = 1
    count: int = 1
