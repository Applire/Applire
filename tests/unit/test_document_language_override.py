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

"""E054 / ADR-038 amendment 2026-08-23 — user override over document language.

The override lives on ``applications.language_override`` (per-user entity),
NEVER on the hash-deduplicated ``job_analyses`` row. A new seam
``resolve_document_language(application, job)`` sits ABOVE the unchanged
detection primitive ``resolve_jd_language``. Generated documents pin their
own ``document_language`` at generation (amendment clause 3b) so read paths
never re-resolve against a mutable override.

No Docker, no LLM.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


GERMAN_JD = (
    "Wir suchen eine Buchhalterin für unsere Kanzlei in München. Sie "
    "verantworten die Abschlüsse und führen ein kleines Team."
)


class TestResolveDocumentLanguage:
    """Amendment clause 2 — override first, detection as fallback."""

    def _seam(self):
        from applire.utils.language_detection import resolve_document_language

        return resolve_document_language

    def test_override_beats_stored_jd_language(self):
        seam = self._seam()
        application = SimpleNamespace(language_override="en")
        job = SimpleNamespace(jd_language="de", raw_text=GERMAN_JD)
        assert seam(application, job) == "en"

    def test_null_override_falls_back_to_stored_jd_language(self):
        seam = self._seam()
        application = SimpleNamespace(language_override=None)
        job = SimpleNamespace(jd_language="de", raw_text=GERMAN_JD)
        assert seam(application, job) == "de"

    def test_null_override_and_null_column_fall_back_to_detection(self):
        seam = self._seam()
        application = SimpleNamespace(language_override=None)
        job = SimpleNamespace(jd_language=None, raw_text=GERMAN_JD)
        assert seam(application, job) == "de"

    def test_missing_application_falls_back_to_resolve_jd_language(self):
        # Job-scoped contexts without an Application row (defensive: the seam
        # must never require the join to have succeeded).
        seam = self._seam()
        job = SimpleNamespace(jd_language="en", raw_text="")
        assert seam(None, job) == "en"


class TestModelColumns:
    """Amendment clauses 1 + 3b — storage locations."""

    def test_application_has_language_override_column(self):
        from applire.models.application import Application

        col = Application.__table__.columns["language_override"]
        assert col.nullable is True

    def test_generated_cv_pins_document_language(self):
        from applire.models.cv import GeneratedCV

        col = GeneratedCV.__table__.columns["document_language"]
        assert col.nullable is True

    def test_generated_cover_letter_pins_document_language(self):
        from applire.models.cover_letter import GeneratedCoverLetter

        col = GeneratedCoverLetter.__table__.columns["document_language"]
        assert col.nullable is True

    def test_job_analyses_does_NOT_gain_the_override(self):
        # Clause 1: the hash-deduplicated, user-less row must never carry a
        # user preference — a regression here is a cross-user write in a
        # shared DB.
        from applire.models.job import JobAnalysis

        assert "language_override" not in JobAnalysis.__table__.columns
