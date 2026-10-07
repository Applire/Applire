# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""Effective posting labels — the per-user title/company for a shared posting.

ADR-092 cl. 5(f) / S-17 / RD-2: a ``job_analyses`` row is one shared,
immutable posting analysis; what a person calls the role and the employer
lives on their own ``applications`` row. Every read of
``job.role_title`` / ``job.company_name`` that renders for a user goes
through :func:`effective_posting_labels` (the W2 package owning each reader
wires it in; Strawberry W0 freezes the signature and ships the working rule).

Rule: an application value wins over the posting's when it is present and
not blank; otherwise the posting's value stands. ``application`` may be
``None`` (no link yet) — the posting's values are returned unchanged.
"""

from __future__ import annotations

from typing import Any

__all__ = ["effective_posting_labels", "posting_response"]


def _present(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def effective_posting_labels(job: Any, application: Any | None) -> tuple[str, str | None]:
    """Return ``(role_title, company_name)`` as this user sees the posting.

    ``job`` is a ``JobAnalysis`` (or anything carrying ``role_title`` /
    ``company_name``); ``application`` is the caller's ``Application`` for
    that posting, or ``None``.
    """
    role_title = job.role_title
    company_name = getattr(job, "company_name", None)
    if application is not None:
        app_role = getattr(application, "role_title", None)
        app_company = getattr(application, "company_name", None)
        if _present(app_role):
            role_title = app_role
        if _present(app_company):
            company_name = app_company
    return role_title, company_name


def posting_response(job: Any, application: Any | None):
    """The shared posting as ONE caller sees it — the only builder of a
    ``JobAnalysisResponse`` (REST ``POST /api/job/analyze`` + ``GET
    /api/job/{id}``, MCP ``analyze_jd`` + ``job://``).

    ``job_analyses`` is a shared cache (S-17): its ``source_url`` is the FIRST
    analyser's URL and may carry their recruiter/tracking token. MD-31
    (adv-own-101/102, 2026-10-05): the response's ``source_url`` comes from the
    caller's own ``applications`` row, or is ``None`` — never the shared row's
    value. The labels are the caller's too (:func:`effective_posting_labels`,
    ADR-092 cl. 5f). ``application`` is the caller's link row (a soft-deleted
    hidden repost link included) or ``None``.
    """
    from applire.schemas.job import JobAnalysisResponse

    response = JobAnalysisResponse.model_validate(job)
    response.role_title, response.company_name = effective_posting_labels(job, application)
    response.source_url = getattr(application, "source_url", None) if application is not None else None
    return response
