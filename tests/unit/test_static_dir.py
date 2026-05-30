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

"""Unit tests for static-asset directory resolution (bug 5 regression).

The template-thumbnail 404s were caused by STATIC_DIR defaulting to the
CWD-relative "./data/static": launching the backend from any directory other
than backend/ served an empty directory. resolve_static_dir() must return a
stable absolute path regardless of the process working directory.
"""
import os
from pathlib import Path

from applire.config import resolve_static_dir


def test_default_is_absolute_and_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("STATIC_DIR", raising=False)

    monkeypatch.chdir(tmp_path)
    from_tmp = resolve_static_dir()

    monkeypatch.chdir(Path(from_tmp).anchor)  # filesystem root
    from_root = resolve_static_dir()

    assert from_tmp.is_absolute()
    assert from_tmp == from_root  # CWD must not affect the result
    assert from_tmp.parts[-2:] == ("data", "static")
    # Anchored to the backend package root (parent of the applire/ package)
    assert from_tmp.parent.name == "data"


def test_env_override_is_honoured(monkeypatch, tmp_path):
    target = tmp_path / "custom_static"
    monkeypatch.setenv("STATIC_DIR", str(target))

    resolved = resolve_static_dir()

    assert resolved == target.resolve()
    assert resolved.is_absolute()
