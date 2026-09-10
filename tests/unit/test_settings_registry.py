# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-087 (settings registry): declared == read, in both directions.

`backend/applire/settings_registry.py` is the declared configuration surface.
This suite proves the three things that module claims to guarantee:

1. every environment variable the code actually reads has a registry entry,
   and vice versa (tests 1-2 below; this is the coverage half — 74 entries
   today, and a new undeclared `os.environ` read makes this suite red);
2. `.env.example` is generated from the registry, not hand-maintained
   (tests 3-5; this is the JF-O-2.2 regression the module's docstring names);
3. `compute_upgrade_notice` / `parse_release` / `format_upgrade_notice_log`
   behave correctly on a small HAND-BUILT registry (test 6), independent of
   whatever the real 74 entries happen to be today;
4. `current_environment` merges `.env` under `os.environ` with the right
   precedence (test 7).

Tests 1-5 exercise the REAL registry (`all_settings()`) and read real files
(`backend/applire/`, `docker-compose.yml`, `.env.example`) relative to the
repo root. Test 6 builds a tiny registry by hand and monkeypatches
`all_settings` so behaviour assertions do not depend on the 74 real entries
ever staying the same shape.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from applire.config import Settings  # noqa: E402
from applire.settings_registry import (  # noqa: E402
    SettingEntry,
    all_settings,
    compute_upgrade_notice,
    current_environment,
    format_upgrade_notice_log,
    parse_release,
)

# ---------------------------------------------------------------------------
# Shared scanning helpers (tests 1 and 2)
# ---------------------------------------------------------------------------

#: Matches the env-var literal in `os.environ.get("X", ...)`, `os.getenv("X")`
#: and `os.environ["X"]` — the three shapes actually used in the tree
#: (constants.py uses the first almost exclusively; config.py's STATIC_DIR
#: reader and mcp/__main__.py's MCP_TRANSPORT reader use the others).
_ENV_READ_RE = re.compile(
    r"os\.environ\.get\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    r"|os\.getenv\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    r"|os\.environ\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]"
)


def _env_literals_read_in_backend() -> set[str]:
    """Every string literal passed to an `os.environ`/`os.getenv` read call
    anywhere under `backend/applire/`."""
    found: set[str] = set()
    for path in (_BACKEND_ROOT / "applire").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _ENV_READ_RE.finditer(text):
            name = next(g for g in match.groups() if g is not None)
            found.add(name)
    return found


def _settings_field_env_vars() -> set[str]:
    """Every `Settings` field name, upper-cased to the env var it reads."""
    return {name.upper() for name in Settings.model_fields}


def _registry_env_vars() -> set[str]:
    return {entry.env_var for entry in all_settings()}


# ---------------------------------------------------------------------------
# 1. declared == read, forward
# ---------------------------------------------------------------------------


def test_every_settings_field_has_a_registry_entry():
    field_vars = _settings_field_env_vars()
    registry_vars = _registry_env_vars()
    missing = field_vars - registry_vars
    assert not missing, (
        f"Settings field(s) with no settings_registry.py entry: {sorted(missing)}. "
        "Add a SettingEntry(source='config', ...) for each in "
        "backend/applire/settings_registry.py."
    )


def test_every_os_environ_literal_read_in_backend_has_a_registry_entry():
    found = _env_literals_read_in_backend()
    registry_vars = _registry_env_vars()
    missing = found - registry_vars
    assert not missing, (
        f"os.environ/os.getenv literal(s) read in backend/applire/ with no "
        f"settings_registry.py entry: {sorted(missing)}. Add a SettingEntry "
        "for each — see backend/applire/settings_registry.py's module docstring."
    )


# ---------------------------------------------------------------------------
# 2. declared == read, reverse
# ---------------------------------------------------------------------------


def test_every_config_or_constants_entry_has_a_reader():
    field_vars = _settings_field_env_vars()
    found_literals = _env_literals_read_in_backend()
    readers = field_vars | found_literals

    declared = {
        entry.env_var for entry in all_settings() if entry.source in ("config", "constants")
    }
    undead = declared - readers
    assert not undead, (
        f"settings_registry.py entr(y/ies) with source 'config'/'constants' but no "
        f"reader (neither a Settings field nor an os.environ/os.getenv literal): "
        f"{sorted(undead)}. Either the entry is stale, or the reader was removed "
        "without removing the declaration."
    )


# ---------------------------------------------------------------------------
# 3. compose entries are really in the compose file
# ---------------------------------------------------------------------------


def test_every_compose_source_entry_appears_in_docker_compose_yml():
    compose_text = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose_entries = [entry for entry in all_settings() if entry.source == "compose"]
    assert compose_entries, "expected at least one source='compose' entry (POSTGRES_*)"

    missing = [
        entry.env_var
        for entry in compose_entries
        if f"${{{entry.env_var}" not in compose_text
    ]
    assert not missing, (
        f"source='compose' registry entr(y/ies) not referenced as ${{...}} in "
        f"docker-compose.yml: {missing}"
    )


def test_there_are_exactly_the_three_known_compose_entries():
    # Pinned by name, not just by count: a fourth compose entry with a typo'd
    # env_var could still pass the substring check above against an unrelated
    # ${...} elsewhere in the file.
    compose_vars = {e.env_var for e in all_settings() if e.source == "compose"}
    assert compose_vars == {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}


# ---------------------------------------------------------------------------
# 4. `.env.example` equals the generator's output
# ---------------------------------------------------------------------------


def _load_generator_module():
    """Import scripts/generate_env_example.py by path (it is not a package)."""
    script_path = _REPO_ROOT / "scripts" / "generate_env_example.py"
    spec = importlib.util.spec_from_file_location("generate_env_example", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_example_file_matches_the_generators_render_output():
    generator = _load_generator_module()
    on_disk = generator.TARGET.read_text(encoding="utf-8")
    rendered = generator.render()
    assert on_disk == rendered, (
        ".env.example does not match scripts/generate_env_example.py's render() "
        "output — this is the JF-O-2.2 drift gate. Fix: "
        "python3 scripts/generate_env_example.py"
    )


def test_env_example_check_mode_reports_up_to_date():
    generator = _load_generator_module()
    exit_code = generator.main(["--check"])
    assert exit_code == 0, (
        ".env.example is out of date with backend/applire/settings_registry.py. "
        "Fix: python3 scripts/generate_env_example.py"
    )


# ---------------------------------------------------------------------------
# 5. a registry entry cannot contradict the code default
# ---------------------------------------------------------------------------

#: Fields with no *literal* Python default at the `Settings` class, so there is
#: nothing for the registry's `default=` to be compared against here:
#:   - DATABASE_URL: `database_url: str` has NO default (PydanticUndefined) —
#:     it is a required field. The registry's "default" documents the value
#:     docker-compose.yml supplies via ${POSTGRES_...} interpolation, which is
#:     an operational fact, not a Python literal this test can introspect.
#:   - STATIC_DIR: has no `Settings` field at all (read via `os.getenv`
#:     directly in `resolve_static_dir()`), so `model_fields` has nothing to
#:     look up.
_SKIP_DEFAULT_CHECK = {"DATABASE_URL", "STATIC_DIR"}


def _rendered_default(value: object) -> str:
    """Render a Python default the way SettingEntry.default is written."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def test_registry_default_matches_the_settings_field_default():
    # This is the test that makes JF-O-2.2 (.env.example said
    # mistral-medium-latest, the code said mistral-small-latest) structurally
    # impossible: a registry entry whose default drifts from Settings' own
    # default fails here, before it ever reaches the generated template.
    mismatches: list[str] = []
    for entry in all_settings():
        if entry.source != "config":
            continue
        if entry.env_var in _SKIP_DEFAULT_CHECK:
            continue
        field = Settings.model_fields.get(entry.name)
        if field is None:
            continue  # covered by the reverse-direction coverage test instead
        code_default = _rendered_default(field.default)
        if entry.default != code_default:
            mismatches.append(
                f"{entry.env_var}: registry says {entry.default!r}, "
                f"Settings.{entry.name} defaults to {code_default!r}"
            )
    assert not mismatches, "\n".join(mismatches)


def test_interview_question_cap_defaults_match_their_imported_constants():
    # interview_max_questions_targeted/guided default to
    # INTERVIEW_HARD_CEILING_TARGETED/GUIDED (imported constants, currently
    # both 30). Named explicitly per the instructions even though the
    # generic test above already covers them structurally (pydantic resolves
    # the class attribute to the constant's literal value at class-body
    # execution time, so Settings.model_fields[...].default IS that literal).
    from applire.constants import (
        INTERVIEW_HARD_CEILING_GUIDED,
        INTERVIEW_HARD_CEILING_TARGETED,
    )

    by_var = {e.env_var: e for e in all_settings()}
    assert by_var["INTERVIEW_MAX_QUESTIONS_TARGETED"].default == str(
        INTERVIEW_HARD_CEILING_TARGETED
    )
    assert by_var["INTERVIEW_MAX_QUESTIONS_GUIDED"].default == str(
        INTERVIEW_HARD_CEILING_GUIDED
    )


# ===========================================================================
# 6. compute_upgrade_notice behaviour — one property per test, hand-built
#    registry so these do not depend on the real 74 entries' current shape.
# ===========================================================================


def _entry(
    env_var: str,
    *,
    introduced_in: str = "0.31.0",
    semantics_changed_in: str | None = None,
    default: str = "0",
) -> SettingEntry:
    return SettingEntry(
        env_var=env_var,
        source="constants",
        default=default,
        description=f"test entry for {env_var}",
        introduced_in=introduced_in,
        semantics_changed_in=semantics_changed_in,
    )


@pytest.fixture
def tiny_registry(monkeypatch):
    """Install a small hand-built registry in place of the real one, via
    monkeypatching `all_settings` where `compute_upgrade_notice` looks it up."""

    def _install(entries: list[SettingEntry]):
        monkeypatch.setattr(
            "applire.settings_registry.all_settings", lambda: entries
        )

    return _install


def test_fresh_install_produces_no_notice(tiny_registry):
    tiny_registry([_entry("FOO", introduced_in="0.38.0")])
    notice = compute_upgrade_notice(last_seen=None, running="0.41.0", environ={})
    assert notice is None


def test_same_version_produces_no_notice(tiny_registry):
    tiny_registry([_entry("FOO", introduced_in="0.38.0")])
    notice = compute_upgrade_notice(last_seen="0.41.0", running="0.41.0", environ={})
    assert notice is None


def test_unreadable_last_seen_version_produces_no_notice(tiny_registry):
    tiny_registry([_entry("FOO", introduced_in="0.38.0")])
    notice = compute_upgrade_notice(last_seen="unknown", running="0.41.0", environ={})
    assert notice is None


def test_unreadable_running_version_produces_no_notice(tiny_registry):
    tiny_registry([_entry("FOO", introduced_in="0.38.0")])
    notice = compute_upgrade_notice(last_seen="0.31.0", running="unknown", environ={})
    assert notice is None


def test_a_downgrade_produces_no_notice(tiny_registry):
    tiny_registry([_entry("FOO", introduced_in="0.35.0")])
    notice = compute_upgrade_notice(last_seen="0.41.0", running="0.36.0", environ={})
    assert notice is None


def test_two_release_jump_lists_an_unset_setting_introduced_in_between(tiny_registry):
    tiny_registry(
        [_entry("NEW_THING", introduced_in="0.39.0", default="7")]
    )
    notice = compute_upgrade_notice(last_seen="0.38.0", running="0.40.0", environ={})
    assert notice is not None
    assert notice["unset"] == [
        {
            "env_var": "NEW_THING",
            "introduced_in": "0.39.0",
            "default": "7",
            "description": "test entry for NEW_THING",
        }
    ]
    assert notice["re_meant"] == []
    assert notice["from"] == "0.38.0"
    assert notice["to"] == "0.40.0"


def test_a_setting_introduced_in_the_jump_but_already_set_is_not_listed(tiny_registry):
    tiny_registry(
        [_entry("NEW_THING", introduced_in="0.39.0", default="7")]
    )
    notice = compute_upgrade_notice(
        last_seen="0.38.0", running="0.40.0", environ={"NEW_THING": "9"}
    )
    assert notice is None


def test_a_re_meant_setting_that_is_set_appears_in_re_meant(tiny_registry):
    tiny_registry(
        [
            _entry(
                "OLD_THING",
                introduced_in="0.31.0",
                semantics_changed_in="0.39.0",
                default="1",
            )
        ]
    )
    notice = compute_upgrade_notice(
        last_seen="0.38.0", running="0.40.0", environ={"OLD_THING": "5"}
    )
    assert notice is not None
    assert notice["re_meant"] == [
        {
            "env_var": "OLD_THING",
            "semantics_changed_in": "0.39.0",
            "default": "1",
            "description": "test entry for OLD_THING",
        }
    ]
    assert notice["unset"] == []


def test_a_re_meant_setting_that_is_unset_appears_in_neither_list(tiny_registry):
    # ADR-087 cl. 6, deliberate: an unset variable carries no operator intent
    # to break, so it must not surface in re_meant NOR get treated as a fresh
    # "unset" introduction (it was introduced long before the jump).
    tiny_registry(
        [
            _entry(
                "OLD_THING",
                introduced_in="0.31.0",
                semantics_changed_in="0.39.0",
                default="1",
            )
        ]
    )
    notice = compute_upgrade_notice(last_seen="0.38.0", running="0.40.0", environ={})
    assert notice is None


def test_a_jump_where_nothing_matches_returns_none_not_an_empty_dict(tiny_registry):
    tiny_registry([_entry("UNRELATED", introduced_in="0.20.0")])
    notice = compute_upgrade_notice(last_seen="0.38.0", running="0.40.0", environ={})
    assert notice is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("v0.41.1-beta", (0, 41, 1)),
        ("0.41.1-beta", (0, 41, 1)),
        ("0.36.2b0", (0, 36, 2)),
        ("0.42.0", (0, 42, 0)),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_release_reads_every_version_shape_the_product_produces(value, expected):
    assert parse_release(value) == expected


def test_format_upgrade_notice_log_mentions_every_env_var_in_the_notice():
    notice = {
        "from": "0.38.0",
        "to": "0.40.0",
        "unset": [
            {
                "env_var": "NEW_THING",
                "introduced_in": "0.39.0",
                "default": "7",
                "description": "a new thing",
            }
        ],
        "re_meant": [
            {
                "env_var": "OLD_THING",
                "semantics_changed_in": "0.39.0",
                "default": "1",
                "description": "an old thing, re-meant",
            }
        ],
    }
    log_text = format_upgrade_notice_log(notice)
    assert "NEW_THING" in log_text
    assert "OLD_THING" in log_text


# ---------------------------------------------------------------------------
# 7. current_environment merges .env under os.environ
# ---------------------------------------------------------------------------


def test_current_environment_reads_the_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ONLY_IN_DOTENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ONLY_IN_DOTENV=from-file\n", encoding="utf-8")

    merged = current_environment(str(env_file))
    assert merged["ONLY_IN_DOTENV"] == "from-file"


def test_current_environment_os_environ_wins_over_dotenv_on_collision(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BOTH_SET", "from-os-environ")
    env_file = tmp_path / ".env"
    env_file.write_text("BOTH_SET=from-dotenv-file\n", encoding="utf-8")

    merged = current_environment(str(env_file))
    # If this ever read `os.environ` alone (ignoring `.env`), a variable set
    # only in the operator's .env would wrongly report as unset — the loudest
    # possible way for the upgrade notice to be wrong (see the function's
    # docstring). os.environ winning on a collision matches pydantic-settings'
    # own precedence.
    assert merged["BOTH_SET"] == "from-os-environ"
