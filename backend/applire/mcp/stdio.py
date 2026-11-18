# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stdio transport of the agent channel.

The same transport as the MCP SDK's ``stdio_server`` (one JSON-RPC message per
line on stdin, one per line on stdout), with one difference: a line that is not
a readable JSON-RPC message is answered here. A request whose id can be read
gets a JSON-RPC error reply (``-32602``) at once, so the agent is never left
waiting; the log line names the refusal and the error id, and the line itself
is a content line at DEBUG (ADR-087 cl. 9 amended 2026-11-18).
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from io import TextIOWrapper

import anyio
import mcp.types as types
from mcp.shared.message import SessionMessage

from applire.content_log import content_logger
from applire.internal_errors import new_error_id
from applire.redaction import scrub_secrets

logger = logging.getLogger("applire.mcp.stdio")
_clog = content_logger("applire.mcp.stdio")

#: What the agent reads for a message the server could not read.
UNREADABLE_MESSAGE = (
    "The request could not be read (for example a broken character in a text "
    "argument). Nothing was done; send it again with valid text."
)


def _request_id(line: str) -> int | str | None:
    try:
        raw = json.loads(line)
    except Exception:  # noqa: BLE001 — not JSON at all: no id to answer
        return None
    if not isinstance(raw, dict) or "method" not in raw:
        return None
    rid = raw.get("id")
    if isinstance(rid, bool) or not isinstance(rid, (int, str)):
        return None
    return rid


def refusal_for(line: str, exc: Exception) -> SessionMessage | None:
    """Log the refusal; return the error reply when the line names a request id."""
    error_id = new_error_id()
    rid = _request_id(line)
    logger.warning(
        "MCP message refused: not a readable JSON-RPC message [%s] [error_id=%s] [reply=%s]",
        type(exc).__name__, error_id, "sent" if rid is not None else "none (no request id)",
    )
    _clog.debug("MCP message refused [error_id=%s]: %s", error_id, scrub_secrets(f"{exc}\n{line}"))
    if rid is None:
        return None
    error = types.JSONRPCError(
        jsonrpc="2.0",
        id=rid,
        error=types.ErrorData(code=types.INVALID_PARAMS, message=UNREADABLE_MESSAGE, data={"error_id": error_id}),
    )
    return SessionMessage(types.JSONRPCMessage(error))


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None):
    """``mcp.server.stdio.stdio_server`` with the refusal above."""
    if not stdin:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    if not stdout:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader():
        try:
            async with read_stream_writer:
                async for line in stdin:
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:  # noqa: BLE001 — every unreadable line is answered here
                        reply = refusal_for(line, exc)
                        if reply is not None:
                            await write_stream.send(reply)
                        continue
                    await read_stream_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer():
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    data = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await stdout.write(data + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream


async def serve(fastmcp) -> None:
    """``FastMCP.run_stdio_async`` over :func:`stdio_server`."""
    from applire.services.log_consent_live import run_refresher

    server = fastmcp._mcp_server
    async with anyio.create_task_group() as tg:
        # A withdrawal recorded by the web process reaches this process's
        # running tool calls within seconds (services/log_consent_live.py).
        tg.start_soon(run_refresher)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
        tg.cancel_scope.cancel()
