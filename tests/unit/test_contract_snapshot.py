# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US372 / ADR-095 — the 1.x stability contract is checked, not promised.

`tests/contract/contract.json` records every MCP tool's input schema (flattened
to parameter paths), every promised settings-registry variable (tier !=
``internal``, founder ruling B2-3) and every Alembic revision. Two assertions
against the live surface:

* **no break** — nothing removed or renamed, nothing optional made required,
  no new required parameter, no accepted type or enum value narrowed, the
  migration chain not rewritten;
* **current** — the file equals the live surface, so an addition is red until
  ``python3 scripts/generate_contract_snapshot.py`` records it (a lagging
  snapshot leaves the new names unprotected: System-FMEA ``SF-CONTRACT.3``).

The comparator's own rules are pinned below on synthetic schemas AND on the real
surface mutated in memory, because a contract check that is green while a break
ships is worse than none (``SF-CONTRACT.2``).
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_contract_snapshot", _REPO / "scripts" / "generate_contract_snapshot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


@pytest.fixture(scope="module")
def live():
    return gen.live_surface()


@pytest.fixture(scope="module")
def snapshot():
    return gen.load_snapshot()


# --------------------------------------------------------------------------
# The two contract assertions
# --------------------------------------------------------------------------


def test_the_live_surface_does_not_break_the_committed_contract(snapshot, live):
    breaks, _ = gen.compare(snapshot, live)
    assert not breaks, (
        "This change breaks the 1.x stability contract (docs/versioning.md):\n  "
        + "\n  ".join(breaks)
        + "\nKeep the old name/parameter/variable and ADD the new one. A real break "
        "ships only in 2.0 (generator flag --accept-breaking, named in the PR body)."
    )


def test_the_committed_snapshot_is_current(snapshot, live):
    _, additions = gen.compare(snapshot, live)
    assert snapshot == live, (
        "The contract snapshot lags the live surface"
        + (":\n  " + "\n  ".join(additions) if additions else " (ordering/format)")
        + f"\nRun:  {gen.COMMAND}"
    )


def test_the_snapshot_covers_every_registered_tool_and_promised_setting(live):
    """Floor against an extractor that silently sees nothing."""
    from applire.settings_registry import INTERNAL_KEYS, all_settings

    assert len(live["mcp_tools"]) >= 30
    assert set(live["settings"]) == {e.env_var for e in all_settings()} - set(INTERNAL_KEYS)
    assert live["migrations"][0] == "0001" and len(live["migrations"]) >= 82
    # every tool that takes parameters has paths recorded
    assert sum(len(p) for p in live["mcp_tools"].values()) >= 60


def test_expected_tools_pin_is_folded_into_the_snapshot(snapshot):
    """ADR-095 cl. 5 — `_EXPECTED_TOOLS` is derived, not a second hand-kept list."""
    spec = importlib.util.spec_from_file_location("test_mcp_server", _REPO / "tests" / "test_mcp_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._EXPECTED_TOOLS == set(snapshot["mcp_tools"])


# --------------------------------------------------------------------------
# Mutations of the REAL surface (in memory) — each must be caught
# --------------------------------------------------------------------------


def _mutated(live):
    return copy.deepcopy(live)


def test_renaming_a_real_parameter_is_breaking(snapshot, live):
    new = _mutated(live)
    params = new["mcp_tools"]["send_message"]
    params["text"] = params.pop("message")
    breaks, _ = gen.compare(snapshot, new)
    assert "MCP parameter removed or renamed: send_message.message" in breaks


def test_making_a_real_optional_parameter_required_is_breaking(snapshot, live):
    new = _mutated(live)
    new["mcp_tools"]["generate_cv"]["target_pages"]["required"] = True
    breaks, _ = gen.compare(snapshot, new)
    assert "MCP parameter became required: generate_cv.target_pages" in breaks


def test_removing_a_real_tool_is_breaking(snapshot, live):
    new = _mutated(live)
    del new["mcp_tools"]["audit_document"]
    assert "MCP tool removed or renamed: audit_document" in gen.compare(snapshot, new)[0]


def test_removing_a_promised_variable_is_breaking(snapshot, live):
    new = _mutated(live)
    new["settings"].remove("RETENTION_ENABLED")
    breaks, _ = gen.compare(snapshot, new)
    assert "setting removed, renamed or made internal: RETENTION_ENABLED" in breaks


def test_changing_an_internal_variable_is_not_a_break(snapshot, live):
    """B2-3: an internal switch carries no promise — renaming or deleting it
    leaves the contract green (it was never in the snapshot)."""
    assert "CV_PIPELINE" not in snapshot["settings"]
    assert "PROMPT_CONTEXT" not in snapshot["settings"]
    assert gen.compare(snapshot, live)[0] == []


def test_removing_a_shipped_migration_is_breaking(snapshot, live):
    new = _mutated(live)
    new["migrations"].remove("0076")
    breaks, _ = gen.compare(snapshot, new)
    assert any(b.startswith("migration chain rewritten") and "0076" in b for b in breaks)


def test_additions_are_not_breaks_but_make_the_snapshot_stale(snapshot, live):
    new = _mutated(live)
    new["mcp_tools"]["a_new_tool"] = {"x": {"types": ["string"], "required": True}}
    new["mcp_tools"]["send_message"]["tone"] = {"types": ["null", "string"], "required": False}
    new["settings"].append("A_NEW_SETTING")
    new["migrations"].append("9999")
    breaks, additions = gen.compare(snapshot, new)
    assert breaks == []
    assert {
        "MCP tool added: a_new_tool",
        "MCP parameter added: send_message.tone",
        "setting added: A_NEW_SETTING",
        "migration added: 9999",
    } <= set(additions)
    assert new != snapshot


# --------------------------------------------------------------------------
# Comparator rules on synthetic schemas
# --------------------------------------------------------------------------


def _surface(schema: dict) -> dict:
    return {"mcp_tools": {"t": gen.flatten_schema(schema)}, "settings": [], "migrations": []}


def _schema(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


_STR = {"type": "string"}
_OPT_STR = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}


def test_a_new_required_parameter_is_breaking():
    old = _surface(_schema({"a": _STR}, ["a"]))
    new = _surface(_schema({"a": _STR, "b": _STR}, ["a", "b"]))
    assert gen.compare(old, new)[0] == ["MCP parameter added as required: t.b"]


def test_a_new_optional_parameter_is_an_addition():
    old = _surface(_schema({"a": _STR}, ["a"]))
    new = _surface(_schema({"a": _STR, "b": _OPT_STR}, ["a"]))
    assert gen.compare(old, new) == ([], ["MCP parameter added: t.b"])


def test_narrowing_a_type_is_breaking_and_widening_is_not():
    opt = _surface(_schema({"a": _OPT_STR}))
    req = _surface(_schema({"a": _STR}))
    assert any("types narrowed" in b for b in gen.compare(opt, req)[0])  # string|null -> string
    assert gen.compare(req, opt)[0] == []  # string -> string|null
    ints = _surface(_schema({"n": {"type": "integer"}}))
    nums = _surface(_schema({"n": {"type": "number"}}))
    assert gen.compare(ints, nums)[0] == []  # integer -> number widens
    assert any("types narrowed" in b for b in gen.compare(nums, ints)[0])


def test_enum_values_may_be_added_never_removed_or_introduced():
    two = _surface(_schema({"k": {"enum": ["cv", "cover_letter"]}}))
    three = _surface(_schema({"k": {"enum": ["cv", "cover_letter", "cover-letter"]}}))
    free = _surface(_schema({"k": _STR}))
    assert gen.compare(two, three)[0] == []
    assert any("enum values removed" in b for b in gen.compare(three, two)[0])
    assert any("gained an enum" in b for b in gen.compare(free, two)[0])
    assert gen.compare(two, free)[0] == []  # dropping the enum widens


def test_nested_fields_are_part_of_the_contract():
    item = {"type": "object", "properties": {"text": _STR, "kind": _OPT_STR}, "required": ["text"]}
    old = _surface(_schema({"claims": {"type": "array", "items": item}}, ["claims"]))
    renamed = copy.deepcopy(item)
    renamed["properties"]["body"] = renamed["properties"].pop("text")
    renamed["required"] = ["body"]
    new = _surface(_schema({"claims": {"type": "array", "items": renamed}}, ["claims"]))
    breaks = gen.compare(old, new)[0]
    assert "MCP parameter removed or renamed: t.claims[].text" in breaks
    assert "MCP parameter added as required: t.claims[].body" in breaks


def test_refs_are_resolved():
    schema = {
        "type": "object",
        "properties": {"c": {"$ref": "#/$defs/C"}},
        "required": ["c"],
        "$defs": {"C": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}},
    }
    flat = gen.flatten_schema(schema)
    assert flat["c"] == {"required": True, "types": ["object"]}
    assert flat["c.x"] == {"required": True, "types": ["integer"]}


def test_reordering_migrations_is_breaking():
    old = {"mcp_tools": {}, "settings": [], "migrations": ["1", "2", "3"]}
    new = {"mcp_tools": {}, "settings": [], "migrations": ["1", "3", "2"]}
    assert gen.compare(old, new)[0] == ["migration chain rewritten (order changed)"]


# --------------------------------------------------------------------------
# The generator refuses to write a break
# --------------------------------------------------------------------------


def test_the_generator_refuses_a_breaking_snapshot_without_the_flag(monkeypatch, tmp_path, snapshot):
    target = tmp_path / "contract.json"
    broken = copy.deepcopy(snapshot)
    broken["settings"].append("A_VARIABLE_THAT_NO_LONGER_EXISTS")
    target.write_text(gen.render(broken), encoding="utf-8")
    monkeypatch.setattr(gen, "TARGET", target)
    before = target.read_text(encoding="utf-8")
    assert gen.main([]) == 2
    assert target.read_text(encoding="utf-8") == before
    assert gen.main(["--check"]) == 1
    assert gen.main(["--accept-breaking"]) == 0
    assert "A_VARIABLE_THAT_NO_LONGER_EXISTS" not in target.read_text(encoding="utf-8")
