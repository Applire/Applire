# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shipped reverse proxy writes critical errors only, at every application
LOG_LEVEL. nginx's upstream error lines print the request line with its query
string, so the published image fixes its error-log level in the config and no
setting of the application (LOG_LEVEL in .env) reaches nginx."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_NGINX = _ROOT / "nginx"


def _server_block(conf: str) -> str:
    return conf.split("server {", 1)[1]


def _directives(text: str) -> list[str]:
    """Non-comment lines, stripped."""
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [line for line in lines if line]


def test_the_shipped_config_writes_critical_errors_only():
    conf = (_NGINX / "self-hosted.conf").read_text()
    error_logs = [d for d in _directives(conf) if d.startswith("error_log")]
    assert error_logs == ["error_log /var/log/nginx/error.log crit;"]
    assert "error_log /var/log/nginx/error.log crit;" in _directives(_server_block(conf))


def test_no_application_setting_reaches_the_proxy():
    compose = (_ROOT / "docker-compose.yml").read_text()
    nginx_block = compose.split("\n  nginx:\n", 1)[1]
    nginx_block = re.split(r"\n  [a-z][a-z0-9_-]*:\n", nginx_block, maxsplit=1)[0]
    assert "LOG_LEVEL" not in "\n".join(_directives(nginx_block))
    for path in _NGINX.iterdir():
        if path.is_file():
            assert "LOG_LEVEL" not in "\n".join(_directives(path.read_text())), path.name


def test_the_image_runs_no_script_that_sets_the_error_log():
    dockerfile = (_NGINX / "Dockerfile").read_text()
    copied = re.findall(r"^COPY .*/docker-entrypoint\.d/(\S+)$", dockerfile, re.MULTILINE)
    assert copied == ["15-applire-trusted-proxy.sh"]
    for name in copied:
        assert "error_log" not in (_NGINX / name).read_text()


def test_the_access_log_writes_the_path_without_its_query_string():
    """A page address's query string (a shared posting, a sign-in code) never
    reaches the proxy's access log: the format names `$uri`, never the request
    line, the arguments or the Referer."""
    for name in ("self-hosted.conf", "dev.conf"):
        directives = "\n".join(_directives((_NGINX / name).read_text()))
        assert "access_log /var/log/nginx/access.log applire_redacted;" in directives, name
        fmt = directives.split("log_format applire_redacted", 1)[1].split(";", 1)[0]
        assert "$applire_log_uri" in fmt, name
        for variable in ("$request ", '$request"', "$request_uri", "$args", "$query_string",
                         "$arg_", "$http_referer"):
            assert variable not in fmt, (name, variable)


def _log_map(conf: str) -> tuple[dict[str, str], list[tuple[re.Pattern, str]], str]:
    block = conf.split("map $uri $applire_log_uri {", 1)[1].split("\n}", 1)[0]
    exact, regexes, default = {}, [], None
    for line in _directives(block):
        if line.startswith("default"):
            default = line.split(None, 1)[1].rstrip(";").strip().strip('"')
            continue
        source, value = re.fullmatch(r'"([^"]+)"\s+"([^"]*)";', line).groups()
        if source.startswith("~"):
            # PCRE's (?<name>…) is Python's (?P<name>…)
            regexes.append((re.compile(source[1:].replace("(?<", "(?P<")), value))
        else:
            exact[source] = value
    return exact, regexes, default


def _logged(conf: str, uri: str) -> str:
    """What nginx writes for ``uri`` (a map: exact strings first, then the
    regular expressions in order, then the default)."""
    exact, regexes, default = _log_map(conf)
    if uri in exact:
        return exact[uri]
    for pattern, value in regexes:
        m = pattern.search(uri)
        if m:
            return re.sub(r"\$(\w+)", lambda v: m.group(v.group(1)), value)
    return default


def test_the_access_log_writes_the_area_or_page_never_the_rest_of_the_path():
    for name in ("self-hosted.conf", "dev.conf"):
        conf = (_NGINX / name).read_text()
        seen = {
            "/": "/",
            "/api/cv": "/api/cv",
            "/api/cv/6f1c2a9e-0b7d-4c55-9a8e-2f3d4c5b6a71/pdf": "/api/cv/[...]",
            "/api/auth/links/Lime9333": "/api/auth/[...]",
            "/api/nonexistent/Lime9333": "[other]",
            "/api/applications/Kiwi9222�": "/api/applications/[...]",
            "/flow/6f1c2a9e/cover-letter": "/flow/[...]",
            "/Lime9333": "[other]",
            "/login": "/login",
            "/sw.js": "/sw.js",
            "/_next/static/chunks/a.js": "/_next/[...]",
            "/api/cv\nLime9333": "[other]",
            "/loginLime9333": "[other]",
        }
        for uri, expected in seen.items():
            assert _logged(conf, uri) == expected, (name, uri)


def test_the_logged_areas_are_the_backend_s_and_the_pages_are_the_frontend_s():
    import os

    os.environ.setdefault("LLM_PROVIDER", "mistral")
    from applire.main import app

    api = {r.path.split("/")[2] for r in app.routes if r.path.startswith("/api/")}
    roots = {r.path.split("/")[1] for r in app.routes if not r.path.startswith("/api/")}
    app_dir = _ROOT / "frontend" / "app"
    pages = set()
    for entry in app_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("("):
            pages |= {p.name for p in entry.iterdir() if p.is_dir() and not p.name.startswith(("_", "["))}
        elif entry.is_dir() and not entry.name.startswith("_"):
            pages.add(entry.name)
    pages.discard("__tests__")
    for name in ("self-hosted.conf", "dev.conf"):
        conf = (_NGINX / name).read_text()
        for area in api:
            assert _logged(conf, f"/api/{area}") == f"/api/{area}", (name, area)
            assert _logged(conf, f"/api/{area}/x") == f"/api/{area}/[...]", (name, area)
        for page in pages | roots:
            assert _logged(conf, f"/{page}") == f"/{page}", (name, page)
