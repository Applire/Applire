# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L95 — `nginx/15-applire-trusted-proxy.sh` refuses at start a trusted-proxy
range wider than /16 (IPv4) or /48 (IPv6), naming the entry (founder ruling
2026-10-10, JF-O-K2.5 / SF-IAM.6): `0.0.0.0/0` would hand every client its own
throttle identity via X-Forwarded-For. Docker's /16 bridge stays allowed.
Runs the real script with POSIX `sh`, output directory as its argument."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "nginx" / "15-applire-trusted-proxy.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _run(value: str, tmp_path: Path) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if k != "APPLIRE_TRUSTED_PROXY"}
    env["APPLIRE_TRUSTED_PROXY"] = value
    proc = subprocess.run(
        ["sh", str(_SCRIPT), str(tmp_path / "out")], env=env, capture_output=True, text=True
    )
    conf = tmp_path / "out" / "trusted-proxy.conf"
    return proc.returncode, proc.stderr, conf.read_text() if conf.exists() else ""


@pytest.mark.parametrize(
    "value",
    ["172.18.0.0/16", "10.0.0.5", "192.168.1.0/24", "10.0.0.0/16,192.168.1.10",
     "fd00::/48", "2001:db8::1", "fd00:1234::/64"],
)
def test_narrow_ranges_and_addresses_are_trusted(value, tmp_path):
    code, err, conf = _run(value, tmp_path)
    assert code == 0, err
    for entry in value.split(","):
        assert f"set_real_ip_from {entry};" in conf


@pytest.mark.parametrize(
    "value, named",
    [("0.0.0.0/0", "0.0.0.0/0"), ("10.0.0.0/8", "10.0.0.0/8"), ("172.16.0.0/12", "172.16.0.0/12"),
     ("10.0.0.0/15", "10.0.0.0/15"), ("::/0", "::/0"), ("2001:db8::/32", "2001:db8::/32"),
     ("fd00::/47", "fd00::/47"), ("192.168.1.0/24,0.0.0.0/0", "0.0.0.0/0")],
)
def test_wide_ranges_refuse_the_start_naming_the_entry(value, named, tmp_path):
    code, err, _ = _run(value, tmp_path)
    assert code == 1
    assert f"'{named}'" in err and "wider than" in err


def test_malformed_entries_still_refuse(tmp_path):
    code, err, _ = _run("not-an-ip", tmp_path)
    assert code == 1 and "is not an IP address or CIDR range" in err


def test_empty_value_trusts_nobody(tmp_path):
    code, _, conf = _run("", tmp_path)
    assert code == 0 and conf == ""
