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
"""
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

# JSON-RPC error codes
_NOT_FOUND = -32001
_INVALID_INPUT = -32602
_INTERNAL = -32603
# Applire: the agent identity no longer holds (ADR-091 cl. 17, MD-3).
_UNAUTHORIZED = -32003


def not_found(msg: str) -> McpError:
    return McpError(ErrorData(code=_NOT_FOUND, message=msg))


def invalid_input(msg: str) -> McpError:
    return McpError(ErrorData(code=_INVALID_INPUT, message=msg))


def internal(msg: str) -> McpError:
    return McpError(ErrorData(code=_INTERNAL, message=msg))


def unauthorized(msg: str) -> McpError:
    return McpError(ErrorData(code=_UNAUTHORIZED, message=msg))
