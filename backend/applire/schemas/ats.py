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

import uuid
from typing import Literal, Optional

from pydantic import BaseModel


class ATSCheck(BaseModel):
    id: str                      # stable machine id, e.g. "contact-name", "work-2", "reading-order"
    status: Literal["pass", "fail"]
    details: Optional[str] = None  # human-readable EN diagnostic; frontend translates labels by id


class ATSKeywordCoverage(BaseModel):
    present: list[str] = []
    missing: list[str] = []


class ATSReport(BaseModel):
    version: int = 1
    document: Literal["cv", "cover_letter"]
    checks: list[ATSCheck]
    keywords: ATSKeywordCoverage
    # convenience counts — NOT a score (ADR-039/ADR-035): the UI shows the list, never a percentage
    passed: int
    failed: int


class ATSReportResponse(BaseModel):
    document_id: uuid.UUID
    status: str                  # generation status of the underlying document
    report: Optional[ATSReport] = None   # null while pending/failed or when the audit engine errored
