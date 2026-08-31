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

"""E057 / ADR-079 clause 4 groundwork (epic #629, story #637) — a THIRD
``ATSCheck`` status, ``not_applicable``, for a check that genuinely cannot be
evaluated on the artefact (ADR-079 clause 4: the page-length band on a
``.docx`` export, which has no fixed pagination until a renderer lays it
out — "emitted as an explicit not_applicable with its reason ... never
folded into an X of Y numerator or denominator").

This module builds no producer: nothing here (or in product code touched by
this change) ever constructs a ``not_applicable`` check as part of a real
document audit. Who emits the band is a separate, pending founder decision.
These tests prove the SCHEMA and the ``_finish()`` COUNTER can carry a
``not_applicable`` check, correctly excluded from ``passed``/``failed``,
using synthetic ``ATSCheck`` fixtures — the same way ``test_ats_audit.py``
already probes ``_finish`` and its callers with hand-built checks.
"""

import pytest
from pydantic import ValidationError

from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
from applire.services.ats_audit import _finish


def test_atscheck_accepts_not_applicable_status():
    """Before this change ``ATSCheck.status`` is ``Literal["pass", "fail"]``
    and this construction raises ``ValidationError``."""
    check = ATSCheck(id="page-length", status="not_applicable")
    assert check.status == "not_applicable"


def test_atscheck_still_rejects_an_unknown_status():
    """The widening is to exactly three values, not an open string — a
    fourth invented status (e.g. a future "warn") must still be rejected."""
    with pytest.raises(ValidationError):
        ATSCheck(id="page-length", status="warn")


def test_atsreport_not_applicable_defaults_to_none_for_legacy_payload():
    """Back-compat semantics, mirroring ``PinnedFactReportEntry.ledger_conflict``
    (schemas/ats.py): a persisted JSONB report from before this field existed
    has no ``not_applicable`` key at all. ``None`` marks that — the report
    predates the three-value schema — never a silent ``0`` that would read
    as "measured, confirmed clean" for a report that was never given the
    chance to carry the concept."""
    legacy_payload = {
        "document": "cv",
        "checks": [{"id": "contact-name", "status": "pass"}],
        "keywords": {"present": [], "missing": []},
        "passed": 1,
        "failed": 0,
        # no "not_applicable" key at all — exactly the shape of every
        # report persisted before this migration.
    }
    report = ATSReport.model_validate(legacy_payload)
    assert report.not_applicable is None


def test_finish_counts_not_applicable_in_its_own_bucket_never_in_passed_or_failed():
    """The load-bearing assertion at the backend-schema layer: a report
    containing a not_applicable check has it counted in NEITHER passed NOR
    failed."""
    checks = [
        ATSCheck(id="contact-name", status="pass"),
        ATSCheck(id="contact-email", status="fail"),
        ATSCheck(id="page-length", status="not_applicable"),
    ]
    coverage = ATSKeywordCoverage(present=[], missing=[])

    report = _finish("cv", checks, coverage)

    assert report.passed == 1
    assert report.failed == 1
    assert report.not_applicable == 1
    # Never folded into an X-of-Y that treats it as either bucket.
    assert report.passed + report.failed == 2
    assert len(report.checks) == 3


def test_finish_not_applicable_when_every_check_is_not_applicable():
    checks = [ATSCheck(id="page-length", status="not_applicable")]
    coverage = ATSKeywordCoverage(present=[], missing=[])

    report = _finish("cover_letter", checks, coverage)

    assert report.passed == 0
    assert report.failed == 0
    assert report.not_applicable == 1


def test_finish_reports_a_genuine_measured_zero_when_no_check_is_not_applicable():
    """A freshly generated (post-schema-change) report with no N/A checks
    reports a real, measured zero — never None. None is reserved for
    reports that predate the field entirely (see the back-compat test
    above); every report ``_finish()`` produces from here on always
    populates this field."""
    checks = [ATSCheck(id="contact-name", status="pass")]
    coverage = ATSKeywordCoverage(present=[], missing=[])

    report = _finish("cv", checks, coverage)

    assert report.not_applicable == 0
    assert report.not_applicable is not None
