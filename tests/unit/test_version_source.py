# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L18 — `__version__` (and so `/health.version`) comes from the source tree's
pyproject.toml when there is one, in the PEP 440 spelling the package metadata
uses; a stale egg-info in a dev clone can no longer make the instance lie."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from applire import _version

_PYPROJECT = Path(__file__).resolve().parents[2] / "backend" / "pyproject.toml"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0.44.0-beta", "0.44.0b0"),
        ("1.0.0", "1.0.0"),
        ("1.1.0-rc.2", "1.1.0rc2"),
        ("v2.0.0-alpha1", "2.0.0a1"),
        ("not a version", None),
    ],
)
def test_normalise_matches_pep440(raw, expected):
    assert _version._normalise(raw) == expected


def test_the_running_version_is_the_pyproject_version():
    raw = re.search(r'^version\s*=\s*"([^"]+)"', _PYPROJECT.read_text(), re.M).group(1)
    assert _version.__version__ == _version._normalise(raw)


def test_a_stale_installed_metadata_does_not_win(monkeypatch):
    """The 2026-10-20 dev-stack shape: metadata says 0.37.2b0, the tree says otherwise."""
    monkeypatch.setattr(_version, "version", lambda _name: "0.37.2b0")
    assert _version._resolve() == _version._from_source_tree() != "0.37.2b0"


def test_without_a_source_tree_the_metadata_answers(monkeypatch):
    monkeypatch.setattr(_version, "_from_source_tree", lambda: None)
    monkeypatch.setattr(_version, "version", lambda _name: "1.2.3")
    assert _version._resolve() == "1.2.3"
