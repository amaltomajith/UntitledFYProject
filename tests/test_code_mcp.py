"""
Puppeteer-Code MCP server: spawn `python -m puppeteer_code mcp` over stdio
and drive it with the official MCP client.  Offline (mock provider).
"""

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

mcp = pytest.importorskip("mcp")
import anyio  # noqa: E402  (installed with mcp)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from code_fixtures import CALC_BUGGY, make_calc_repo


async def _session_call(fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "puppeteer_code", "mcp"], cwd=ROOT)
    # errlog must be a real file: pytest's captured stderr has no fileno on Windows.
    with open(os.devnull, "w") as errlog, anyio.fail_after(240):
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


def test_lists_tools():
    async def fn(s):
        return {t.name: t for t in (await s.list_tools()).tools}
    tools = asyncio.run(_session_call(fn))
    assert {"puppeteer_fix", "puppeteer_models"} <= set(tools)
    props = tools["puppeteer_fix"].inputSchema["properties"]
    assert {"task", "repo", "apply"} <= set(props)


def test_fix_runs_in_sandbox_and_does_not_touch_repo(tmp_path):
    repo = make_calc_repo(tmp_path)

    async def fn(s):
        res = await s.call_tool("puppeteer_fix", {"task": "mean() is wrong", "repo": str(repo),
                                                  "provider": "mock", "max_steps": 4})
        return res.content[0].text
    text = asyncio.run(_session_call(fn))
    assert "status:" in text and "tests: 1/3" in text
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC_BUGGY


def test_bad_repo_is_reported():
    async def fn(s):
        res = await s.call_tool("puppeteer_fix", {"task": "x", "repo": "Z:/definitely/missing", "provider": "mock"})
        return res.content[0].text
    assert "repository not found" in asyncio.run(_session_call(fn))
