"""MCP connectors: config, credentials, connections and tool adapters.

This is the only package that imports `mcp`, `mcp_types` or `httpx2`, and it
imports them lazily so startup does not pay for them.
"""
