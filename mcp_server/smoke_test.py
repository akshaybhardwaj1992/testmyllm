"""Stdio smoke test: start `testmyllm-mcp`, list its tools, call one. Usage: python smoke_test.py"""
import asyncio
import os

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> None:
    params = StdioServerParameters(command="testmyllm-mcp", env={**os.environ})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print("server:", init.server_info.name, init.server_info.version)
        tools = await session.list_tools()
        for t in tools.tools:
            print(" tool:", t.name, "-", (t.description or "")[:70])
        res = await session.call_tool("list_projects", {})
        print("list_projects ->", (res.content[0].text if res.content else res)[:300])


asyncio.run(main())
