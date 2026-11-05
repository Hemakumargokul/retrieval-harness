import asyncio
import sys

import pytest

mcp = pytest.importorskip("mcp")


def test_mcp_server_lists_and_calls_tool(repo):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "rh.cli", "mcp"],
                                       env={"RH_REPO": str(repo), "PATH": "/usr/bin:/bin"})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                res = await s.call_tool("retrieve_context",
                                        {"task": "why does InvoiceTotaler.compute_total return a wrong total"})
                return [t.name for t in tools.tools], res.content[0].text

    names, text = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["retrieve_context"]
    assert "pkg/billing.py" in text
