# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The admin's notices follow the one logging switch (ADR-087 cl. 9 amended
2026-11-18): ``debug_log_on`` at LOG_LEVEL=DEBUG only, and
``log_consent_settings_missing`` while the consent text has no controller or
contact (WPL-1)."""

import pytest

from applire.config import settings
from applire.services.admin import dashboard


def _codes():
    return [n["code"] for n in dashboard._setting_notices(last_run_skipped=False)]


@pytest.fixture
def named(monkeypatch):
    monkeypatch.setattr(settings, "log_consent_operator_name", "Operator Example")
    monkeypatch.setattr(settings, "log_consent_contact_email", "kontakt@example.org")


def test_info_has_neither_notice_even_with_the_retired_switch(monkeypatch):
    monkeypatch.setattr(settings, "log_level", "INFO")
    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "log_consent_operator_name", "")
    codes = _codes()
    assert "debug_log_on" not in codes and "log_consent_settings_missing" not in codes


def test_debug_with_controller_and_contact_set(monkeypatch, named):
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    codes = _codes()
    assert "debug_log_on" in codes and "log_consent_settings_missing" not in codes


@pytest.mark.parametrize("unset", ["log_consent_operator_name", "log_consent_contact_email"])
def test_debug_with_controller_or_contact_unset(monkeypatch, named, unset):
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    monkeypatch.setattr(settings, unset, "")
    assert "log_consent_settings_missing" in _codes()
