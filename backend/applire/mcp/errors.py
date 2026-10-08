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

"""
MCP error helpers — translate domain exceptions to structured McpError responses.

Error detail hardening: every message passes :func:`applire.redaction.scrub_secrets`
on its way out, and an exception no tool classified becomes
:func:`internal_unexpected` — a static message plus an error id, the exception
itself going to the server log only.
"""
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from applire.redaction import scrub_secrets

# JSON-RPC error codes
_NOT_FOUND = -32001
_INVALID_INPUT = -32602
_INTERNAL = -32603
# Applire: the agent identity no longer holds (ADR-091 cl. 17, MD-3).
_UNAUTHORIZED = -32003


def not_found(msg: str) -> McpError:
    return McpError(ErrorData(code=_NOT_FOUND, message=scrub_secrets(msg)))


def invalid_input(msg: str) -> McpError:
    return McpError(ErrorData(code=_INVALID_INPUT, message=scrub_secrets(msg)))


def internal(msg: str) -> McpError:
    return McpError(ErrorData(code=_INTERNAL, message=scrub_secrets(msg)))


def unauthorized(msg: str) -> McpError:
    return McpError(ErrorData(code=_UNAUTHORIZED, message=scrub_secrets(msg)))


def internal_unexpected(exc: BaseException, *, where: str) -> McpError:
    """The ``internal`` error for an exception the tool did not classify.

    Never ``str(exc)`` of an arbitrary exception: our own LLM error types keep
    their (static) message so the agent knows whether to retry; anything else
    answers a static message plus an error id. See
    :func:`applire.internal_errors.agent_facing_message`.
    """
    from applire.internal_errors import agent_facing_message

    return internal(agent_facing_message(exc, where=f"mcp.{where}"))
