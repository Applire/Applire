FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# US371: the MCP Registry verifies that this image belongs to the server entry
# by reading this label; it must equal `name` in the repository's server.json
# (pinned by tests/unit/test_mcp_registry_listing.py).
LABEL io.modelcontextprotocol.server.name="io.github.Applire/applire"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "applire.main:app", "--host", "0.0.0.0", "--port", "8000"]
