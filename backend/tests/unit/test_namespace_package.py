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
"""Guards the PEP 420 namespace contract that lets `applire` (Core) and
`applire.cloud` (Cloud) coexist across two separate editable-install roots in
development (ADR-031, amended 2026-06-03). Production is unaffected — there the
overlay is COPY'd into Core's `applire/` dir as a physical subpackage.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

CORE_BACKEND = Path(__file__).resolve().parents[2]  # .../applire-core/backend


def test_applire_namespace_spans_two_roots(tmp_path):
    """A second sys.path root contributing `applire/<sub>/` must be importable as
    `applire.<sub>` — exactly the dev scenario where Cloud lives in its own repo.
    Fails while Core ships a top-level `applire/__init__.py` (regular package:
    `__path__` is pinned to one directory and cannot span the second root).
    """
    second_root = tmp_path / "second_root"
    cloud_probe = second_root / "applire" / "cloud_probe"
    cloud_probe.mkdir(parents=True)
    (cloud_probe / "__init__.py").write_text('MARKER = "from-second-root"\n')
    # Deliberately NO `applire/__init__.py` in second_root — it is a namespace portion.

    code = textwrap.dedent(
        """
        import importlib
        import applire                      # must resolve as a namespace package
        m = importlib.import_module("applire.cloud_probe")
        print(m.MARKER)
        """
    )
    env = dict(os.environ)
    # Core backend first, the simulated Cloud root second — both must contribute to __path__.
    env["PYTHONPATH"] = os.pathsep.join([str(CORE_BACKEND), str(second_root)])

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, (
        "import applire.cloud_probe from a second root failed — "
        f"applire is not a namespace package.\nSTDOUT={result.stdout!r}\nSTDERR={result.stderr!r}"
    )
    assert "from-second-root" in result.stdout


def test_version_is_exposed_from_version_module():
    """`__version__` moves out of the (now-deleted) top-level package marker into a
    real submodule so a namespace package can still expose it (consumed by
    `applire.main` and `applire.routers.health`)."""
    from applire._version import __version__

    assert isinstance(__version__, str) and __version__
