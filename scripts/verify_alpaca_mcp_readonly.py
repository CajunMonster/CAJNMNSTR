"""Run one sanitized, authenticated, read-only proof against Alpaca MCP v2."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VERSION_PIN = "2.3.1"
TOOLSETS = ("assets", "stock-data", "options-data", "news")
PROOF_TOOL = "get_clock"
FORBIDDEN_TOOL_PREFIXES = (
    "add_",
    "cancel_",
    "close_",
    "create_",
    "delete_",
    "do_not_exercise_",
    "exercise_",
    "place_",
    "remove_",
    "replace_",
    "update_",
)
FORBIDDEN_ACCOUNT_TOOLS = {
    "get_account_info",
    "get_account_config",
    "get_all_positions",
    "get_open_position",
    "get_orders",
}


async def verify() -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    launcher = project_root / "launcher" / "Start-Alpaca-Mcp-Readonly.ps1"
    powershell = (
        Path(os.environ["SYSTEMROOT"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    parameters = StdioServerParameters(
        command=str(powershell),
        args=[
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
        ],
        cwd=project_root,
    )

    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                tool_names = {tool.name for tool in listed.tools}
                forbidden = sorted(
                    name
                    for name in tool_names
                    if name.startswith(FORBIDDEN_TOOL_PREFIXES)
                    or name in FORBIDDEN_ACCOUNT_TOOLS
                )
                if forbidden:
                    raise RuntimeError("The restricted server exposed a forbidden capability.")
                if PROOF_TOOL not in tool_names:
                    raise RuntimeError("The expected read-only market-clock tool is unavailable.")

                result = await session.call_tool(PROOF_TOOL, arguments={})
                is_error = bool(
                    getattr(result, "isError", False) or getattr(result, "is_error", False)
                )
                if is_error:
                    raise RuntimeError("The authenticated read-only market-clock call failed.")

    return {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "server": "alpaca-mcp-server",
        "version_pin": VERSION_PIN,
        "registration": "alpaca_market_readonly",
        "transport": "stdio",
        "paper_only": True,
        "toolsets": list(TOOLSETS),
        "exposed_tool_count": len(tool_names),
        "broker_write_tools_exposed": False,
        "proof_tool": PROOF_TOOL,
        "proof_succeeded": True,
        "response_content_blocks": len(result.content),
        "credentials_recorded": False,
        "broker_mutation_performed": False,
    }


def main() -> None:
    print(json.dumps(asyncio.run(verify()), indent=2))


if __name__ == "__main__":
    main()
