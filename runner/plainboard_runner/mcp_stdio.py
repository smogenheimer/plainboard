"""The PlainBoard tools as a stdio MCP server, for providers that can only reach tools through MCP.

Started by the provider's CLI as ``python -m plainboard_runner.mcp_stdio``. The run's bridge spec is
read from the file named in ``PLAINBOARD_BRIDGE_FILE``, so the run token never shows up in a command
line.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from plainboard_protocol import ToolBridgeSpec

from .tool_bridge import ToolBridge

BRIDGE_FILE_ENV = "PLAINBOARD_BRIDGE_FILE"


def write_bridge_file(path: Path, spec: ToolBridgeSpec, workspace: Path, read_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"spec": spec.model_dump(mode="json"), "workspace": str(workspace), "read_only": read_only}, fh)


def build_server(bridge: ToolBridge, read_only: bool = False) -> Server:
    """``read_only``: a turn that only reads (toolset "read" or "none"), whose tools the app limits to reading ones.
    They say so (readOnlyHint): Codex refuses every other MCP tool in such a turn."""
    annotations = types.ToolAnnotations(read_only_hint=True) if read_only else None

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        tools = [types.Tool(name=t.name, description=t.description, input_schema=t.input_schema, annotations=annotations) for t in bridge.tools]
        return types.ListToolsResult(tools=tools)

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        result = await bridge.call(params.name, params.arguments or {})
        images = [types.ImageContent(type="image", data=i.data, mime_type=i.media_type) for i in result.images]
        return types.CallToolResult(content=[types.TextContent(type="text", text=result.text), *images], is_error=result.is_error)

    return Server("plainboard", on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    data = json.loads(Path(os.environ[BRIDGE_FILE_ENV]).read_text(encoding="utf-8"))
    bridge = ToolBridge(ToolBridgeSpec.model_validate(data["spec"]), Path(data["workspace"]))
    server = build_server(bridge, read_only=data.get("read_only", False))
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await bridge.aclose()


if __name__ == "__main__":
    asyncio.run(main())
