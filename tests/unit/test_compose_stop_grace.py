# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A clean stop gets the time it needs (adv/plans re-probe 3, R3-3a).

On SIGTERM the backend first waits for its background renders — a render
whose record could not be ended retries its failure mark for up to
``render_slots.SETTLE_SECONDS`` — and then writes the queued usage rows for up
to ``usage.SHUTDOWN_DRAIN_SECONDS``. Docker's default grace is 10 s, after
which it kills the process and the drain never runs. Every compose file that
runs the backend gives it at least that bound: the shipped file sets it, and
no overlay (dev, CI, edge) sets a lower one.
"""

import inspect
import re
from pathlib import Path

import pytest
import yaml

from applire.providers.llm import usage
from applire.services import render_slots

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO_ROOT / "docker-compose.yml"
_OVERLAYS = sorted(p for p in _REPO_ROOT.glob("docker-compose.*.yml"))

_DRAIN_BOUND = render_slots.SETTLE_SECONDS + usage.SHUTDOWN_DRAIN_SECONDS


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


def test_the_shipped_backend_waits_longer_than_its_drain_bound():
    grace = _backend(_SHIPPED).get("stop_grace_period")
    assert grace is not None, "docker-compose.yml: backend has no stop_grace_period (docker's default is 10 s)"
    assert _seconds(grace) >= _DRAIN_BOUND


def test_there_are_overlays_to_check():
    assert {p.name for p in _OVERLAYS} >= {
        "docker-compose.override.yml", "docker-compose.ci.yml", "docker-compose.ci-auth.yml", "docker-compose.edge.yml",
    }


@pytest.mark.parametrize("overlay", _OVERLAYS, ids=lambda p: p.name)
def test_no_overlay_shortens_the_backend_grace(overlay):
    grace = _backend(overlay).get("stop_grace_period")
    if grace is not None:
        assert _seconds(grace) >= _DRAIN_BOUND


def test_the_shutdown_drains_for_the_named_bound():
    from applire import main

    assert "drain_usage(timeout=SHUTDOWN_DRAIN_SECONDS)" in inspect.getsource(main.lifespan)


@pytest.mark.parametrize("text,seconds", [("90s", 90), ("1m30s", 90), ("2m", 120), ("10s", 10)])
def test_durations_are_read_as_compose_writes_them(text, seconds):
    assert _seconds(text) == seconds
