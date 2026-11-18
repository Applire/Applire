# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the instance says about its content logs is what the code does
(ADR-017 / ADR-087 amended 2026-11-18).

The startup line, the consent text and the self-hosting guide promise three
things: daily files past the age limit are deleted, a withdrawn or deleted
person's text is removed, and the worker's rewrite never races a writer. Each
promise is tied here to the mechanism that keeps it, so removing a mechanism
while a text still claims it fails a named test.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import time
from pathlib import Path

import pytest

from applire.config import settings
from applire.retention import content_logs, worker

ROOT = Path(__file__).resolve().parents[3]


def _registered_rules() -> set[str]:
    tree = ast.parse(inspect.getsource(worker._sweep_unscoped))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "rule" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def test_the_startup_line_promises_deletion_only_with_both_rules_in_the_sweep(monkeypatch):
    from applire.main import startup_posture_lines

    monkeypatch.setattr(settings, "log_level", "DEBUG")
    monkeypatch.setattr(settings, "content_log_retention_days", 30)
    line = startup_posture_lines()[0]
    assert "Files older than 30 days are deleted by the retention worker" in line
    assert {"content_log_age", "content_log_withdrawn"} <= _registered_rules()


def test_the_guide_and_the_consent_text_promise_what_the_sweep_does():
    guide = (ROOT / "docs" / "SELF-HOSTING.md").read_text("utf-8")
    assert "the nightly cleanup removes that person's text from both logs" in guide
    assert "`CONTENT_LOG_RETENTION_DAYS` deletes both logs' daily files" in guide
    de = json.loads((ROOT / "frontend" / "messages" / "de.json").read_text("utf-8"))
    assert "Danach werden die Einträge automatisch gelöscht" in de["logConsent"]["text"]["durationBody"]
    assert "Wir löschen dann innerhalb von 48 Stunden" in de["logConsent"]["text"]["withdrawBody"]
    assert {"content_log_age", "content_log_withdrawn"} <= _registered_rules()


def test_the_rewrite_waits_for_a_writer_holding_the_lock(tmp_path):
    """The worker takes the same exclusive flock as ``append_jsonl``: while a
    writer holds it, the rewrite does not replace the file."""
    import fcntl

    path = tmp_path / "2026-11-18.jsonl"
    path.write_text(json.dumps({"user_id": "u-gone", "ts": "2026-11-18T08:00:00+00:00",
                                "template": "t", "message": "Weberit"}) + "\n", "utf-8")
    done = threading.Event()

    def rewrite():
        content_logs.rewrite_file(path, {"u-gone": "all"})
        done.set()

    with open(path, "a", encoding="utf-8") as writer:
        fcntl.flock(writer.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=rewrite)
        t.start()
        time.sleep(0.3)
        assert not done.is_set(), "the rewrite ran while a writer held the lock"
        fcntl.flock(writer.fileno(), fcntl.LOCK_UN)
    t.join(5)
    assert done.is_set()
    assert "Weberit" not in path.read_text("utf-8")
