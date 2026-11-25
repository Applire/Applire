# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean stop fits the container's grace (adv/plans re-probes 3 and 4, R3-3a, R4-1).

On SIGTERM uvicorn waits for running renders only for its
``--timeout-graceful-shutdown``, then cancels them. The lifespan then waits up
to ``render_slots.SHUTDOWN_WAIT_SECONDS`` for their failure marks and writes the
queued usage rows for up to ``usage.SHUTDOWN_DRAIN_SECONDS``. Docker's default
grace is 10 s, after which it kills the process. So:

* every place that defines the backend's command line (the image's ``CMD``, any
  compose ``command:``) bounds uvicorn's wait, with one value;
* that value + wait + drain + headroom fits the backend's ``stop_grace_period``
  in the shipped compose file, and no overlay (dev, CI, edge) sets a lower one.
"""

import inspect
import json
import re
import shlex
from pathlib import Path

import pytest
import yaml

from applire.providers.llm import usage
from applire.services import render_slots

#: These tests prove the real settle window and shutdown bounds (backend/tests/support/settle_window.py).
pytestmark = pytest.mark.real_settle

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO_ROOT / "docker-compose.yml"
_OVERLAYS = sorted(p for p in _REPO_ROOT.glob("docker-compose.*.yml"))
_DOCKERFILE = _REPO_ROOT / "backend" / "Dockerfile"
_FLAG = "--timeout-graceful-shutdown"
_HEADROOM_SECONDS = 5


def _graceful_timeout(argv: list[str]) -> float | None:
    for i, arg in enumerate(argv):
        if arg == _FLAG:
            return float(argv[i + 1])
        if arg.startswith(_FLAG + "="):
            return float(arg.split("=", 1)[1])
    return None


def _image_cmd() -> list[str]:
    lines = [ln for ln in _DOCKERFILE.read_text(encoding="utf-8").splitlines() if ln.startswith("CMD ")]
    assert len(lines) == 1, "backend/Dockerfile: exactly one CMD"
    return json.loads(lines[0][4:])


def _backend_commands() -> dict[str, list[str]]:
    """Every backend command line: the image's CMD and each compose override."""
    out = {"backend/Dockerfile CMD": _image_cmd()}
    for path in [_SHIPPED, *_OVERLAYS]:
        cmd = _backend(path).get("command")
        if cmd is not None:
            out[f"{path.name} backend.command"] = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    return out


def _stop_bound() -> float:
    timeout = _graceful_timeout(_image_cmd())
    assert timeout is not None
    return timeout + render_slots.SHUTDOWN_WAIT_SECONDS + usage.SHUTDOWN_DRAIN_SECONDS + _HEADROOM_SECONDS


def _seconds(value: str) -> float:
    """Compose duration ("90s", "1m30s", "2m") in seconds."""
    parts = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", str(value).strip())
    assert parts and any(parts.groups()), f"unreadable duration {value!r}"
    minutes, seconds = (int(g) if g else 0 for g in parts.groups())
    return minutes * 60 + seconds


class _ComposeLoader(yaml.SafeLoader):
    """Reads compose's merge tags (``!override``, ``!reset``) as plain values."""


def _untagged(loader, _suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _untagged)


def _backend(path: Path) -> dict:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506 — SafeLoader subclass
    return (data.get("services") or {}).get("backend") or {}


def test_every_backend_command_line_bounds_the_servers_wait_with_one_value():
    commands = _backend_commands()
    assert len(commands) >= 2  # the image's CMD and the dev overlay's --reload command
    values = {}
    for where, argv in commands.items():
        if "uvicorn" in argv[:3]:
            values[where] = _graceful_timeout(argv)
            assert values[where] is not None, f"{where}: uvicorn without {_FLAG} waits for renders forever"
    assert "backend/Dockerfile CMD" in values
    assert len(set(values.values())) == 1, values


def test_the_shipped_grace_fits_the_servers_wait_the_marks_and_the_drain():
    grace = _backend(_SHIPPED).get("stop_grace_period")
    assert grace is not None, "docker-compose.yml: backend has no stop_grace_period (docker's default is 10 s)"
    assert _seconds(grace) >= _stop_bound()


def test_there_are_overlays_to_check():
    assert {p.name for p in _OVERLAYS} >= {
        "docker-compose.override.yml", "docker-compose.ci.yml", "docker-compose.ci-auth.yml", "docker-compose.edge.yml",
    }


@pytest.mark.parametrize("overlay", _OVERLAYS, ids=lambda p: p.name)
def test_no_overlay_shortens_the_backend_grace(overlay):
    grace = _backend(overlay).get("stop_grace_period")
    if grace is not None:
        assert _seconds(grace) >= _stop_bound()


def test_the_bound_is_what_the_numbers_say():
    # 10 s server wait + 65 s for the marks + 10 s drain + 5 s headroom = the 90 s grace
    assert _stop_bound() == 90


def test_the_shutdown_drains_for_the_named_bound():
    from applire import main

    assert "drain_usage(timeout=SHUTDOWN_DRAIN_SECONDS)" in inspect.getsource(main.lifespan)


@pytest.mark.parametrize("text,seconds", [("90s", 90), ("1m30s", 90), ("2m", 120), ("10s", 10)])
def test_durations_are_read_as_compose_writes_them(text, seconds):
    assert _seconds(text) == seconds
