# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US371 (#791) / ADR-095 am. 2026-11-13 (second) — the MCP Registry publish
waits for 1.0.0, and a beta never becomes the listed version.

The release workflow promotes a qualified pre-release by unticking its
"pre-release" box, which fires `released` with `prerelease == false` for a tag
such as `v0.47.0-beta`. That predicate alone moves `:latest` (wanted) and would
also publish the beta to the registry, where a published version can never be
changed or withdrawn. The publish job therefore also requires a tag without a
pre-release suffix.

The job's `if:` is evaluated here, offline, for every event the workflow
receives, by a small evaluator for the subset of the GitHub expression syntax
the condition uses. An unknown token fails the test instead of passing it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"

# --------------------------------------------------------------------------
# A minimal GitHub Actions expression evaluator (the subset the gate uses)
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<op>&&|\|\||==|!=|!|\(|\)|,)"
    r"|(?P<str>'(?:[^']|'')*')"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_.\-]*))"
)


def _tokens(expr: str) -> list[tuple[str, str]]:
    out, pos, expr = [], 0, expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if m is None or m.end() == pos:
            raise ValueError(f"unsupported expression syntax at {expr[pos:]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def evaluate(expr: str, context: dict) -> bool:
    inner = expr.strip()
    if inner.startswith("${{") and inner.endswith("}}"):
        inner = inner[3:-2]
    toks = _tokens(inner)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take(value=None):
        nonlocal i
        tok = peek()
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value!r}, got {tok[1]!r}")
        i += 1
        return tok

    def primary():
        kind, val = peek()
        if val == "!":
            take()
            return not primary()
        if val == "(":
            take("(")
            v = or_()
            take(")")
            return v
        if kind == "str":
            take()
            return val[1:-1].replace("''", "'")
        if kind == "word":
            take()
            if val in ("true", "false"):
                return val == "true"
            if peek()[1] == "(":
                take("(")
                args = [or_()]
                while peek()[1] == ",":
                    take(",")
                    args.append(or_())
                take(")")
                if val == "contains" and len(args) == 2:
                    return str(args[1]).lower() in str(args[0]).lower()
                if val == "startsWith" and len(args) == 2:
                    return str(args[0]).lower().startswith(str(args[1]).lower())
                raise ValueError(f"unsupported function {val}")
            node = context
            for part in val.split("."):
                if not isinstance(node, dict) or part not in node:
                    raise ValueError(f"unknown context path {val}")
                node = node[part]
            return node
        raise ValueError(f"unexpected token {val!r}")

    def comparison():
        left = primary()
        while peek()[1] in ("==", "!="):
            op = take()[1]
            right = primary()
            left = (left == right) if op == "==" else (left != right)
        return left

    def and_():
        v = comparison()
        while peek()[1] == "&&":
            take()
            r = comparison()
            v = bool(v) and bool(r)
        return v

    def or_():
        v = and_()
        while peek()[1] == "||":
            take()
            r = and_()
            v = bool(v) or bool(r)
        return v

    result = or_()
    if i != len(toks):
        raise ValueError(f"trailing tokens: {toks[i:]}")
    return bool(result)


def _release(tag: str, prerelease: bool, action: str) -> dict:
    return {
        "github": {
            "event_name": "release",
            "event": {"action": action, "release": {"tag_name": tag, "prerelease": prerelease}},
        }
    }


_DISPATCH = {"github": {"event_name": "workflow_dispatch", "event": {"release": {"tag_name": "", "prerelease": False}}}}


def _publish_if() -> str:
    jobs = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs["publish-mcp-registry"]["if"]


# --------------------------------------------------------------------------
# The evaluator is itself checked before it is trusted
# --------------------------------------------------------------------------


def test_the_evaluator_reads_the_subset_correctly():
    ctx = {"a": {"b": True, "s": "v1.0.0-beta"}}
    assert evaluate("${{ a.b == true && !contains(a.s, '-') }}", ctx) is False
    assert evaluate("${{ a.b && contains(a.s, 'BETA') }}", ctx) is True  # contains() ignores case
    assert evaluate("${{ !(a.b == false) || false }}", ctx) is True
    with pytest.raises(ValueError):
        evaluate("${{ a.missing }}", ctx)
    with pytest.raises(ValueError):
        evaluate("${{ a.b >= 1 }}", ctx)  # an operator it does not know fails loudly


# --------------------------------------------------------------------------
# The gate's truth table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "context, publishes",
    [
        (_release("v1.0.0", False, "released"), True),  # the first regular 1.x release
        (_release("v1.3.2", False, "released"), True),
        (_release("v2.0.0", False, "released"), True),
        (_release("v0.47.0-beta", False, "released"), False),  # a promoted beta (box unticked)
        (_release("v0.47.0", False, "released"), False),  # a 0.x without suffix: the publish waits for 1.0.0
        (_release("v1.0.0-rc.1", False, "released"), False),
        (_release("v0.47.0-beta", True, "prereleased"), False),
        (_release("v1.0.0", True, "prereleased"), False),  # published as pre-release first
        (_DISPATCH, False),  # a manual dispatch never publishes
    ],
    ids=lambda v: v if isinstance(v, bool) else (v["github"]["event"]["release"]["tag_name"] or "dispatch")
    + ("/pre" if v["github"]["event"]["release"]["prerelease"] else ""),
)
def test_only_a_regular_release_without_a_prerelease_suffix_publishes(context, publishes):
    assert evaluate(_publish_if(), context) is publishes


def test_the_latest_tag_still_moves_for_a_promoted_beta():
    """The new condition narrows the registry publish only, not `:latest`:
    promoting a qualified beta keeps moving the image tags as before."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    latest = re.findall(r"type=raw,value=latest,enable=(\$\{\{.*?\}\})", text)
    assert len(latest) == 3
    for cond in latest:
        assert evaluate(cond, _release("v0.47.0-beta", False, "released")) is True


# --------------------------------------------------------------------------
# The `:{{major}}` image tag (ADR-095 cl. 7): `:1` = the newest 1.x, never `:0`
# --------------------------------------------------------------------------


def _major_tag_conditions() -> list[str]:
    text = _WORKFLOW.read_text(encoding="utf-8")
    return re.findall(r"type=semver,pattern=\{\{major\}\},enable=(\$\{\{.*?\}\})", text)


@pytest.mark.parametrize(
    "context, tagged",
    [
        (_release("v1.0.0", False, "released"), True),
        (_release("v2.3.1", False, "released"), True),
        # metadata-action emits `:0` for a suffix-free 0.x version (its README,
        # "Major version zero"); docs/versioning.md says the betas get no `:0`.
        (_release("v0.47.0", False, "released"), False),
        (_release("v1.0.0", True, "prereleased"), False),
        (_DISPATCH, False),
    ],
    ids=["v1.0.0", "v2.3.1", "v0.47.0", "v1.0.0/pre", "dispatch"],
)
def test_the_major_tag_never_names_zero(context, tagged):
    conditions = _major_tag_conditions()
    assert len(conditions) == 3  # backend, frontend, nginx
    for cond in conditions:
        assert evaluate(cond, context) is tagged


# --------------------------------------------------------------------------
# The stamp step states the publish rule positively and fails closed
# --------------------------------------------------------------------------


def _stamp_module():
    import importlib.util

    root = _WORKFLOW.parents[2]
    spec = importlib.util.spec_from_file_location("server_json_release", root / "scripts" / "server_json_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_publish_job_stamps_with_the_positive_stable_rule():
    jobs = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    runs = [s.get("run", "") for s in jobs["publish-mcp-registry"]["steps"]]
    stamp = [r for r in runs if "server_json_release.py" in r]
    assert stamp == ['python3 scripts/server_json_release.py --require-stable "$TAG" server.json']


@pytest.mark.parametrize(
    "tag, ok",
    [
        ("v1.0.0", True),
        ("v10.2.33", True),
        ("v0.47.0", False),
        ("v00.1.0", False),  # passes the job's `if:`; only the positive rule refuses it
        ("v01.0.0", False),
        ("v1.0.0-rc.1", False),
        ("v0.46.0-beta", False),
    ],
)
def test_require_stable_admits_only_one_point_oh_and_later(tag, ok, tmp_path):
    mod = _stamp_module()
    server = tmp_path / "server.json"
    original = (_WORKFLOW.parents[2] / "server.json").read_text(encoding="utf-8")
    server.write_text(original, encoding="utf-8")
    code = mod.main(["server_json_release.py", "--require-stable", tag, str(server)])
    assert (code == 0) is ok
    if not ok:
        assert server.read_text(encoding="utf-8") == original  # nothing stamped
