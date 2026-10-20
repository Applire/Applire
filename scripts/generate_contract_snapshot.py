#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The 1.x stability contract snapshot (ADR-095, US372).

    python3 scripts/generate_contract_snapshot.py            # write additions
    python3 scripts/generate_contract_snapshot.py --check    # exit 1 if stale or broken
    python3 scripts/generate_contract_snapshot.py --accept-breaking   # the 2.0 path

`tests/contract/contract.json` is the committed record of what Applire promises
across 1.x (docs/versioning.md):

* ``mcp_tools`` — every MCP tool's input schema, flattened to parameter paths
  (``param``, ``param[]`` for array items, ``param.field`` for object fields),
  each with the JSON types it accepts, whether it is required at its level, and
  its ``enum`` when it has one;
* ``settings`` — every settings-registry variable whose tier is not ``internal``
  (founder ruling B2-3: internal switches carry no promise);
* ``migrations`` — every Alembic revision id, in chain order.

`tests/unit/test_contract_snapshot.py` compares the live surface with the file.
A **breaking** difference (something removed or renamed, optional -> required,
a new required parameter, accepted types or enum values narrowed, the migration
chain rewritten) fails, and this script refuses to write it without
``--accept-breaking`` — whose use is the 2.0 decision (ADR-095 cl. 2). An
**addition** is written without a flag, and the test is red until it is: a
snapshot that lags the surface would leave the new names unprotected.

What the snapshot does NOT see (ADR-095 cl. 6): tool result shapes, tool
descriptions, the values a tool body accepts inside a free-form string, and a
variable whose meaning changes under an unchanged name.

Like `generate_env_example.py`, importing this must work in a bare CI shell: it
never touches a database, it only lists the registered tools.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

TARGET = _REPO_ROOT / "tests" / "contract" / "contract.json"
COMMAND = "python3 scripts/generate_contract_snapshot.py"
_ANY = "any"
_JSON_TYPE = {str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}

# --------------------------------------------------------------------------
# Live surface
# --------------------------------------------------------------------------


def _resolve(node: dict, defs: dict) -> dict:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 32:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = defs.get(name, {})
        seen += 1
    return node if isinstance(node, dict) else {}


def _branches(node: dict, defs: dict) -> list[dict]:
    node = _resolve(node, defs)
    alts = node.get("anyOf") or node.get("oneOf")
    if alts:
        out: list[dict] = []
        for alt in alts:
            out.extend(_branches(alt, defs))
        return out
    return [node]


def _types_of(node: dict, defs: dict) -> set[str]:
    types: set[str] = set()
    for branch in _branches(node, defs):
        t = branch.get("type")
        if t is None and "enum" in branch:
            types.update(_JSON_TYPE.get(type(v), _ANY) for v in branch["enum"])
        elif t is None:
            # No constraint: accepts anything (an `items: {}` or a bare `{}`).
            types.add("const" if "const" in branch else _ANY)
        elif isinstance(t, list):
            types.update(t)
        else:
            types.add(t)
    return types


def _enum_of(node: dict, defs: dict) -> list | None:
    values: list = []
    constrained = True
    for branch in _branches(node, defs):
        if "enum" in branch:
            values.extend(branch["enum"])
        elif "const" in branch:
            values.append(branch["const"])
        elif branch.get("type") == "null":
            values.append(None)
        else:
            constrained = False
    if not constrained or not values:
        return None
    return sorted({json.dumps(v, sort_keys=True) for v in values})


def flatten_schema(schema: dict) -> dict[str, dict]:
    """``inputSchema`` -> ``{path: {"types", "required", ["enum"]}}``."""
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    out: dict[str, dict] = {}

    def walk(node: dict, prefix: str) -> None:
        for branch in _branches(node, defs):
            props = branch.get("properties") or {}
            required = set(branch.get("required") or [])
            for name, sub in props.items():
                path = f"{prefix}.{name}" if prefix else name
                _add(path, sub, name in required)
                walk(sub, path)
            items = branch.get("items")
            if isinstance(items, dict) and prefix:
                path = f"{prefix}[]"
                _add(path, items, False)
                walk(items, path)

    def _add(path: str, sub: dict, required: bool) -> None:
        entry = out.setdefault(path, {"types": set(), "required": False})
        entry["types"] |= _types_of(sub, defs)
        entry["required"] = entry["required"] or required
        enum = _enum_of(sub, defs)
        if enum is not None:
            entry["enum"] = sorted(set(entry.get("enum", [])) | set(enum))

    walk(schema, "")
    return {
        path: {k: (sorted(v) if k == "types" else v) for k, v in sorted(e.items())}
        for path, e in sorted(out.items())
    }


def live_tools() -> dict[str, dict]:
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return {t.name: flatten_schema(t.inputSchema or {}) for t in sorted(tools, key=lambda t: t.name)}


def live_settings() -> list[str]:
    from applire.settings_registry import promised_settings

    return sorted(e.env_var for e in promised_settings())


def live_migrations() -> list[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(_REPO_ROOT / "backend" / "alembic"))
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def live_surface() -> dict[str, Any]:
    return {
        "contract": "applire-1.x",
        "regenerate": COMMAND,
        "mcp_tools": live_tools(),
        "settings": live_settings(),
        "migrations": live_migrations(),
    }


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _covers(new: set[str], old: set[str]) -> bool:
    """Does the new type set accept everything the old one did?"""
    if _ANY in new:
        return True
    widened = set(new)
    if "number" in widened:
        widened.add("integer")
    return set(old) <= widened


def _parent(path: str) -> str:
    if path.endswith("[]"):
        return path[:-2]
    return path.rsplit(".", 1)[0] if "." in path else ""


def compare(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """``(breaks, additions)`` from the committed snapshot to the live surface."""
    breaks: list[str] = []
    additions: list[str] = []
    old_tools, new_tools = old.get("mcp_tools", {}), new.get("mcp_tools", {})

    for tool in sorted(old_tools):
        if tool not in new_tools:
            breaks.append(f"MCP tool removed or renamed: {tool}")
            continue
        o, n = old_tools[tool], new_tools[tool]
        for path in sorted(o):
            if path not in n:
                breaks.append(f"MCP parameter removed or renamed: {tool}.{path}")
                continue
            op, np_ = o[path], n[path]
            if np_.get("required") and not op.get("required"):
                breaks.append(f"MCP parameter became required: {tool}.{path}")
            if not _covers(set(np_.get("types", [])), set(op.get("types", []))):
                breaks.append(
                    f"MCP parameter types narrowed: {tool}.{path} "
                    f"{op.get('types')} -> {np_.get('types')}"
                )
            if "enum" in np_:
                if "enum" not in op:
                    breaks.append(f"MCP parameter gained an enum (narrowed): {tool}.{path}")
                elif not set(op["enum"]) <= set(np_["enum"]):
                    gone = sorted(set(op["enum"]) - set(np_["enum"]))
                    breaks.append(f"MCP parameter enum values removed: {tool}.{path} {gone}")
        for path in sorted(set(n) - set(o)):
            parent = _parent(path)
            if n[path].get("required") and (parent == "" or parent in o):
                breaks.append(f"MCP parameter added as required: {tool}.{path}")
            else:
                additions.append(f"MCP parameter added: {tool}.{path}")
    for tool in sorted(set(new_tools) - set(old_tools)):
        additions.append(f"MCP tool added: {tool}")

    old_s, new_s = set(old.get("settings", [])), set(new.get("settings", []))
    breaks += [f"setting removed, renamed or made internal: {s}" for s in sorted(old_s - new_s)]
    additions += [f"setting added: {s}" for s in sorted(new_s - old_s)]

    old_m, new_m = list(old.get("migrations", [])), list(new.get("migrations", []))
    if new_m[: len(old_m)] != old_m:
        missing = [r for r in old_m if r not in new_m]
        breaks.append(
            "migration chain rewritten"
            + (f" (missing: {', '.join(missing)})" if missing else " (order changed)")
        )
    else:
        additions += [f"migration added: {r}" for r in new_m[len(old_m):]]
    return breaks, additions


def load_snapshot(path: Path | None = None) -> dict:
    return json.loads((path or TARGET).read_text(encoding="utf-8"))


def render(surface: dict) -> str:
    return json.dumps(surface, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if the snapshot is stale or the surface broke it")
    parser.add_argument(
        "--accept-breaking",
        action="store_true",
        help="write a snapshot that BREAKS the 1.x contract — the 2.0 decision (ADR-095 cl. 2)",
    )
    args = parser.parse_args(argv)

    live = live_surface()
    old = load_snapshot() if TARGET.exists() else None
    breaks, additions = compare(old, live) if old is not None else ([], ["(new snapshot)"])

    for line in breaks:
        print(f"BREAKING  {line}", file=sys.stderr)
    for line in additions:
        print(f"added     {line}")

    if args.check:
        if breaks or old != live:
            print(f"Contract snapshot is {'BROKEN' if breaks else 'stale'}. Run:  {COMMAND}", file=sys.stderr)
            return 1
        print(f"{TARGET.name} is current ({len(live['mcp_tools'])} tools, {len(live['settings'])} settings, "
              f"{len(live['migrations'])} migrations).")
        return 0

    if breaks and not args.accept_breaking:
        print(
            "Refusing to write a snapshot that breaks the 1.x contract (docs/versioning.md).\n"
            "Keep the old name/parameter/variable (add, don't rename), or — for 2.0 only —\n"
            "re-run with --accept-breaking and say so in the PR body.",
            file=sys.stderr,
        )
        return 2
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(live), encoding="utf-8")
    print(f"Wrote {TARGET} ({len(live['mcp_tools'])} tools, "
          f"{len(live['settings'])} settings, {len(live['migrations'])} migrations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
