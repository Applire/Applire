# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every frontend API call has a backend route (#789 L33 — CI tripwire).

The frontend (``frontend/{app,components,lib}``) reaches the backend through
string/template literals that start with ``/api/`` (optionally behind
``${API_BASE}``). This test extracts every such call site statically with its
HTTP method, normalises ``${...}`` to a path-parameter placeholder, and checks
the (method, path) pair against the FastAPI route table of ``applire.main.app``.
A frontend call to a path or method the backend does not serve (a renamed route,
a PATCH that became a PUT, a typo) goes red here, naming ``file:line``, instead
of surfacing as a 404/405 in a user's browser.

How the method is read (the project's real fetch helpers):

* ``fetch(url, { method: "X" })`` — the ``method`` option of the enclosing call;
  no ``method`` option = GET.
* ``send(path, "X")`` (``components/account/api.ts``, ``components/admin/users/api.ts``).
* ``postJson(path)`` (``lib/auth/api.ts``) = POST.
* ``call(path, { method: "X" })`` (``lib/api/admin.ts``); no option = GET.
* any other use of a ``/api/...`` literal (``fetchDerived(path)``, a URL variable,
  ``window.location.assign``, an ``<a href>``) = GET.

A call whose method is dynamic (``method`` set to a variable) cannot be read
statically and is reported as such: pin it in ``ALLOWED`` with a reason.

Two guards keep the extractor honest: ``RECOGNISED_FLOOR`` (an extractor that
silently matches nothing cannot pass green), and a mutation self-test on
synthetic sources and a synthetic route table (a wrong path, a wrong method and
an undeterminable method are each reported).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

os.environ.setdefault("LLM_PROVIDER", "mistral")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SCAN_DIRS = ("app", "components", "lib")
#: The one-off sweep (Nougat, 2026-09) measured 166 call sites; the extractor
#: below sees more. Fewer than this means the extractor stopped matching.
RECOGNISED_FLOOR = 150

#: (file relative to frontend/, METHOD, normalised path) -> why the pair has no
#: route. Every entry needs a reason; an entry nothing needs any more fails.
ALLOWED: dict[tuple[str, str, str], str] = {}

#: A path with a variable in a LITERAL position (a resource name, an action) cannot
#: be matched as written. Each entry lists, per ``{}`` in order, the values it
#: takes (None = a real path parameter, left as ``{}``); EVERY combination must
#: have a route: (file, normalised path) -> (per-placeholder values, reason).
EXPANDED: dict[tuple[str, str], tuple[tuple[tuple[str, ...] | None, ...], str]] = {
    ("lib/api/document-review.ts", "/api/{}/{}/review/{}"): (
        (
            ("cv", "cover-letter"),
            None,
            ("add-evidence", "take-out", "undo", "edited", "kept", "walked"),
        ),
        "`kind: ReviewDocumentKind` = cv | cover-letter (lib/api/document-review.ts:33); "
        "the action is each string passed to `post(kind, id, action)` in that file",
    ),
}


@dataclass(frozen=True)
class Call:
    file: str
    line: int
    method: str  # an HTTP verb, or "?" when it cannot be read statically
    path: str  # normalised: ``${...}`` -> ``{}``, query string removed
    raw: str


# --------------------------------------------------------------------------- lexer


def _mask(src: str) -> tuple[str, list[tuple[int, int]]]:
    """Blank comments; replace string/template contents by ``x``. Returns the
    masked text (same length as ``src``) and the (start, end) of every string or
    template literal including its quotes."""
    out = list(src)
    toks: list[tuple[int, int]] = []
    n = len(src)

    def blank(a: int, b: int, ch: str | None = None) -> None:
        for k in range(a, b):
            if src[k] != "\n":
                out[k] = " " if ch is None else ch

    def template(i: int) -> int:  # src[i] == "`"; returns index after the closing backtick
        j = i + 1
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
            elif c == "`":
                return j + 1
            elif c == "$" and src[j + 1 : j + 2] == "{":
                j = code(j + 2, closing="}")
            else:
                j += 1
        return n

    def code(i: int, closing: str | None = None) -> int:
        depth = 0
        while i < n:
            c = src[i]
            nxt = src[i + 1 : i + 2]
            if c == "/" and nxt == "/":
                j = src.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
            elif c == "/" and nxt == "*":
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                blank(i, j)
                i = j
            elif c in "\"'":
                j = i + 1
                while j < n and src[j] != c and src[j] != "\n":
                    j += 2 if src[j] == "\\" else 1
                j = min(j + 1, n)
                toks.append((i, j))
                blank(i + 1, j - 1, "x")
                i = j
            elif c == "`":
                j = template(i)
                toks.append((i, j))
                blank(i + 1, j - 1, "x")
                i = j
            elif closing and c == "{":
                depth += 1
                i += 1
            elif closing and c == closing:
                if depth == 0:
                    return i + 1
                depth -= 1
                i += 1
            else:
                i += 1
        return n

    code(0)
    return "".join(out), toks


# --------------------------------------------------------------------------- extractor

# "/api/" alone is a prefix test (``startsWith("/api/")``), not a call.
_API_LITERAL = re.compile(r"^(?:\$\{[^}]*\})?(/api/[^\s].*)$", re.S)
_METHOD_OPT = re.compile(r"\bmethod\s*:\s*([\"'])([A-Za-z]+)\1")
_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _normalise(path: str) -> str:
    """Cut the query string, turn ``${...}`` into ``{}``, drop a trailing
    placeholder glued to a segment (``/audit${qs}`` — a query-string suffix)."""
    # cut at the first "?" outside a ${...}
    depth = 0
    for i, ch in enumerate(path):
        if path[i : i + 2] == "${":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif ch == "?" and not depth:
            path = path[:i]
            break
    path = re.sub(r"(?<=[^/])\$\{[^}]*\}$", "", path)
    return re.sub(r"\$\{[^}]*\}", "{}", path)


def _enclosing_call(masked: str, pos: int) -> tuple[str | None, int | None]:
    """(callee name, index of its "(") of the call whose argument list holds ``pos``."""
    depth = 0
    i = pos - 1
    while i >= 0:
        c = masked[i]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                if c != "(":
                    return None, None
                m = re.search(r"([A-Za-z_$][\w$]*)\s*$", masked[:i])
                return (m.group(1) if m else None), i
            depth -= 1
        elif c == ";" and depth == 0:
            return None, None
        i -= 1
    return None, None


def _call_args(masked: str, open_idx: int) -> tuple[int, int]:
    depth = 0
    for i in range(open_idx, len(masked)):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return open_idx + 1, i
    return open_idx + 1, len(masked)


def extract_calls(src: str, file: str) -> list[Call]:
    masked, toks = _mask(src)
    calls: list[Call] = []
    for start, end in toks:
        inner = src[start + 1 : end - 1]
        m = _API_LITERAL.match(inner)
        if not m:
            continue
        callee, open_idx = _enclosing_call(masked, start)
        method = "GET"
        if callee == "postJson":
            method = "POST"
        elif open_idx is not None:
            a, b = _call_args(masked, open_idx)
            if callee == "send":
                # send(path, "METHOD", body?) — the literal after the path literal
                after = re.match(r"\s*,\s*([\"'])([A-Za-z]+)\1", src[end:b])
                method = after.group(2).upper() if after else "?"
            else:
                # fetch / doFetch / call / fetchDerived …: a `method` option of the call
                mm = _METHOD_OPT.search(src[a:b])
                if mm:
                    method = mm.group(2).upper()
                elif re.search(r"\bmethod\b", masked[a:b]):
                    method = "?"
        calls.append(
            Call(file, src.count("\n", 0, start) + 1, method, _normalise(m.group(1)), inner)
        )
    return calls


def _frontend_sources() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        for p in sorted((FRONTEND / d).rglob("*")):
            if p.suffix not in {".ts", ".tsx"} or p.name.endswith(".d.ts"):
                continue
            rel = p.relative_to(FRONTEND).parts
            if "__tests__" in rel or "test-utils" in rel or ".test." in p.name:
                continue
            files.append(p)
    return files


def all_calls() -> list[Call]:
    out: list[Call] = []
    for p in _frontend_sources():
        out += extract_calls(p.read_text(encoding="utf-8"), p.relative_to(FRONTEND).as_posix())
    return out


# --------------------------------------------------------------------------- matching


def _route_table(app) -> list[tuple[re.Pattern, set[str], str]]:
    table = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        rx = re.sub(r"\{[^}:]+:path\}", ".+", r.path)
        rx = re.sub(r"\{[^}]+\}", "[^/]+", rx)
        table.append((re.compile(f"^{rx}$"), set(methods), r.path))
    return table


def _expand(calls: list[Call], expanded) -> list[Call]:
    out: list[Call] = []
    for c in calls:
        spec = expanded.get((c.file, c.path))
        if not spec:
            out.append(c)
            continue
        paths = [c.path]
        for values in spec[0]:
            if values is None:  # a real path parameter: step over it
                paths = [p.replace("{}", "\0", 1) for p in paths]
            else:
                paths = [p.replace("{}", v, 1) for p in paths for v in values]
        out += [Call(c.file, c.line, c.method, p.replace("\0", "{}"), c.raw) for p in paths]
    return out


def violations(calls: list[Call], table, allowed=ALLOWED, expanded=EXPANDED) -> list[str]:
    bad = []
    for c in _expand(calls, expanded):
        key = (c.file, c.method, c.path)
        if key in allowed:
            continue
        probe = c.path.replace("{}", "X")
        hits = [(ms, p) for rx, ms, p in table if rx.match(probe)]
        where = f"{c.file}:{c.line}"
        if c.method == "?":
            bad.append(f"{where}: method of {c.path} cannot be read statically — pin it in ALLOWED")
        elif not hits:
            bad.append(f"{where}: {c.method} {c.path} — NO ROUTE")
        elif not any(c.method in ms for ms, _ in hits):
            served = sorted({m for ms, _ in hits for m in ms - {"HEAD", "OPTIONS"}})
            bad.append(f"{where}: {c.method} {c.path} — NO METHOD (route serves {', '.join(served)})")
    return bad


@pytest.fixture(scope="module")
def route_table():
    from applire.main import app

    return _route_table(app)


# --------------------------------------------------------------------------- tests


def test_every_frontend_api_call_has_a_backend_route(route_table):
    calls = all_calls()
    assert len(calls) >= RECOGNISED_FLOOR, (
        f"extractor recognised only {len(calls)} frontend API call sites "
        f"(floor {RECOGNISED_FLOOR}) — it stopped matching the call shapes"
    )
    bad = violations(calls, route_table)
    assert not bad, "frontend calls without a backend route:\n  " + "\n  ".join(bad)


def test_allowlist_entries_are_still_needed(route_table):
    """A stale allowlist line hides the next real mismatch on the same pair."""
    calls = {(c.file, c.method, c.path) for c in all_calls()}
    for key, reason in ALLOWED.items():
        assert reason.strip(), f"allowlist entry {key} has no reason"
        assert key in calls, f"allowlist entry {key} matches no call site any more"
        assert violations([c for c in all_calls() if (c.file, c.method, c.path) == key], route_table, {}), (
            f"allowlist entry {key} now has a route — remove it"
        )


def test_expansion_entries_still_match_a_call_site():
    calls = {(c.file, c.path) for c in all_calls()}
    for key, (values, reason) in EXPANDED.items():
        assert reason.strip(), f"expansion {key} has no reason"
        assert key in calls, f"expansion {key} matches no call site any more"
        assert len(values) == key[1].count("{}"), f"expansion {key}: one entry per placeholder"


# ---- the extractor and the matcher are themselves mutation-tested ---------------

_SYNTH = '''
const API_BASE = "";
// fetch(`${API_BASE}/api/commented/out`)
async function a(id: string, q: string) {
  await fetch(`${API_BASE}/api/widgets/${id}/parts?x=${q}`, { method: "PATCH", body: "{}" });
  await fetch(`${API_BASE}/api/widgets/${id}`);
  await send(`/api/widgets/${encodeURIComponent(id)}`, "DELETE");
  await send("/api/widgets", "POST", { a: 1 });
  await postJson("/api/widgets/import", { b: 2 });
  await call(`/api/widgets/audit${qs(q)}`, { method: "PUT", body: { c: 3 } });
  await call("/api/widgets");
  const m = pick();
  await fetch("/api/widgets", { method: m });
  window.location.assign(`${API_BASE}/api/widgets/start?next=${encodeURIComponent("/")}`);
  const note = "see /api in the docs"; /* `/api/in/a/comment` */
}
'''


def _synth_table():
    class R:
        def __init__(self, path, methods):
            self.path, self.methods = path, methods

    return _route_table(
        type(
            "A",
            (),
            {
                "routes": [
                    R("/api/widgets", {"GET", "POST", "HEAD"}),
                    R("/api/widgets/{wid}", {"GET", "DELETE"}),
                    R("/api/widgets/{wid}/parts", {"PATCH"}),
                    R("/api/widgets/import", {"POST"}),
                    R("/api/widgets/audit", {"PUT"}),
                    R("/api/widgets/start", {"GET"}),
                ]
            },
        )
    )


def test_extractor_reads_every_call_shape():
    got = [(c.method, c.path) for c in extract_calls(_SYNTH, "synth.ts")]
    assert got == [
        ("PATCH", "/api/widgets/{}/parts"),
        ("GET", "/api/widgets/{}"),
        ("DELETE", "/api/widgets/{}"),
        ("POST", "/api/widgets"),
        ("POST", "/api/widgets/import"),
        ("PUT", "/api/widgets/audit"),
        ("GET", "/api/widgets"),
        ("?", "/api/widgets"),
        ("GET", "/api/widgets/start"),
    ]


def test_matcher_reports_a_wrong_path_a_wrong_method_and_an_unreadable_method():
    table = _synth_table()
    calls = extract_calls(_SYNTH, "synth.ts")
    # only the dynamic method is reported on the pristine synthetic source
    pristine = violations(calls, table)
    assert len(pristine) == 1 and "cannot be read statically" in pristine[0]

    renamed = extract_calls(_SYNTH.replace('"/api/widgets", "POST"', '"/api/gadgets", "POST"'), "synth.ts")
    assert any("POST /api/gadgets — NO ROUTE" in v for v in violations(renamed, table))

    wrong_verb = extract_calls(_SYNTH.replace('"DELETE");', '"PUT");'), "synth.ts")
    assert any("PUT /api/widgets/{} — NO METHOD (route serves DELETE, GET)" in v for v in violations(wrong_verb, table))

    assert not any("commented" in c.path or "in/a/comment" in c.path for c in calls)
