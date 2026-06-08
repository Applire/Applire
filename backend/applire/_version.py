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
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("applire")
except PackageNotFoundError:
    __version__ = "unknown"
