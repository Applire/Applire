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
    python -m applire.mcp
"""
import os
import sys


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport != "stdio":
        print(
            f"ERROR: MCP_TRANSPORT={transport!r} is not supported in the Community Edition. "
            "Only 'stdio' is available.",
            file=sys.stderr,
        )
        sys.exit(1)

    from applire.mcp.server import mcp, warn_if_base_url_unset

    warn_if_base_url_unset()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
