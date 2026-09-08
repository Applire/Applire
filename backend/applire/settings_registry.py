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

"""The declared configuration surface (ADR-087).

One entry per environment variable the product reads — the 39 typed `Settings`
fields of `config.py`, the 29 direct `os.environ.get` reads of `constants.py`,
the two paths read outside both, and the deployment variables the compose file
and the frontend consume.

Three things depend on this module, which is why it is data and not prose:

1. **`.env.example` is generated from it** (`scripts/generate_env_example.py`).
   That file is a published release asset — `releases/latest/download/env.example`,
   the README's documented install path, guarded by `.github/workflows/compose-guard.yml`
   — so a hand-maintained copy is how the shipped template drifts from the code.
   It did: `MISTRAL_MODEL`'s example said `mistral-medium-latest` while the code
   defaulted to `mistral-small-latest` (JF-O-2.2).
2. **`introduced_in` / `semantics_changed_in` drive the version-jump notice.**
   After an upgrade the instance names the settings a release introduced that
   this environment does not set, and the settings whose *meaning* changed that
   it does (JF-O-6.1 / JF-O-6.2). ADR-080 is the worked re-meant case.
3. **A test asserts declared == read, in both directions**
   (`tests/unit/test_settings_registry.py`). A new environment variable costs an
   entry here or the suite is red. That tax is the point: the failure mode being
   closed is "someone added a variable and nobody wrote it down".

`introduced_in` values were derived mechanically rather than remembered — for
each declaration, the first commit that introduced it
(``git log --reverse -S<name> -- <file>``) and then the earliest tag containing
that commit (``git tag --contains``). ``0.31.0`` means "present in the first
public release". A new entry names the release it will ship in.

Adding a variable:
  * add the entry below (or call ``register()`` from your own module, the way
    `services/ops/config.py` does),
  * set ``introduced_in`` to the release you are shipping in,
  * run ``python3 scripts/generate_env_example.py`` and commit the diff,
  * if you changed what an EXISTING variable means, set
    ``semantics_changed_in`` and add it to the CHANGELOG's ``### Upgrade notes``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

#: Section order in the generated file. An entry naming a section absent from
#: this list is a hard error in the generator, not a silently appended block.
SECTION_ORDER: tuple[str, ...] = (
    "Database",
    "LLM provider",
    "LLM behaviour",
    "Operations",
    "Retention (GDPR)",
    "Storage and uploads",
    "Network and access",
    "Advanced tuning",
)


@dataclass(frozen=True)
class SettingEntry:
    """One declared environment variable.

    `default` is the value the code falls back to, rendered as the operator
    would type it — `""` for "empty means unset", never `None`.

    `example` is what the generated template shows. It defaults to `default`,
    which is exactly the property JF-O-2.2 was missing: the template cannot
    contradict the code unless someone *deliberately* sets a different example
    (an API-key placeholder), and the generator then prints the code default
    beside it so the difference is visible rather than misleading.
    """

    env_var: str
    source: str  # "config" | "constants" | "compose" | "frontend"
    default: str
    description: str
    introduced_in: str
    section: str = "Advanced tuning"
    name: str = ""
    semantics_changed_in: str | None = None
    example: str | None = None
    #: Written as `#VAR=value` (an optional setting the operator may uncomment)
    #: rather than `VAR=value`. Only the handful of variables a fresh install
    #: genuinely has to fill in are active lines.
    commented: bool = True
    #: The value is a credential. The generated template never shows a real one.
    secret: bool = False
    #: Declared but withheld from the template — for a variable with no operator
    #: audience. Withholding is visible here; an omission would be visible nowhere.
    in_env_example: bool = True
    #: Free-text notes for a reader of this module. Never rendered.
    notes: str = ""

    def __post_init__(self) -> None:
        if self.source not in ("config", "constants", "compose", "frontend"):
            raise ValueError(f"{self.env_var}: unknown source {self.source!r}")
        if self.section not in SECTION_ORDER:
            raise ValueError(f"{self.env_var}: unknown section {self.section!r}")
        if not self.name:
            object.__setattr__(self, "name", self.env_var.lower())

    @property
    def shown_value(self) -> str:
        """What the generated template puts after the `=`."""
        return self.default if self.example is None else self.example


_REGISTRY: dict[str, SettingEntry] = {}


def register(entry: SettingEntry) -> SettingEntry:
    """Add an entry. A duplicate `env_var` is an error, never an overwrite."""
    if entry.env_var in _REGISTRY:
        raise ValueError(f"{entry.env_var} is already registered")
    _REGISTRY[entry.env_var] = entry
    return entry


def all_settings() -> list[SettingEntry]:
    """Every registered entry, in section order then declaration order."""
    order = {s: i for i, s in enumerate(SECTION_ORDER)}
    return sorted(_REGISTRY.values(), key=lambda e: order[e.section])


def get(env_var: str) -> SettingEntry | None:
    return _REGISTRY.get(env_var)


def _register_all(entries: Iterable[SettingEntry]) -> None:
    for entry in entries:
        register(entry)


# --------------------------------------------------------------------------
# Release-version arithmetic
#
# Tags read `v0.41.1-beta`; the package metadata reads `0.41.1-beta`; an
# `introduced_in` reads `0.41.0`. All three reduce to the same triple, and the
# comparison is at RELEASE granularity — a pre-release suffix never decides
# whether a setting is new to an operator.
# --------------------------------------------------------------------------

_RELEASE_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_release(value: str | None) -> tuple[int, int, int] | None:
    """`"v0.41.1-beta"` -> `(0, 41, 1)`. Returns None for anything unreadable.

    `applire._version.__version__` is `"unknown"` when the package metadata is
    missing (a source checkout that was never `pip install -e`'d), and a
    comparison against "unknown" must produce *no notice*, never a wrong one.
    """
    if not value:
        return None
    match = _RELEASE_RE.search(value)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def compute_upgrade_notice(
    *,
    last_seen: str | None,
    running: str,
    environ: dict[str, str],
) -> dict | None:
    """The version-jump notice, or None when there is nothing to report.

    `unset[]`  — introduced after `last_seen` and NOT set in this environment:
                 the operator inherits a new default without being asked.
    `re_meant[]` — semantics changed after `last_seen` and the variable IS set:
                 the operator's own value now means something else.

    A re-meant variable that is *unset* is deliberately absent from both lists:
    an unset variable carries no operator intent to break. (ADR-087 cl. 6.)
    """
    from_version = parse_release(last_seen)
    to_version = parse_release(running)
    if from_version is None or to_version is None:
        return None
    if to_version <= from_version:
        return None

    unset: list[dict] = []
    re_meant: list[dict] = []
    for entry in all_settings():
        introduced = parse_release(entry.introduced_in)
        changed = parse_release(entry.semantics_changed_in)
        is_set = bool(environ.get(entry.env_var, "").strip())
        if introduced is not None and introduced > from_version and not is_set:
            unset.append(
                {
                    "env_var": entry.env_var,
                    "introduced_in": entry.introduced_in,
                    "default": entry.default,
                    "description": _first_line(entry.description),
                }
            )
        elif changed is not None and changed > from_version and is_set:
            re_meant.append(
                {
                    "env_var": entry.env_var,
                    "semantics_changed_in": entry.semantics_changed_in,
                    "default": entry.default,
                    "description": _first_line(entry.description),
                }
            )

    if not unset and not re_meant:
        return None
    return {
        "from": last_seen,
        "to": running,
        "unset": unset,
        "re_meant": re_meant,
    }


def _first_line(text: str) -> str:
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def format_upgrade_notice_log(notice: dict) -> str:
    """The multi-line WARNING block written to the log at startup."""
    lines = [
        "Applire was last started as %s and is now running %s."
        % (notice["from"], notice["to"]),
    ]
    if notice["unset"]:
        lines.append(
            "  Settings introduced since then that your environment does NOT set "
            "(the new default applies):"
        )
        for item in notice["unset"]:
            lines.append(
                "    %s  (since %s, default %s) — %s"
                % (
                    item["env_var"],
                    item["introduced_in"],
                    item["default"] or "empty",
                    item["description"],
                )
            )
    if notice["re_meant"]:
        lines.append(
            "  Settings you DO set whose meaning changed since then "
            "(your value now does something else):"
        )
        for item in notice["re_meant"]:
            lines.append(
                "    %s  (changed in %s) — %s"
                % (
                    item["env_var"],
                    item["semantics_changed_in"],
                    item["description"],
                )
            )
    lines.append(
        "  See the CHANGELOG's 'Upgrade notes' and docs/SELF-HOSTING.md. "
        "Dismiss on the dashboard, or: "
        "curl -XPOST http://localhost/api/settings/upgrade-notice/dismiss"
    )
    return "\n".join(lines)


# ==========================================================================
# The entries
# ==========================================================================

# ---- Database ------------------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="DATABASE_URL",
            source="config",
            default="postgresql+asyncpg://applire:applire@postgres:5432/applire",
            section="Database",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "PostgreSQL connection string. Under docker compose the compose file "
                "builds this from POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB "
                "below, so change those rather than this line."
            ),
        ),
        SettingEntry(
            env_var="POSTGRES_USER",
            source="compose",
            default="applire",
            section="Database",
            introduced_in="0.42.0",
            description=(
                "Database credentials. The defaults are the values every install has "
                "used so far, so changing nothing changes nothing. On a host where the "
                "database port could ever be reachable, set all three — and remember "
                "that Postgres reads them only when the data volume is FIRST created; "
                "an existing volume keeps its original credentials (docs/SELF-HOSTING.md, "
                "Secrets)."
            ),
        ),
        SettingEntry(
            env_var="POSTGRES_PASSWORD",
            source="compose",
            default="applire",
            section="Database",
            secret=True,
            introduced_in="0.42.0",
            description="Database password — see POSTGRES_USER above.",
        ),
        SettingEntry(
            env_var="POSTGRES_DB",
            source="compose",
            default="applire",
            section="Database",
            introduced_in="0.42.0",
            description="Database name — see POSTGRES_USER above.",
        ),
    ]
)

# ---- LLM provider --------------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="LLM_PROVIDER",
            source="config",
            default="mistral",
            section="LLM provider",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "Bring your own key: pick any provider below and supply its key.\n"
                "Your data goes only to the provider you choose. No provider is privileged.\n"
                "\n"
                "The value below is an EXAMPLE, not a recommendation. It decides where your CVs\n"
                "and answers are sent, so choose it deliberately at install time — with whoever\n"
                "the data belongs to — rather than inheriting it:\n"
                "  ollama                        stays on your machine, no key, no cloud\n"
                "  mistral, requesty             stay in the EU (Requesty also routes to\n"
                "                                Claude/GPT/Gemini via their EU deployments)\n"
                "  openrouter, anthropic, openai US-hosted\n"
                "Choosing a model? See docs/llm-models.md (capability floor + recommendations).\n"
                "One of: mistral | requesty | openrouter | anthropic | openai | ollama"
            ),
        ),
        SettingEntry(
            env_var="MISTRAL_API_KEY",
            source="config",
            default="",
            example="your-mistral-api-key-here",
            section="LLM provider",
            commented=False,
            secret=True,
            introduced_in="0.31.0",
            description="Mistral AI — EU-hosted, strong German proficiency.",
        ),
        SettingEntry(
            env_var="MISTRAL_MODEL",
            source="config",
            default="mistral-small-latest",
            section="LLM provider",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "code default — pick per docs/llm-models.md. Larger Mistral models "
                "(mistral-medium-latest, mistral-large-latest) follow the reconcile and "
                "review prompts more reliably; this line shows what the code uses when "
                "you delete it."
            ),
        ),
        SettingEntry(
            env_var="OPENROUTER_API_KEY",
            source="config",
            default="",
            example="your-openrouter-api-key-here",
            section="LLM provider",
            commented=False,
            secret=True,
            introduced_in="0.31.0",
            description=(
                "OpenRouter — multi-model gateway (set LLM_PROVIDER=openrouter).\n"
                "Bypasses per-provider rate limits. Get a key at https://openrouter.ai/keys"
            ),
        ),
        SettingEntry(
            env_var="OPENROUTER_MODEL",
            source="config",
            default="mistralai/mistral-large-latest",
            section="LLM provider",
            commented=False,
            introduced_in="0.31.0",
            description="code default — pick per docs/llm-models.md.",
        ),
        SettingEntry(
            env_var="OPENROUTER_BASE_URL",
            source="config",
            default="",
            section="LLM provider",
            introduced_in="0.31.0",
            description="leave empty to use https://openrouter.ai/api/v1",
        ),
        SettingEntry(
            env_var="OPENROUTER_DISABLE_THINKING",
            source="config",
            default="false",
            section="LLM provider",
            introduced_in="0.31.0",
            description=(
                "Emit reasoning:{enabled:false} on every OpenRouter call (cross-vendor: "
                "Gemini/Qwen/DeepSeek). A per-call override still wins. Some models "
                "MANDATE reasoning and reject this — use OPENROUTER_REASONING_EFFORT there."
            ),
        ),
        SettingEntry(
            env_var="OPENROUTER_REASONING_EFFORT",
            source="config",
            default="",
            section="LLM provider",
            introduced_in="0.37.0",
            description=(
                "low | medium | high — bound reasoning on models that keep it ON. "
                "Reasoning tokens share the max_tokens budget, so an over-thinking model "
                "can burn the budget and truncate the visible answer. Empty = let the "
                "model decide."
            ),
        ),
        SettingEntry(
            env_var="REQUESTY_API_KEY",
            source="config",
            default="",
            example="your-requesty-api-key-here",
            section="LLM provider",
            commented=False,
            secret=True,
            introduced_in="0.37.0",
            description=(
                "Requesty — EU-hosted gateway (Frankfurt, zero-retention), set "
                "LLM_PROVIDER=requesty. Also an EU-resident path to Claude/GPT/Gemini "
                "(Bedrock/Azure/Vertex EU deployments). Get a key at https://app.requesty.ai"
            ),
        ),
        SettingEntry(
            env_var="REQUESTY_MODEL",
            source="config",
            default="mistralai/mistral-large-latest",
            section="LLM provider",
            commented=False,
            introduced_in="0.37.0",
            description=(
                "For FULL residency pick an EU-region model, "
                "e.g. bedrock/claude-sonnet-4-5-v2@eu-central-1"
            ),
        ),
        SettingEntry(
            env_var="REQUESTY_BASE_URL",
            source="config",
            default="",
            section="LLM provider",
            introduced_in="0.37.0",
            description=(
                "empty = https://router.eu.requesty.ai/v1 (EU); use "
                "https://router.requesty.ai/v1 for the global router"
            ),
        ),
        SettingEntry(
            env_var="REQUESTY_DISABLE_THINKING",
            source="config",
            default="false",
            section="LLM provider",
            introduced_in="0.38.0",
            description="Emit reasoning_effort:\"none\"; a per-call override still wins.",
        ),
        SettingEntry(
            env_var="REQUESTY_REASONING_EFFORT",
            source="config",
            default="",
            section="LLM provider",
            introduced_in="0.38.0",
            description="low | medium | high | max — bound reasoning when it stays on.",
        ),
        SettingEntry(
            env_var="ANTHROPIC_API_KEY",
            source="config",
            default="",
            example="your-anthropic-api-key-here",
            section="LLM provider",
            commented=False,
            secret=True,
            introduced_in="0.37.0",
            description=(
                "Anthropic (Claude) — native Messages API, set LLM_PROVIDER=anthropic.\n"
                "BYO-API-key ONLY: a Claude Pro/Max/Team SUBSCRIPTION cannot be used —\n"
                "Anthropic prohibits subscription auth in third-party apps. Get a key at\n"
                "https://console.anthropic.com\n"
                "US-hosted; for Claude with EU residency use LLM_PROVIDER=requesty instead."
            ),
        ),
        SettingEntry(
            env_var="ANTHROPIC_MODEL",
            source="config",
            default="claude-sonnet-4-6",
            section="LLM provider",
            introduced_in="0.37.0",
            description=(
                "Model ids move fast — take a current one from "
                "https://docs.anthropic.com/en/docs/about-claude/models rather than an "
                "example pinned in this file."
            ),
        ),
        SettingEntry(
            env_var="OPENAI_API_KEY",
            source="config",
            default="",
            example="your-openai-api-key-here",
            section="LLM provider",
            commented=False,
            secret=True,
            introduced_in="0.31.0",
            description=(
                "OpenAI or any OpenAI-compatible server (set LLM_PROVIDER=openai)."
            ),
        ),
        SettingEntry(
            env_var="OPENAI_MODEL",
            source="config",
            default="gpt-4o",
            section="LLM provider",
            introduced_in="0.31.0",
            description="code default.",
        ),
        SettingEntry(
            env_var="OPENAI_BASE_URL",
            source="config",
            default="",
            example="http://host.docker.internal:1234/v1",
            section="LLM provider",
            introduced_in="0.31.0",
            description=(
                "For LM Studio: set this and leave OPENAI_API_KEY as any non-empty "
                "string. Empty = the OpenAI default endpoint."
            ),
        ),
        SettingEntry(
            env_var="OLLAMA_BASE_URL",
            source="config",
            default="http://ollama:11434",
            section="LLM provider",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "Ollama — fully offline (set LLM_PROVIDER=ollama).\n"
                "Start with: docker compose --profile ollama up\n"
                "The server starts EMPTY: pull a model once\n"
                "(docker compose exec ollama ollama pull llama3.2) or set OLLAMA_MODEL to\n"
                "one you pulled; CPU-only inference is slow — raise LLM_TIMEOUT (e.g. 600)."
            ),
        ),
        SettingEntry(
            env_var="OLLAMA_MODEL",
            source="config",
            default="llama3.2",
            section="LLM provider",
            commented=False,
            introduced_in="0.31.0",
            description="The model you pulled into the Ollama container.",
        ),
    ]
)

# ---- LLM behaviour -------------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="LLM_TIMEOUT",
            source="config",
            default="120",
            example="180",
            section="LLM behaviour",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "Seconds. Raise for thinking/reasoning models (Qwen3, o3, DeepSeek-R1 "
                "etc.) — code default 120. Keep it BELOW the reverse proxy's read "
                "timeout (300 s, baked into the applire-nginx image) or the proxy cuts "
                "the connection first and the operator sees a 504 instead of the real "
                "error."
            ),
        ),
        SettingEntry(
            env_var="LLM_MAX_OUTPUT_TOKENS",
            source="config",
            default="0",
            section="LLM behaviour",
            introduced_in="0.37.0",
            description=(
                "Operator-declared hard output cap for the chosen model, in tokens. "
                "0 = unset (no known cap). When set, every requested max_tokens is "
                "clamped to it, so a capped model is never asked for more than it can "
                "emit (ADR-047 §2). Optional: segmentation already handles capped "
                "models without metadata."
            ),
        ),
        SettingEntry(
            env_var="LLM_DEBUG_LOG",
            source="config",
            default="false",
            example="true",
            section="LLM behaviour",
            introduced_in="0.37.0",
            description=(
                "Developer-only: log every LLM call's full input/output to\n"
                "<LLM_DEBUG_LOG_DIR>/<date>.jsonl (one JSON line per call: stage, model,\n"
                "system, prompt, params, response, latency).\n"
                "RECORDS CV PII — keep OFF in production. While it is on, the backend logs\n"
                "a WARNING at every startup and GET /health reports debug_log_on: true.\n"
                "There is deliberately no size or age cap: a cap on a diagnostic tool\n"
                "truncates evidence silently. Turn it off, and delete the files."
            ),
        ),
        SettingEntry(
            env_var="LLM_DEBUG_LOG_DIR",
            source="config",
            default="logs/llm",
            section="LLM behaviour",
            introduced_in="0.37.0",
            description="Where those JSONL files land, relative to the backend's workdir.",
        ),
        SettingEntry(
            env_var="EMBEDDING_PROVIDER",
            source="config",
            default="noop",
            section="LLM behaviour",
            introduced_in="0.31.0",
            description=(
                "Embeddings for the job-match score. 'noop' (the default) skips them "
                "and the match score falls back to its LLM half."
            ),
        ),
        SettingEntry(
            env_var="EMBEDDING_MODEL",
            source="config",
            default="",
            section="LLM behaviour",
            introduced_in="0.31.0",
            description="Empty = the embedding provider's own default.",
        ),
    ]
)

# ---- Operations ----------------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="LOG_LEVEL",
            source="config",
            default="INFO",
            section="Operations",
            introduced_in="0.31.0",
            description=(
                "DEBUG | INFO | WARNING | ERROR — applied to every applire.* logger. "
                "DEBUG is loud; it does not log prompts (that is LLM_DEBUG_LOG)."
            ),
        ),
        SettingEntry(
            env_var="APPLIRE_TOPOLOGY",
            source="config",
            default="production",
            section="Operations",
            introduced_in="0.42.0",
            description=(
                "Which compose topology this instance is running: 'production'\n"
                "(docker-compose.yml alone — nginx on :80 is the only published port) or\n"
                "'dev' (docker-compose.override.yml is also applied — builds from source,\n"
                "hot-reload backend, and 3000/8001/5433 published).\n"
                "Do NOT set this by hand. The override file sets it; leaving it unset is\n"
                "what makes 'production' true. A 'dev' value logs a WARNING at startup and\n"
                "appears at GET /health — running a clone with a plain `docker compose up`\n"
                "silently applies the override, which is a debugging topology."
            ),
        ),
    ]
)

# ---- Retention (GDPR) ----------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="GENERATED_DOCUMENTS_TTL_DAYS",
            source="constants",
            default="90",
            section="Retention (GDPR)",
            introduced_in="0.31.0",
            description=(
                "GDPR retention TTLs, in DAYS, enforced by the retention worker once a\n"
                "day (ADR-005). Deletion is irreversible — read docs/SELF-HOSTING.md\n"
                "before changing one.\n"
                "Generated CVs and cover letters, counted from the application's last\n"
                "activity."
            ),
        ),
        SettingEntry(
            env_var="CANCELLED_APPLICATION_TTL_DAYS",
            source="constants",
            default="7",
            section="Retention (GDPR)",
            introduced_in="0.38.0",
            description=(
                "Grace window after an application is cancelled. Its documents "
                "(including submitted pins) are purged after this regardless of "
                "GENERATED_DOCUMENTS_TTL_DAYS. 0 disables the short clock."
            ),
        ),
        SettingEntry(
            env_var="INTERVIEW_SESSION_TTL_DAYS",
            source="constants",
            default="30",
            section="Retention (GDPR)",
            introduced_in="0.31.0",
            description="Interview transcripts, counted from the last message.",
        ),
        SettingEntry(
            env_var="UPLOAD_TTL_DAYS",
            source="constants",
            default="7",
            section="Retention (GDPR)",
            introduced_in="0.31.0",
            description=(
                "Uploaded CV files. The extracted content lives on in the profile; "
                "the file itself is deleted."
            ),
        ),
        SettingEntry(
            env_var="PROFILE_INACTIVITY_TTL_DAYS",
            source="constants",
            default="730",
            section="Retention (GDPR)",
            introduced_in="0.31.0",
            description=(
                "The whole profile, after this long without activity. Two years by "
                "default. This is the vault — the candidate's career record."
            ),
        ),
        SettingEntry(
            env_var="ORPHAN_FILE_GRACE_HOURS",
            source="constants",
            default="1",
            section="Retention (GDPR)",
            introduced_in="0.38.0",
            description=(
                "The orphan-file scan skips files younger than this: an upload is "
                "written before its database row is committed, so a young unreferenced "
                "file may be an upload in flight rather than an orphan."
            ),
        ),
    ]
)

# ---- Storage and uploads -------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="UPLOAD_DIR",
            source="config",
            default="./data/uploads",
            section="Storage and uploads",
            introduced_in="0.31.0",
            description=(
                "Where uploaded CVs and profile photos are written inside the backend "
                "container. The compose file mounts the applire_uploads volume here — "
                "if you change this, change the volume mount too, or every uploaded file "
                "is lost on the next `docker compose pull`."
            ),
        ),
        SettingEntry(
            env_var="STORAGE_BACKEND",
            source="config",
            default="local",
            section="Storage and uploads",
            introduced_in="0.31.0",
            description="'local' is the only Community backend.",
        ),
        SettingEntry(
            env_var="OCR_BACKEND",
            source="config",
            default="mistral_vision",
            section="Storage and uploads",
            introduced_in="0.31.0",
            description=(
                "How a scanned or image-only PDF is read. 'mistral_vision' needs a "
                "Mistral key even when LLM_PROVIDER is something else."
            ),
        ),
        SettingEntry(
            env_var="STATIC_DIR",
            source="config",
            default="<backend>/data/static",
            section="Storage and uploads",
            introduced_in="0.36.0",
            description=(
                "Absolute path to the static-asset directory (CV template thumbnails). "
                "Empty = the package-relative default, which is what the image ships. "
                "Set only if you serve those assets from elsewhere."
            ),
        ),
    ]
)

# ---- Network and access --------------------------------------------------

_register_all(
    [
        SettingEntry(
            env_var="AUTH_PROVIDER",
            source="config",
            default="none",
            section="Network and access",
            commented=False,
            introduced_in="0.31.0",
            description=(
                "'none' disables authentication, which is the only Community option and\n"
                "is appropriate for a SINGLE-PERSON self-hosted instance. Anyone who can\n"
                "reach the URL can read and change the vault — do not put an\n"
                "AUTH_PROVIDER=none instance on a shared network without your own\n"
                "authenticating proxy in front of it (docs/SELF-HOSTING.md).\n"
                "Zitadel OIDC is a Cloud Edition feature."
            ),
        ),
        SettingEntry(
            env_var="CORS_ORIGINS",
            source="config",
            default="*",
            section="Network and access",
            introduced_in="0.31.0",
            description=(
                "Comma-separated list of allowed origins. '*' is safe for a single-user "
                "self-hosted install with AUTH_PROVIDER=none; lock it down otherwise, "
                "e.g. CORS_ORIGINS=https://app.example.com"
            ),
        ),
        SettingEntry(
            env_var="APPLIRE_BASE_URL",
            source="config",
            default="http://localhost:8001",
            section="Network and access",
            introduced_in="0.31.0",
            description=(
                "MCP / agent channel — the base URL used to build html_url / pdf_url in\n"
                "tool responses (generate_cv, get_cv_status, generate_cover_letter, ...).\n"
                "The default is correct only for a local, unproxied dev setup. Set this to\n"
                "the externally reachable scheme://host:port of your reverse proxy for any\n"
                "other deployment, or agent-fetched artifact links silently point at\n"
                "localhost:8001 instead of your real host."
            ),
        ),
        SettingEntry(
            env_var="MCP_TRANSPORT",
            source="config",
            default="stdio",
            section="Network and access",
            introduced_in="0.31.0",
            description=(
                "Transport for `python -m applire.mcp`. 'stdio' is the supported "
                "Community transport (ADR-010)."
            ),
        ),
        SettingEntry(
            env_var="NEXT_PUBLIC_API_URL",
            source="frontend",
            default="",
            example="http://localhost:8001",
            section="Network and access",
            introduced_in="0.31.0",
            description=(
                "Frontend API URL.\n"
                "docker compose: leave this EMPTY — the nginx service at :80 routes\n"
                "/api/* to the backend and the browser uses relative paths.\n"
                "standalone (npm run dev outside Docker): set it to http://localhost:8001\n"
                "so the frontend talks to the backend directly."
            ),
        ),
    ]
)

# ---- Advanced tuning -----------------------------------------------------
#
# Everything below is read by `constants.py` and was, until ADR-087, readable
# and undeclared: nothing told the operator these existed, what unit they were
# in, or what they defaulted to (JF-O-4.2). They are here because "declared"
# and "recommended" are different things — leave them alone unless a real run
# gives you a reason.

_register_all(
    [
        SettingEntry(
            env_var="INTERVIEW_MAX_QUESTIONS_TARGETED",
            source="config",
            default="30",
            section="Advanced tuning",
            introduced_in="0.38.0",
            semantics_changed_in="0.41.0",
            description=(
                "Interview question-count CAP for a targeted (MODE A) interview.\n"
                "CHANGED IN 0.41.0 (ADR-080): this is a CAP, not the budget. A session's\n"
                "budget is now derived from its own gap plan —\n"
                "INTERVIEW_MAX_QUESTIONS_PER_GAP x (gaps to work through) + 2 — and this\n"
                "value is applied afterwards as an upper bound. Previously it WAS the\n"
                "budget. Setting it BELOW the derived budget truncates interviews on\n"
                "gap-rich jobs: the candidate is told the question limit was reached, with\n"
                "real gaps left unasked. Leave unset unless you need to bound cost."
            ),
        ),
        SettingEntry(
            env_var="INTERVIEW_MAX_QUESTIONS_GUIDED",
            source="config",
            default="30",
            section="Advanced tuning",
            introduced_in="0.38.0",
            semantics_changed_in="0.41.0",
            description=(
                "The same cap for a guided (MODE B) interview. Same 0.41.0 change of "
                "meaning as INTERVIEW_MAX_QUESTIONS_TARGETED above."
            ),
        ),
        SettingEntry(
            env_var="INTERVIEW_MAX_QUESTIONS_PER_GAP",
            source="constants",
            default="2",
            section="Advanced tuning",
            introduced_in="0.31.0",
            description=(
                "Questions a single gap may consume (its question plus a follow-up or "
                "denial probe). Also the multiplier in the ADR-080 budget derivation, so "
                "raising it lengthens interviews twice over."
            ),
        ),
        SettingEntry(
            env_var="INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.36.0",
            description=(
                "Retries for the interview question's output-language review (ADR-038). "
                "0 disables it and leaves the language directive alone to do the job."
            ),
        ),
        SettingEntry(
            env_var="LLM_REVIEW_MAX_RETRIES",
            source="constants",
            default="2",
            section="Advanced tuning",
            introduced_in="0.31.0",
            description=(
                "LLM review layer — max generator retries per reviewed step (ADR-021). "
                "0 disables the review layer entirely, which also disables both terminal "
                "reviews. Raising it multiplies provider calls."
            ),
        ),
        SettingEntry(
            env_var="CV_TERMINAL_REVIEW_MAX_RETRIES",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "Retries in the CV's TERMINAL review round, over the composed document "
                "(ADR-076 cl. 3). Deliberately tighter than LLM_REVIEW_MAX_RETRIES."
            ),
        ),
        SettingEntry(
            env_var="CV_TERMINAL_REENTRY_MAX",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "How often a post-verdict change may re-enter the CV's terminal review "
                "before the document ships with the gap logged."
            ),
        ),
        SettingEntry(
            env_var="LETTER_TERMINAL_REVIEW_MAX_RETRIES",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description="The cover letter's twin of CV_TERMINAL_REVIEW_MAX_RETRIES.",
        ),
        SettingEntry(
            env_var="LETTER_TERMINAL_REENTRY_MAX",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description="The cover letter's twin of CV_TERMINAL_REENTRY_MAX.",
        ),
        SettingEntry(
            env_var="CV_LANGUAGE_REVIEW_MAX_RETRIES",
            source="constants",
            default="2",
            section="Advanced tuning",
            introduced_in="0.37.0",
            description=(
                "Retries for the generated document's output-language review (ADR-038). "
                "2, not 1: the language pass is the last writer in the pipeline and "
                "carries the coverage gate, so its own output has to be reviewable."
            ),
        ),
        SettingEntry(
            env_var="CRITIC_ENABLED",
            source="constants",
            default="true",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "The outcome critic's cross-document coherence pass (ADR-060). "
                "Default ON; set false to save one provider call per application."
            ),
        ),
        SettingEntry(
            env_var="CRITIC_MAX_ROUNDS",
            source="constants",
            default="1",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "Retry budget for the critic's single judgement call on malformed "
                "output. The critic never asks the writer to try again."
            ),
        ),
        SettingEntry(
            env_var="CV_MAX_SKILLS",
            source="constants",
            default="24",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description=(
                "How many skills the tailored CV may present. JD-required skills that "
                "exist in the profile are kept even past the cap; no-relevance tags are "
                "dropped first."
            ),
        ),
        SettingEntry(
            env_var="SNAPSHOT_MAX_PER_PROFILE",
            source="constants",
            default="10",
            section="Advanced tuning",
            introduced_in="0.37.0",
            description=(
                "Pre-merge profile snapshots kept per profile (the undo history for an "
                "import). The most recent N survive."
            ),
        ),
        SettingEntry(
            env_var="MERGE_DATALOSS_CRITICAL_THRESHOLD",
            source="constants",
            default="3",
            section="Advanced tuning",
            introduced_in="0.37.0",
            description=(
                "Reconciliation delta STRICTLY ABOVE this makes a merge finding critical "
                "rather than a dismissible review."
            ),
        ),
        SettingEntry(
            env_var="MERGE_CONFIDENCE_REVIEW_THRESHOLD",
            source="constants",
            default="0.75",
            section="Advanced tuning",
            introduced_in="0.37.0",
            description="Merge confidence BELOW this asks the candidate to review.",
        ),
        SettingEntry(
            env_var="ORACLE_PROSE_FALLBACK_CHARS",
            source="constants",
            default="600",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description=(
                "Truthfulness Oracle (ADR-052). A raw prose block goes to the LLM "
                "segmentation fallback only when it is longer than this AND deterministic "
                "sentence splitting found no boundaries."
            ),
        ),
        SettingEntry(
            env_var="ORACLE_SEGMENT_MAX_TOKENS",
            source="constants",
            default="800",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description="Output cap for one Oracle segmentation call.",
        ),
        SettingEntry(
            env_var="ORACLE_MAX_SEGMENT_CALLS",
            source="constants",
            default="5",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description=(
                "Hard cap on segmentation calls per audited document. Exhausted, the "
                "block is treated as one claim."
            ),
        ),
        SettingEntry(
            env_var="ORACLE_ENTAILMENT_MAX_TOKENS",
            source="constants",
            default="200",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description="Output cap for one narrow entailment verdict.",
        ),
        SettingEntry(
            env_var="ORACLE_MAX_ENTAILMENT_CALLS",
            source="constants",
            default="10",
            section="Advanced tuning",
            introduced_in="0.38.0",
            description=(
                "Hard cap on entailment calls per audited document; claims past it fall "
                "back to the deterministic verdict."
            ),
        ),
        SettingEntry(
            env_var="ORACLE_JUDGEMENT_MAX_TOKENS",
            source="constants",
            default="120",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description="Per-item token budget for a batched equivalence judgement (ADR-068).",
        ),
        SettingEntry(
            env_var="ORACLE_MAX_JUDGEMENT_CALLS",
            source="constants",
            default="5",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "Hard cap on judgement batch calls per document; candidates past it "
                "degrade to judgement_unavailable rather than to a guess."
            ),
        ),
        SettingEntry(
            env_var="ORACLE_TRIAGE_MAX_TOKENS",
            source="constants",
            default="160",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description="Per-item token budget for the batched pre-grading triage call.",
        ),
        SettingEntry(
            env_var="ORACLE_MAX_TRIAGE_CALLS",
            source="constants",
            default="3",
            section="Advanced tuning",
            introduced_in="0.39.0",
            description=(
                "Hard cap on triage batch calls. Triage gates every letter claim, so "
                "exhausting this audits the remaining sentences as candidate claims."
            ),
        ),
        SettingEntry(
            env_var="MATCHING_SCORE_EMBEDDING_WEIGHT",
            source="config",
            default="0.4",
            section="Advanced tuning",
            introduced_in="0.31.0",
            description=(
                "Weights for GET /api/jobs/match. Must sum to 1.0 with "
                "MATCHING_SCORE_LLM_WEIGHT."
            ),
        ),
        SettingEntry(
            env_var="MATCHING_SCORE_LLM_WEIGHT",
            source="config",
            default="0.6",
            section="Advanced tuning",
            introduced_in="0.31.0",
            description="See MATCHING_SCORE_EMBEDDING_WEIGHT.",
        ),
    ]
)
