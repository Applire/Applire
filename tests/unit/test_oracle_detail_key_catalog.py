# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""B3-10 bug-fix part (WP-FO, 2026-10-23): a verdict detail is said in the UI
language. Every ``detail_key`` the Oracle can emit has a translation in BOTH
catalogs (``oracleDetail.<key>``) — a key the catalog lacks falls back to the
English ``detail`` on a German screen, which is what delivery F1 showed ("The only
vault evidence …"). And every red verdict the review card can show carries a key.
"""
import ast
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ORACLE = _ROOT / "backend" / "applire" / "services" / "oracle"
_FLAGGED = {"unbacked", "inflated", "misattributed"}


def _emitted_keys() -> set[str]:
    keys: set[str] = set()
    for f in _ORACLE.rglob("*.py"):
        keys |= set(re.findall(r'detail_key\s*=\s*"([A-Za-z]+)"', f.read_text(encoding="utf-8")))
    return keys


@pytest.mark.parametrize("lang", ["de", "en"])
def test_every_emitted_detail_key_is_translated(lang):
    catalog = json.loads((_ROOT / "frontend" / "messages" / f"{lang}.json").read_text(encoding="utf-8"))
    missing = sorted(k for k in _emitted_keys() if k not in catalog["oracleDetail"])
    assert missing == []


def test_every_red_verdict_literal_carries_a_key():
    """Every ``ClaimVerdict(verdict="unbacked"|"inflated"|"misattributed", …)`` built
    in the Oracle names a ``detail_key`` (a dynamic verdict is out of scope: the
    narrow entailment arm is off on every channel, ADR-068 cl. 7)."""
    offenders = []
    for f in _ORACLE.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ClaimVerdict"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            v = kw.get("verdict")
            if isinstance(v, ast.Constant) and v.value in _FLAGGED and "detail_key" not in kw:
                offenders.append(f"{f.name}:{node.lineno}")
    assert offenders == []
