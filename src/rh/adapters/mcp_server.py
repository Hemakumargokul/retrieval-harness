"""MCP stdio server exposing one tool, `retrieve_context`. Needs the optional `mcp` extra:
`uv sync --extra mcp`. The agent decides when to call it (unlike a hook, it is not automatic)."""
from __future__ import annotations

import os
from pathlib import Path

from rh.config import load_config
from rh.render import to_markdown
from rh.retrieve import retrieve


def retrieve_context_text(task: str, repo: str | None = None, budget_tokens: int | None = None,
                          profile: str | None = None) -> str:
    root = Path(repo or os.environ.get("RH_REPO") or os.getcwd())
    cfg = load_config(root, profile, budget_tokens=budget_tokens)
    pkg = retrieve(root, task, cfg)
    return to_markdown(pkg, cfg.mermaid_min_edges) or "No relevant context found."


def run(profile: str | None = None) -> int:
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError:
            import sys

            print("rh mcp needs the 'mcp' extra: uv sync --extra mcp", file=sys.stderr)
            return 1

    server = Server("retrieval-harness")

    @server.tool()
    def retrieve_context(task: str, budget_tokens: int | None = None) -> str:
        """Find the code, tests and callers relevant to a coding task in the current repository.
        Call this before editing. Returns Markdown with path:start-end excerpts."""
        return retrieve_context_text(task, budget_tokens=budget_tokens, profile=profile)

    server.run()
    return 0
