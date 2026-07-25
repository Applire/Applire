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

"""#259 — operator-configurable interview question budget.

INTERVIEW_MAX_QUESTIONS_TARGETED / INTERVIEW_MAX_QUESTIONS_GUIDED (env vars)
read via config.Settings, defaulting to the pre-#259 hardcoded ceilings in
constants.py.

Builds a FRESH Settings() instance per test rather than reloading
applire.config / mutating the process-wide `settings` singleton: a module
reload rebinds `applire.config.settings` to a new object, but any module that
already did `from applire.config import settings` (e.g. services/session.py)
keeps its OLD reference — a reload here would desync those and break
unrelated tests that patch the singleton later in the same test run
(discovered empirically: it broke the LLM-provider factory tests when this
file's reload ran first in a full-suite alpha-sorted run).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _settings(**overrides):
    from applire.config import Settings
    return Settings(database_url="sqlite+aiosqlite:///:memory:", **overrides)


def test_defaults_match_constants_when_unset(monkeypatch):
    monkeypatch.delenv("INTERVIEW_MAX_QUESTIONS_TARGETED", raising=False)
    monkeypatch.delenv("INTERVIEW_MAX_QUESTIONS_GUIDED", raising=False)
    from applire.constants import (
        INTERVIEW_HARD_CEILING_GUIDED,
        INTERVIEW_HARD_CEILING_TARGETED,
    )
    s = _settings()
    assert s.interview_max_questions_targeted == INTERVIEW_HARD_CEILING_TARGETED
    assert s.interview_max_questions_guided == INTERVIEW_HARD_CEILING_GUIDED


def test_env_override_targeted(monkeypatch):
    monkeypatch.setenv("INTERVIEW_MAX_QUESTIONS_TARGETED", "25")
    s = _settings()
    assert s.interview_max_questions_targeted == 25


def test_env_override_guided(monkeypatch):
    monkeypatch.setenv("INTERVIEW_MAX_QUESTIONS_GUIDED", "40")
    s = _settings()
    assert s.interview_max_questions_guided == 40
