# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""F-10 (adversarial pass 2026-11-23) / ADR-095 am. 2026-11-24 — a break is
measured against the last RELEASE, not against a snapshot the same pull
request may rewrite.

`--accept-breaking` rewrites `tests/contract/contract.json`, after which the
snapshot tests compare the live surface with the rewritten file and pass. From
1.0.0 on, the release commit of every regular release writes
`tests/contract/released/<version>.json`, and the live surface is compared with
the newest released snapshot of the current version's major. Only a new major
lifts that. Before 1.0.0 a break stays allowed, and this file says so instead of
skipping.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_contract_snapshot_anchor", _REPO / "scripts" / "generate_contract_snapshot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


@pytest.fixture(scope="module")
def live():
    return gen.live_surface()


def _broken(live: dict) -> dict:
    new = copy.deepcopy(live)
    params = new["mcp_tools"]["send_message"]
    params["text"] = params.pop("message")
    return new


def _released(tmp_path: Path, **snapshots: dict) -> Path:
    directory = tmp_path / "released"
    directory.mkdir()
    for version, surface in snapshots.items():
        (directory / f"{version.replace('_', '.')}.json").write_text(gen.render(surface), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_the_live_surface_keeps_the_promise_of_the_newest_release_of_its_major(live):
    anchor = gen.release_anchor(live, gen.current_version())
    assert anchor["state"] != "missing-own-release", anchor["message"]
    assert not anchor["breaks"], (
        f"This change breaks the surface released as {anchor['anchor']} "
        "(docs/versioning.md). --accept-breaking does not lift this; only a new major does:\n  "
        + "\n  ".join(anchor["breaks"])
    )


def test_before_one_point_oh_breaks_are_allowed_and_this_says_so(live):
    """Not a skip: the state is named, and it switches off by itself at 1.0.0."""
    version = gen.current_version()
    anchor = gen.release_anchor(live, version)
    if gen.parse_version(version)[0] == 0:
        assert anchor["state"] == "pre-1.0"
        assert "before 1.0.0: a break is still allowed" in anchor["message"]
    else:
        assert anchor["state"] in ("compared", "new-major")


def test_every_released_snapshot_is_a_regular_release_from_one_point_oh_on():
    for version in gen.released_versions():
        major, _minor, _patch, pre = gen.parse_version(version)
        assert major >= 1 and pre is None, version
        assert gen.load_snapshot(gen.RELEASED_DIR / f"{version}.json")["contract"] == "applire-1.x"


# --------------------------------------------------------------------------
# Faked versions: both sides of the switch
# --------------------------------------------------------------------------


def test_a_0x_version_may_break(live, tmp_path):
    anchor = gen.release_anchor(_broken(live), "0.47.0", _released(tmp_path))
    assert anchor["state"] == "pre-1.0" and anchor["breaks"] == []


def test_a_1x_minor_that_breaks_the_released_surface_is_red(live, tmp_path):
    released = _released(tmp_path, **{"1_0_0": live})
    anchor = gen.release_anchor(_broken(live), "1.1.0-beta", released)
    assert anchor["state"] == "compared" and anchor["anchor"] == "1.0.0"
    assert "MCP parameter removed or renamed: send_message.message" in anchor["breaks"]


def test_an_unchanged_or_additive_1x_surface_is_green(live, tmp_path):
    released = _released(tmp_path, **{"1_0_0": live})
    added = copy.deepcopy(live)
    added["mcp_tools"]["send_message"]["tone"] = {"required": False, "types": ["null", "string"]}
    assert gen.release_anchor(live, "1.0.1-beta", released)["breaks"] == []
    assert gen.release_anchor(added, "1.1.0-beta", released)["breaks"] == []


def test_the_newest_release_of_the_major_is_the_anchor(live, tmp_path):
    newer = copy.deepcopy(live)
    newer["mcp_tools"]["send_message"]["tone"] = {"required": False, "types": ["null", "string"]}
    released = _released(tmp_path, **{"1_0_0": live, "1_1_0": newer})
    anchor = gen.release_anchor(live, "1.2.0-beta", released)
    assert anchor["anchor"] == "1.1.0"
    assert "MCP parameter removed or renamed: send_message.tone" in anchor["breaks"]


def test_a_new_major_lifts_it_until_its_first_release(live, tmp_path):
    released = _released(tmp_path, **{"1_0_0": live})
    anchor = gen.release_anchor(_broken(live), "2.0.0-beta", released)
    assert anchor["state"] == "new-major" and anchor["breaks"] == []


def test_a_regular_release_without_its_snapshot_is_red(live, tmp_path):
    """The switch at 1.0.0 is automatic: the 1.0.0 release commit cannot be
    green without writing its anchor."""
    anchor = gen.release_anchor(live, "1.0.0", _released(tmp_path))
    assert anchor["state"] == "missing-own-release"
    assert "--release" in anchor["message"]
    second = tmp_path / "with-anchor"
    second.mkdir()
    assert gen.release_anchor(live, "1.0.0", _released(second, **{"1_0_0": live}))["state"] == "compared"


def test_accept_breaking_cannot_silence_the_anchor(live, tmp_path, monkeypatch):
    """The F-10 repro: rewrite contract.json with --accept-breaking, and the
    1.x surface is still red against the released snapshot."""
    target = tmp_path / "contract.json"
    target.write_text(gen.render(live), encoding="utf-8")
    released = _released(tmp_path, **{"1_0_0": live})
    broken = _broken(live)
    monkeypatch.setattr(gen, "TARGET", target)
    monkeypatch.setattr(gen, "RELEASED_DIR", released)
    monkeypatch.setattr(gen, "live_surface", lambda: broken)
    monkeypatch.setattr(gen, "current_version", lambda: "1.1.0-beta")
    assert gen.main(["--accept-breaking"]) == 1
    assert gen.load_snapshot(target) == live  # nothing written
    # even with the snapshot rewritten by hand, --check stays red
    target.write_text(gen.render(broken), encoding="utf-8")
    assert gen.main(["--check"]) == 1
    # the same break before 1.0.0 is the ruled path
    monkeypatch.setattr(gen, "current_version", lambda: "0.47.0-beta")
    target.write_text(gen.render(live), encoding="utf-8")
    assert gen.main(["--accept-breaking"]) == 0
    assert gen.load_snapshot(target) == broken


# --------------------------------------------------------------------------
# --release: the release commit writes the anchor, once
# --------------------------------------------------------------------------


@pytest.fixture
def release_env(live, tmp_path, monkeypatch):
    target = tmp_path / "contract.json"
    target.write_text(gen.render(live), encoding="utf-8")
    released = tmp_path / "released"
    monkeypatch.setattr(gen, "TARGET", target)
    monkeypatch.setattr(gen, "RELEASED_DIR", released)
    monkeypatch.setattr(gen, "live_surface", lambda: live)
    return monkeypatch, released


@pytest.mark.parametrize("version", ["0.47.0", "0.46.0-beta", "1.0.0-rc.1"])
def test_release_refuses_a_version_that_is_not_an_anchor(release_env, version):
    monkeypatch, released = release_env
    monkeypatch.setattr(gen, "current_version", lambda: version)
    assert gen.main(["--release"]) == 2
    assert not released.exists()


def test_release_writes_once_and_refuses_a_rewrite(release_env, live):
    monkeypatch, released = release_env
    monkeypatch.setattr(gen, "current_version", lambda: "1.0.0")
    assert gen.main(["--release"]) == 0
    assert gen.load_snapshot(released / "1.0.0.json") == live
    assert gen.main(["--release"]) == 0  # idempotent on the same surface
    changed = copy.deepcopy(live)
    changed["mcp_tools"]["send_message"]["tone"] = {"required": False, "types": ["null", "string"]}
    monkeypatch.setattr(gen, "live_surface", lambda: changed)
    assert gen.main(["--release"]) == 2  # written once
    assert gen.load_snapshot(released / "1.0.0.json") == live


def test_release_refuses_a_minor_that_breaks_the_previous_release(release_env, live):
    monkeypatch, released = release_env
    released.mkdir()
    (released / "1.0.0.json").write_text(gen.render(live), encoding="utf-8")
    broken = _broken(live)
    gen.TARGET.write_text(gen.render(broken), encoding="utf-8")
    monkeypatch.setattr(gen, "live_surface", lambda: broken)
    monkeypatch.setattr(gen, "current_version", lambda: "1.1.0")
    assert gen.main(["--release"]) == 2
    assert not (released / "1.1.0.json").exists()
