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
"""Package version lookup.

Lives in a real submodule (not the top-level package marker) because `applire`
is a PEP 420 namespace package — it has no top-level `__init__.py`, so it cannot
carry module-level attributes itself. Consumed by `applire.main` and
`applire.routers.health`. (ADR-031, amended 2026-06-03.)
"""
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

#: ``X.Y.Z`` plus an optional pre-release suffix as the project writes it in
#: ``pyproject.toml`` (``0.44.0-beta``, ``1.1.0-rc.2``, ``1.0.0``).
_PEP440_PRE = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "rc": "rc", "c": "rc"}
_VERSION_RE = re.compile(
    r"^\s*v?(\d+\.\d+\.\d+)(?:[-_.]?(alpha|beta|rc|a|b|c)[-_.]?(\d+)?)?\s*$", re.I
)


def _normalise(raw: str) -> str | None:
    """``0.44.0-beta`` -> ``0.44.0b0`` — the PEP 440 spelling that
    ``importlib.metadata`` reports for the same release, so ``/health.version``
    reads identically whichever source answered. None for anything else."""
    m = _VERSION_RE.match(raw)
    if not m:
        return None
    base, pre, num = m.group(1), m.group(2), m.group(3)
    if not pre:
        return base
    return f"{base}{_PEP440_PRE[pre.lower()]}{num or 0}"


def _from_source_tree() -> str | None:
    """The version of the ``pyproject.toml`` beside this package, if any.

    Every image (``COPY . .`` into ``/app``) and every clone has one. It wins
    over the installed metadata because the metadata can be stale: the dev
    override mounts a clone's ``backend/`` over ``/app``, and a gitignored
    ``applire.egg-info`` left there by an old ``pip install -e`` made the dev
    stack's ``/health`` report ``0.37.2b0`` while it ran 0.44.0-beta
    (2026-10-20, 785:L18). A wheel install has no adjacent file and falls
    through to the metadata.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'^version\s*=\s*"([^"]+)"', stripped)
            if m:
                return _normalise(m.group(1))
    return None


def _resolve() -> str:
    from_tree = _from_source_tree()
    if from_tree:
        return from_tree
    try:
        return version("applire")
    except PackageNotFoundError:
        return "unknown"


__version__ = _resolve()
