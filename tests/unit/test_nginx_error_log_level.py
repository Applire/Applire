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
