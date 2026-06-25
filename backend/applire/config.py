# Copyright (C) 2024-2026 Tobias Rosenbaum
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

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Backend package root — the directory that contains the `applire/` package.
# config.py lives at <backend>/applire/config.py, so two parents up is <backend>.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def resolve_static_dir() -> Path:
    """Return the absolute path to the static-asset directory.

    Honours the STATIC_DIR env var when set; otherwise defaults to the
    package-relative ``<backend>/data/static``. Anchoring to the package root
    (rather than the CWD-relative "./data/static") keeps the path stable no
    matter which directory the process is launched from — launching uvicorn
    from the repo root used to serve an empty directory and 404 every template
    thumbnail.
    """
    env = os.getenv("STATIC_DIR")
    if env:
        return Path(env).resolve()
    return _BACKEND_ROOT / "data" / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    llm_provider: str = "mistral"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"
    openai_api_key: str = ""
    openai_base_url: str = ""          # empty = use OpenAI default; set to point at LM Studio etc.
    openai_model: str = "gpt-4o"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.2"
    openrouter_api_key: str = ""
    openrouter_model: str = "mistralai/mistral-large-latest"
    openrouter_base_url: str = ""          # empty = use https://openrouter.ai/api/v1
    openrouter_disable_thinking: bool = False  # global default; emits reasoning:{enabled:false} (cross-vendor: Gemini/Qwen/DeepSeek). Per-call disable_thinking overrides it (F-B)
    # Requesty — EU-hosted OpenAI-compat gateway (ADR-009 amended 2026-06-14)
    requesty_api_key: str = ""
    requesty_model: str = "mistralai/mistral-large-latest"  # set an EU-region model for full residency
    requesty_base_url: str = ""            # empty = use https://router.eu.requesty.ai/v1 (EU residency)
    # Anthropic — native Messages API; BYO-API-key only (Claude subscriptions are not usable)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    llm_timeout: int = 120                 # seconds; raise for thinking/reasoning models (e.g. Qwen3, o3)
    embedding_provider: str = "noop"
    embedding_model: str = ""             # empty = use provider default
    # Combined score weights for GET /api/jobs/match (must sum to 1.0)
    matching_score_embedding_weight: float = 0.4
    matching_score_llm_weight: float = 0.6
    auth_provider: str = "none"
    mcp_transport: str = "stdio"
    applire_base_url: str = "http://localhost:8001"
    upload_dir: str = "./data/uploads"
    storage_backend: str = "local"
    ocr_backend: str = "mistral_vision"
    cors_origins: str = "*"
    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR — applied to all applire.* loggers


settings = Settings()

# Edition detection: presence of the applire.cloud package IS the gate (ADR 012).
# APPLIRE_EDITION env var has been removed — do not re-add it.
try:
    import applire.cloud  # type: ignore[import-not-found]
    HAS_CLOUD = True
except ImportError:
    HAS_CLOUD = False
