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
import re
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


def _ref_name(node: dict) -> str | None:
    ref = node.get("$ref") if isinstance(node, dict) else None
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None


def _resolve(node: dict, defs: dict) -> dict:
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        name = _ref_name(node)
        if name in seen:  # a reference cycle: stop here (the walk will not descend)
            return {}
        seen.add(name)
        node = defs.get(name, {})
    return node if isinstance(node, dict) else {}


def _merge_all_of(node: dict, defs: dict) -> dict:
    """`allOf` = every member applies: merge members into one schema."""
    members = node.get("allOf")
    if not members:
        return node
    merged: dict = {k: v for k, v in node.items() if k != "allOf"}
    for member in members:
        m = _merge_all_of(_resolve(member, defs), defs)
        merged.setdefault("properties", {})
        merged["properties"] = {**m.get("properties", {}), **merged["properties"]}
        merged["required"] = sorted(set(merged.get("required", [])) | set(m.get("required", [])))
        for key, value in m.items():
            if key not in ("properties", "required"):
                merged.setdefault(key, value)
    if not merged.get("properties"):
        merged.pop("properties", None)
    return merged


def _branches(node: dict, defs: dict) -> list[dict]:
    node = _merge_all_of(_resolve(node, defs), defs)
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
        elif t is None and "const" in branch:
            types.add(_JSON_TYPE.get(type(branch["const"]), _ANY))
        elif t is None:
            # No constraint: accepts anything (an `items: {}` or a bare `{}`).
            types.add(_ANY)
        elif isinstance(t, list):
            types.update(t)
        else:
            types.add(t)
    return types


def _enum_of(node: dict, defs: dict) -> tuple[list | None, list | None]:
    """``(enum, enum_partial)``: the value set when EVERY branch is a value set;
    otherwise the value set of the branches that are (a union like
    ``Literal["a","b"] | int``), so narrowing it is still visible."""
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
    if not values or all(v is None for v in values):
        return None, None
    encoded = sorted({json.dumps(v, sort_keys=True) for v in values})
    return (encoded, None) if constrained else (None, encoded)


#: Value constraints a client can violate; recorded when the path has ONE
#: non-null branch (a union's per-branch bounds are a named limit, ADR-095 cl. 6).
_MAX_KEYS = ("maxLength", "maximum", "exclusiveMaximum", "maxItems", "maxProperties")
_MIN_KEYS = ("minLength", "minimum", "exclusiveMinimum", "minItems", "minProperties")
_EXACT_KEYS = ("pattern", "multipleOf", "format")


def _constraints_of(node: dict, defs: dict) -> dict:
    real = [b for b in _branches(node, defs) if b.get("type") != "null"]
    if len(real) != 1:
        return {}
    b = real[0]
    out = {k: b[k] for k in (*_MAX_KEYS, *_MIN_KEYS, *_EXACT_KEYS) if k in b}
    if b.get("additionalProperties") is False:
        out["closed"] = True
    return out


def _is_open_object(node: dict, defs: dict) -> bool:
    """An object branch that names no fields and does not refuse unknown ones:
    the schema says "any object". Recorded as ``open`` so that defining or
    closing it later is visible in the diff."""
    for branch in _branches(node, defs):
        if branch.get("type") == "object" and not branch.get("properties") \
                and branch.get("additionalProperties", True) is not False:
            return True
    return False


def flatten_schema(schema: dict) -> dict[str, dict]:
    """``inputSchema`` -> ``{path: {"types", "required", ["enum"|"enum_partial"], ["constraints"]}}``."""
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    out: dict[str, dict] = {}

    def walk(node: dict, prefix: str, refs: frozenset[str]) -> None:
        name = _ref_name(node)
        if name is not None:
            if name in refs:
                return  # recursive model: the first level is recorded, the cycle is not followed
            refs = refs | {name}
        for branch in _branches(node, defs):
            props = branch.get("properties") or {}
            required = set(branch.get("required") or [])
            for key, sub in props.items():
                path = f"{prefix}.{key}" if prefix else key
                _add(path, sub, key in required)
                walk(sub, path, refs)
            if not prefix:
                continue
            tuple_items = branch.get("prefixItems")
            items = branch.get("items")
            if isinstance(items, list):
                tuple_items = items
            if isinstance(tuple_items, list):
                for i, sub in enumerate(tuple_items):
                    _add(f"{prefix}[{i}]", sub, False)
                    walk(sub, f"{prefix}[{i}]", refs)
            if isinstance(items, dict):
                _add(f"{prefix}[]", items, False)
                walk(items, f"{prefix}[]", refs)

    def _add(path: str, sub: dict, required: bool) -> None:
        entry = out.setdefault(path, {"types": set(), "required": False})
        entry["types"] |= _types_of(sub, defs)
        entry["required"] = entry["required"] or required
        enum, partial = _enum_of(sub, defs)
        if enum is not None:
            entry["enum"] = sorted(set(entry.get("enum", [])) | set(enum))
        if partial is not None:
            entry["enum_partial"] = sorted(set(entry.get("enum_partial", [])) | set(partial))
        constraints = _constraints_of(sub, defs)
        if constraints:
            entry["constraints"] = constraints
        if _is_open_object(sub, defs):
            entry["open"] = True

    walk(schema, "", frozenset())
    return {
        path: {k: (sorted(v) if k == "types" else v) for k, v in sorted(e.items())}
        for path, e in sorted(out.items())
    }


# --------------------------------------------------------------------------
# Server-defined shapes behind open parameters (F-9, adversarial pass 2026-11-23)
# --------------------------------------------------------------------------
#
# Some tools take a parameter the signature types as an open object (`dict`,
# `list[dict]`) and validate it in the body against a pydantic model. The
# inputSchema then says "any object", while the server refuses anything that
# does not match the model: renaming `ClaimItem.statement` broke every agent
# and left the contract green. Each such parameter is listed here with the
# model the body validates it against; its fields are recorded under the
# parameter's path. A parameter whose shape another parameter selects
# (`render_document.document_kind`, `update_profile.section`) has one variant
# per value, recorded under ``<param>{<value>}``.
#
# Entry: (model "module:Class", field of that model the value is validated as,
# or None for the model itself, "module:function" whose source performs the
# validation — the seam test reads it).


def server_defined_shapes() -> dict[str, dict[str, dict[str, tuple[str, str | None, str]]]]:
    from applire.schemas.profile import VAULT_SECTIONS

    profile = "applire.schemas.profile:MasterProfileData"
    profile_seam = "applire.services.profile.reconcile.apply:_apply_replace_section"
    return {
        "submit_claims": {
            "claims": {"": ("applire.schemas.claims:ClaimsSubmission", "claims", "applire.mcp.server:submit_claims")},
        },
        "update_application": {
            "add_fact_pin": {
                "": ("applire.schemas.application:AddFactPinRequest", None, "applire.mcp.server:update_application"),
            },
        },
        "render_document": {
            "content": {
                "cv": ("applire.schemas.cv:TailoredCVData", None, "applire.services.cv:render_agent_cv"),
                "cover_letter": (
                    "applire.schemas.cover_letter:LetterData", None, "applire.services.cover_letter:render_agent_letter",
                ),
            },
        },
        "update_profile": {
            "data": {section: (profile, section, profile_seam) for section in sorted(VAULT_SECTIONS)},
        },
    }


def merge_patch_variants() -> set[tuple[str, str, str]]:
    """Variants merged into what is stored (RFC 7386), so no field is required
    from the caller: `update_profile` on an object section."""
    from applire.schemas.profile import OBJECT_SECTIONS

    return {("update_profile", "data", section) for section in OBJECT_SECTIONS}


#: Parameters that are free-form on purpose ("tool.path" -> why). Empty today:
#: every open parameter of the live surface has a server-defined shape.
OPEN_PARAMS: dict[str, str] = {}


def _import(ref: str):
    import importlib

    module, name = ref.split(":")
    return getattr(importlib.import_module(module), name)


def _covered(path: str, bases) -> bool:
    return any(path == b or path.startswith((b + ".", b + "[", b + "{")) for b in bases)


def _graft(paths: dict, tool: str, base: str, variant: str, ref: str, field: str | None, merge_patch: bool) -> None:
    schema = json.loads(json.dumps(_import(ref).model_json_schema()))
    defs = {**schema.pop("$defs", {}), **schema.pop("definitions", {})}
    node = schema["properties"][field] if field else schema
    flat = flatten_schema({"type": "object", "properties": {"__v": node}, "$defs": defs})
    target = f"{base}{{{variant}}}" if variant else base
    for path, entry in flat.items():
        new_path = target + path[len("__v"):]
        if merge_patch:
            entry = {**entry, "required": False}
        if path == "__v":
            if variant:
                paths[new_path] = {**entry, "required": False}
            elif "constraints" in entry:  # the signature keeps its own types and requiredness
                paths[base]["constraints"] = entry["constraints"]
            continue
        paths[new_path] = entry


def _record_shapes(tool: str, paths: dict) -> dict:
    shapes = server_defined_shapes().get(tool, {})
    merge = merge_patch_variants()
    for base, variants in shapes.items():
        if base not in paths:
            raise ValueError(f"server_defined_shapes names {tool}.{base}, which the live surface does not have")
        for variant, (ref, field, _seam) in sorted(variants.items()):
            _graft(paths, tool, base, variant, ref, field, (tool, base, variant) in merge)
        paths[base].pop("open", None)
        paths[base]["shape"] = "server"
    for path, entry in paths.items():
        if f"{tool}.{path}" in OPEN_PARAMS:
            entry["shape"] = "open"  # defining fields under it later is a narrowing, never "recorded"
        elif entry.get("open") and not _covered(path, shapes):
            entry["shape"] = "unclassified"
    return {k: dict(sorted(paths[k].items())) for k in sorted(paths)}


def unclassified(surface: dict) -> list[str]:
    """Open parameters nobody classified (server-defined or free-form on purpose)."""
    return sorted(
        f"{tool}.{path}"
        for tool, paths in surface["mcp_tools"].items()
        for path, entry in paths.items()
        if entry.get("shape") == "unclassified"
    )


def live_tools() -> dict[str, dict]:
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    return {
        t.name: _record_shapes(t.name, flatten_schema(t.inputSchema or {}))
        for t in sorted(tools, key=lambda t: t.name)
    }


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
    if path.endswith("}") and "{" in path:
        return path[: path.rindex("{")]
    return path.rsplit(".", 1)[0] if "." in path else ""


def _constraint_breaks(where: str, old: dict, new: dict) -> list[str]:
    """A bound that appears or tightens rejects calls that used to pass."""
    out: list[str] = []
    for k in _MAX_KEYS:
        if k in new and (k not in old or new[k] < old[k]):
            out.append(f"MCP parameter constraint tightened: {where} {k} {old.get(k)} -> {new[k]}")
    for k in _MIN_KEYS:
        if k in new and (k not in old or new[k] > old[k]):
            out.append(f"MCP parameter constraint tightened: {where} {k} {old.get(k)} -> {new[k]}")
    for k in _EXACT_KEYS:
        if k in new and new.get(k) != old.get(k):
            out.append(f"MCP parameter constraint changed: {where} {k} {old.get(k)!r} -> {new[k]!r}")
    if new.get("closed") and not old.get("closed"):
        out.append(f"MCP parameter now refuses unknown fields: {where}")
    return out


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
        # F-9: a parameter whose server-defined shape is recorded for the first
        # time (the old snapshot predates nested recording: no `shape` key).
        # What the server always enforced is written down, not newly imposed:
        # fields, bounds and closedness appearing under it are additions. Every
        # path the old snapshot did record keeps its full protection.
        enriched = {p for p, e in n.items() if e.get("shape") == "server" and p in o and "shape" not in o[p]}
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
            first_record = _covered(path, enriched)
            if "enum" in np_:
                if "enum" not in op:
                    if first_record:
                        additions.append(f"server-defined shape recorded: {tool}.{path} enum")
                    else:
                        breaks.append(f"MCP parameter gained an enum (narrowed): {tool}.{path}")
                elif not set(op["enum"]) <= set(np_["enum"]):
                    gone = sorted(set(op["enum"]) - set(np_["enum"]))
                    breaks.append(f"MCP parameter enum values removed: {tool}.{path} {gone}")
            if "enum_partial" in op:
                kept = set(np_.get("enum_partial", [])) | set(np_.get("enum", []))
                if ("enum_partial" in np_ or "enum" in np_) and not set(op["enum_partial"]) <= kept:
                    gone = sorted(set(op["enum_partial"]) - kept)
                    breaks.append(f"MCP parameter enum values removed: {tool}.{path} {gone}")
            tightened = _constraint_breaks(f"{tool}.{path}", op.get("constraints", {}), np_.get("constraints", {}))
            if first_record:
                additions += [f"server-defined shape recorded: {t}" for t in tightened]
            else:
                breaks += tightened
        for path in sorted(set(n) - set(o)):
            parent = _parent(path)
            if _covered(path, enriched):
                additions.append(f"server-defined shape recorded: {tool}.{path}")
            elif n[path].get("required") and (parent == "" or parent in o):
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


# --------------------------------------------------------------------------
# The release anchor (F-10, adversarial pass 2026-11-23; ADR-095 am. 2026-11-24)
# --------------------------------------------------------------------------
#
# `--accept-breaking` rewrites contract.json, and the two tests above then
# compare the live surface with the rewritten file: green. From 1.0.0 on that
# must not be enough. The release commit of every regular release >= 1.0.0
# writes `tests/contract/released/<version>.json` (`--release`), and the live
# surface is compared with the NEWEST released snapshot of the current
# version's major. Only a new major (whose first release has not shipped yet)
# lifts it. Before 1.0.0 breaks stay allowed (ADR-095 am. 2026-11-13).

RELEASED_DIR = TARGET.parent / "released"
PYPROJECT = _REPO_ROOT / "backend" / "pyproject.toml"
_VERSION = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:[-.]?(?P<pre>[0-9A-Za-z.-]+))?$")


def current_version() -> str:
    import tomllib

    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def parse_version(version: str) -> tuple[int, int, int, str | None]:
    match = _VERSION.match(version)
    if match is None:
        raise ValueError(f"not a release version: {version!r}")
    return int(match["major"]), int(match["minor"]), int(match["patch"]), match["pre"]


def _order(version: str) -> tuple:
    major, minor, patch, pre = parse_version(version)
    return (major, minor, patch, 0 if pre else 1)  # a pre-release sorts before its release


def released_versions(directory: Path | None = None) -> list[str]:
    directory = directory or RELEASED_DIR
    if not directory.is_dir():
        return []
    return sorted((f.stem for f in directory.glob("*.json")), key=_order)


def release_anchor(live: dict, version: str, directory: Path | None = None) -> dict:
    """``{"state", "anchor", "breaks", "message"}`` for the live surface at ``version``.

    states: ``pre-1.0`` (breaks allowed) · ``new-major`` (no release of this
    major yet: the run-up to N.0.0, breaks allowed) · ``missing-own-release``
    (a regular release >= 1.0.0 without its released snapshot: the release
    commit must write it) · ``compared`` (``breaks`` against ``anchor``).
    """
    directory = directory or RELEASED_DIR
    major, _minor, _patch, pre = parse_version(version)
    if major == 0:
        return {"state": "pre-1.0", "anchor": None, "breaks": [], "message": (
            f"{version} is before 1.0.0: a break is still allowed (ADR-095 am. 2026-11-13). It needs "
            "--accept-breaking and upgrade notes. From 1.0.0 on, this check compares with the newest "
            "released snapshot of the same major and --accept-breaking cannot silence it.")}
    if pre is None and not (directory / f"{version}.json").exists():
        return {"state": "missing-own-release", "anchor": None, "breaks": [], "message": (
            f"{version} is a regular release but tests/contract/released/{version}.json does not exist. "
            f"The release commit writes it:  {COMMAND} --release")}
    same_major = [v for v in released_versions(directory)
                  if parse_version(v)[0] == major and _order(v) <= _order(version)]
    if not same_major:
        return {"state": "new-major", "anchor": None, "breaks": [], "message": (
            f"No {major}.x release has shipped yet: the run-up to {major}.0.0 may break the "
            f"{major - 1}.x surface (it ships as a new major, with upgrade notes).")}
    anchor = same_major[-1]
    breaks, _ = compare(load_snapshot(directory / f"{anchor}.json"), live)
    return {"state": "compared", "anchor": anchor, "breaks": breaks, "message": (
        f"compared with released {anchor}: " + ("no break" if not breaks else f"{len(breaks)} break(s)"))}


def load_snapshot(path: Path | None = None) -> dict:
    return json.loads((path or TARGET).read_text(encoding="utf-8"))


def render(surface: dict) -> str:
    return json.dumps(surface, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _write_release(live: dict) -> int:
    version = current_version()
    major, _minor, _patch, pre = parse_version(version)
    if major == 0 or pre is not None:
        print(f"Refusing: {version} is not a regular release >= 1.0.0; only those are anchors.", file=sys.stderr)
        return 2
    if loose := unclassified(live):
        print("Refusing: unclassified open parameters: " + ", ".join(loose), file=sys.stderr)
        return 2
    target = RELEASED_DIR / f"{version}.json"
    if target.exists():
        if load_snapshot(target) == live:
            print(f"{target.name} already records this surface.")
            return 0
        print(f"Refusing: {target} exists and differs. A released snapshot is written once.", file=sys.stderr)
        return 2
    previous = release_anchor(live, f"{major}.{_minor}.{_patch}-pre", RELEASED_DIR)
    if previous["breaks"]:
        for line in previous["breaks"]:
            print(f"BREAKING (against released {previous['anchor']})  {line}", file=sys.stderr)
        return 2
    if load_snapshot() != live:
        print(f"Refusing: contract.json is not current. Run:  {COMMAND}", file=sys.stderr)
        return 2
    RELEASED_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(render(live), encoding="utf-8")
    print(f"Wrote {target} — the anchor every later {major}.x is compared with.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if the snapshot is stale or the surface broke it")
    parser.add_argument(
        "--release",
        action="store_true",
        help="the release commit of a regular release >= 1.0.0: write tests/contract/released/<version>.json",
    )
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

    if args.release:
        return _write_release(live)

    anchor = release_anchor(live, current_version())
    if anchor["breaks"] or anchor["state"] == "missing-own-release":
        for line in anchor["breaks"]:
            print(f"BREAKING (against released {anchor['anchor']})  {line}", file=sys.stderr)
        print(anchor["message"], file=sys.stderr)
        if not args.check:
            print("--accept-breaking does not lift this: only a new major does (ADR-095).", file=sys.stderr)
        return 1

    if args.check:
        if breaks or old != live:
            print(f"Contract snapshot is {'BROKEN' if breaks else 'stale'}. Run:  {COMMAND}", file=sys.stderr)
            return 1
        print(f"{TARGET.name} is current ({len(live['mcp_tools'])} tools, {len(live['settings'])} settings, "
              f"{len(live['migrations'])} migrations).")
        return 0

    loose = unclassified(live)
    if loose:
        print(
            "Refusing to write: these parameters take any object, and nobody said whether the server\n"
            "defines their shape (add the model to server_defined_shapes()) or they are free-form on\n"
            "purpose (add them to OPEN_PARAMS with the reason):\n  " + "\n  ".join(loose),
            file=sys.stderr,
        )
        return 2
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
