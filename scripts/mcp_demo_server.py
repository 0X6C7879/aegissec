from __future__ import annotations

import argparse
import asyncio

from mcp.server.fastmcp import FastMCP


def build_server(*, name: str, host: str, port: int) -> FastMCP:
    server = FastMCP(
        name,
        instructions="Minimal MCP demo server for AegisSec verification.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
    )

    @server.tool(name="sum_numbers", description="Add two integers together")
    def sum_numbers(a: int, b: int) -> int:
        return a + b

    @server.resource(
        "demo://status",
        name="demo_status",
        title="Demo Status",
        description="Static demo resource for MCP capability discovery",
    )
    def demo_status() -> str:
        return "AegisSec MCP demo server is healthy."

    @server.prompt(
        name="demo_prompt",
        title="Demo Prompt",
        description="Simple prompt used to verify prompt discovery",
    )
    def demo_prompt(topic: str = "AegisSec") -> str:
        return f"Summarize the current status of {topic}."

    return server


async def run(mode: str, host: str, port: int) -> None:
    server = build_server(
        name=f"AegisSec Demo {mode.upper()} Server",
        host=host,
        port=port,
    )
    if mode == "stdio":
        await server.run_stdio_async()
        return
    await server.run_streamable_http_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a minimal MCP demo server.")
    parser.add_argument("mode", choices=("stdio", "http"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(arguments.mode, arguments.host, arguments.port))
