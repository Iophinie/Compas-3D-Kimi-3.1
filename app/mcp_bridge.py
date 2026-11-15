from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT, Settings, home_dir

CORE_TOOLS = {
    "connect",
    "attach_active_model",
    "create_part",
    "open_model",
    "create_sketch",
    "extrude",
    "revolve",
    "inspect_model",
    "save_model",
    "export_model",
    "model_preview",
    "api_describe",
    "api_constants",
    "api_invoke",
    "sdk_search",
    "sdk_read",
    "list_variables",
    "set_variable",
}


def _safe_tool_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return cleaned[:64] or "tool"


def _runtime_temp() -> Path:
    candidates = (
        home_dir() / "tmp",
        Path(tempfile.gettempdir()) / "KompasChat",
        ROOT / ".tmp",
    )
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / "_.probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


class MCPBridge:
    """Owns one stdio MCP server process and exposes its tools."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools: list[Any] = []
        self.error: str = ""
        self.started_at: float = 0.0
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._call_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._want_running = True
        self._restart = False
        self._manager_task: asyncio.Task | None = None
        self._name_map: dict[str, str] = {}
        self._stopping = False

    # ------------------------------------------------------------------ lifecycle
    def start_manager(self) -> None:
        if self._manager_task is None or self._manager_task.done():
            self._manager_task = asyncio.create_task(self._manager(), name="mcp-manager")

    async def stop(self) -> None:
        self._stopping = True
        self._want_running = False
        if self._manager_task:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except (asyncio.CancelledError, Exception):
                pass
        async with self._state_lock:
            await self._stop_internal()

    def request_restart(self) -> None:
        self._restart = True

    async def _manager(self) -> None:
        while not self._stopping:
            try:
                if self._want_running and (self._session is None or self._restart):
                    self._restart = False
                    async with self._state_lock:
                        await self._stop_internal()
                        await self._start_internal()
                elif not self._want_running and self._session is not None:
                    async with self._state_lock:
                        await self._stop_internal()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the panel alive, surface the error
                self.error = f"{type(exc).__name__}: {exc}"
                async with self._state_lock:
                    await self._stop_internal()
                await asyncio.sleep(3.0)
            await asyncio.sleep(0.4)

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        tmp = _runtime_temp()
        # pywin32 stores generated COM wrappers in %TEMP%. Keeping that inside our
        # own directory makes the packaged panel independent from user temp ACLs.
        env["TEMP"] = str(tmp)
        env["TMP"] = str(tmp)
        env["TMPDIR"] = str(tmp)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    async def _start_internal(self) -> None:
        command = self.settings.mcp_command
        if not command or not Path(command).exists():
            raise FileNotFoundError(
                f"Не найден Python MCP-сервера: {command}. Запустите install.ps1."
            )
        cwd = self.settings.mcp_cwd or None
        if cwd and not Path(cwd).exists():
            raise FileNotFoundError(f"Не найдена папка MCP-сервера: {cwd}")
        params = StdioServerParameters(
            command=command,
            args=list(self.settings.mcp_args),
            env=self._child_env(),
            cwd=cwd,
        )
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self.tools = list(listed.tools)
        self.error = ""
        self.started_at = time.time()
        self._rebuild_name_map()

    async def _stop_internal(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        self.tools = []
        self._name_map = {}
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------ tools
    def _rebuild_name_map(self) -> None:
        mapping: dict[str, str] = {}
        used: set[str] = set()
        for tool in self.tools:
            safe = _safe_tool_name(tool.name)
            base = safe
            index = 2
            while safe in used:
                suffix = f"_{index}"
                safe = base[: 64 - len(suffix)] + suffix
                index += 1
            used.add(safe)
            mapping[safe] = tool.name
        self._name_map = mapping

    def selected_tools(self) -> list[Any]:
        if self.settings.tool_mode != "core":
            return list(self.tools)
        selected = [t for t in self.tools if t.name in CORE_TOOLS]
        return selected or list(self.tools)

    def anthropic_tools(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool in self.selected_tools():
            safe = next((k for k, v in self._name_map.items() if v == tool.name), tool.name)
            schema = tool.inputSchema or {"type": "object", "properties": {}}
            description = (tool.description or "").strip()
            if len(description) > 3500:
                description = description[:3500] + "…"
            result.append(
                {
                    "name": safe,
                    "description": description or tool.name,
                    "input_schema": schema,
                }
            )
        return result

    def resolve_name(self, name: str) -> str:
        return self._name_map.get(name, name)

    async def wait_ready(self, timeout: float = 25.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._session is not None:
                return True
            await asyncio.sleep(0.25)
        return self._session is not None

    async def call(self, name: str, arguments: dict[str, Any] | None) -> Any:
        async with self._call_lock:
            session = self._session
            if session is None:
                raise RuntimeError(
                    "MCP-сервер КОМПАС не подключён"
                    + (f": {self.error}" if self.error else "")
                )
            return await asyncio.wait_for(
                session.call_tool(name, arguments or {}), timeout=300.0
            )

    def status(self) -> dict[str, Any]:
        return {
            "running": self._session is not None,
            "error": self.error,
            "tools_total": len(self.tools),
            "tools_selected": len(self.selected_tools()),
            "uptime_sec": int(time.time() - self.started_at) if self.started_at else 0,
            "command": self.settings.mcp_command,
        }
