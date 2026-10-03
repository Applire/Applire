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
Entry point: python -m applire.mcp

Starts the Applire MCP server.  Only stdio transport is supported in the
Community Edition.  SSE transport is reserved for the Cloud Edition.

Usage:
    cd backend
    APPLIRE_AGENT_TOKEN=apl_… python -m applire.mcp

Without a valid agent token (Settings → Tokens in Applire) the process prints a
message to stderr and exits 1 (ADR-091 cl. 17, S-5).
"""
import asyncio
import os
import sys


async def _startup() -> None:
    """Fences, instance secret, agent identity — all before ``mcp.run`` (ADR-091 cl. 3, 17, 18).

    Runs in its own event loop; the pooled connections it opened are disposed of
    at the end so ``mcp.run``'s loop starts clean.
    """
    from applire.auth import harness
    from applire.auth.links import load_instance_secret
    from applire.config import settings
    from applire.db.session import engine
    from applire.mcp import identity
    from applire.mcp.deps import get_db

    try:
        async with get_db() as db:
            # ADR-091 cl. 3: the harness fences hold for this process too.
            await harness.enforce_at_startup(db)
            # ADR-091 cl. 18: signed document links need the instance secret.
            await load_instance_secret(db)
            await identity.establish(db, settings.applire_agent_token)
    finally:
        await engine.dispose()


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport != "stdio":
        print(
            f"ERROR: MCP_TRANSPORT={transport!r} is not supported in the Community Edition. "
            "Only 'stdio' is available.",
            file=sys.stderr,
        )
        sys.exit(1)

    from applire.auth.harness import HarnessRefused, log_refusal
    from applire.mcp.identity import AgentStartRefused

    try:
        asyncio.run(_startup())
    except AgentStartRefused as exc:
        # S-5: no valid agent token → refuse to start (stdout is the protocol channel).
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except HarnessRefused as exc:
        log_refusal(exc)
        print(f"Applire MCP: AUTH_HARNESS refused: {exc.reason}", file=sys.stderr)
        sys.exit(1)

    from applire.mcp.server import mcp, warn_if_base_url_unset

    warn_if_base_url_unset()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
