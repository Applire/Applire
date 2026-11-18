# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A reduced content line keeps its template (ADR-017 amended 2026-11-18), so
every template must be code text: no ``debug`` call in ``applire`` builds its
message at runtime (an f-string, ``%`` or ``.format`` in the first argument).
The values go in the arguments, which a reduced line drops.

Also: the content files are created 0600 in a 0700 folder, whatever the
process umask (the shipped containers run with umask 0000).
"""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

from applire import content_log

PACKAGE = Path(__file__).resolve().parents[2] / "applire"


def _built(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp):
        return True
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "format"


def built_debug_messages(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "debug"
                and node.args
                and _built(node.args[0])
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


def test_no_debug_call_builds_its_message_at_runtime():
    assert built_debug_messages(PACKAGE) == []


def test_the_scan_sees_a_built_message(tmp_path):
    (tmp_path / "m.py").write_text(
        'log.debug(f"answer {text}")\nlog.debug("a %s" % x)\nlog.debug("ok %s", x)\n', "utf-8"
    )
    assert built_debug_messages(tmp_path) == ["m.py:1", "m.py:2"]


def test_content_files_are_private_whatever_the_umask(tmp_path):
    old = os.umask(0)
    try:
        directory = tmp_path / "content"
        content_log.append_jsonl(str(directory), {"n": 1})
    finally:
        os.umask(old)
    files = sorted(directory.iterdir())
    assert [p.name for p in files] == sorted([content_log.DIRECTORY_LOCK, f"{content_log._today()}.jsonl"])
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    for path in files:  # the day's file and the writers' lock file
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path.name
