# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:67 (Hazelnut build 4) — every Oracle verdict detail carries a UI-language key.

Round-2 census (ux-audit-2 DB, 5 CVs + 3 letters): 243 verdicts, 2 keyed (141 grounded with no detail); the
unkeyed ones were `unverifiable` grounding (22), cross-language (9), triage
`not_applicable` (23), grounded figures (46). The rule is enumerated over the
source, so a new verdict without a key fails here rather than in a German UI."""
import ast
import json
import pathlib

from applire.services.oracle import audit

ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOGS = {
    loc: json.loads((ROOT / "frontend" / "messages" / f"{loc}.json").read_text())["oracleDetail"]
    for loc in ("de", "en")
}


def _verdict_calls():
    tree = ast.parse(pathlib.Path(audit.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ClaimVerdict":
            yield node, {k.arg: k.value for k in node.keywords}


def test_every_verdict_with_a_detail_carries_a_key():
    missing = [n.lineno for n, kw in _verdict_calls() if "detail" in kw and "detail_key" not in kw]
    assert missing == [], f"ClaimVerdict without detail_key at audit.py lines {missing}"


def test_every_literal_key_is_in_both_catalogs():
    keys = {
        kw["detail_key"].value
        for _, kw in _verdict_calls()
        if isinstance(kw.get("detail_key"), ast.Constant)
    } | set(audit._TRIAGE_EXEMPT_DETAIL_KEY.values())
    for loc, cat in CATALOGS.items():
        assert keys - set(cat) == set(), (loc, keys - set(cat))


def test_every_triage_class_has_a_key():
    assert set(audit._TRIAGE_EXEMPT_DETAIL_KEY) == set(audit._TRIAGE_EXEMPT_DETAIL)


def test_the_de_values_are_german_and_say_du():
    for key in ("noCloseEvidence", "triageLetterForm", "crossLanguageUnconfirmed", "figuresTrace"):
        assert "Dein" in CATALOGS["de"][key] or "Dich" in CATALOGS["de"][key] or "Du " in CATALOGS["de"][key]
        assert CATALOGS["de"][key] != CATALOGS["en"][key]
