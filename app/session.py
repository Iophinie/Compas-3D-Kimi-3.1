from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatSession:
    messages: list[dict[str, Any]] = field(default_factory=list)
    display: list[dict[str, Any]] = field(default_factory=list)
    busy: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    turn: int = 0

    def reset(self) -> None:
        self.messages.clear()
        self.display.clear()
        self.busy = False
        self.stop_event = asyncio.Event()
        self.turn = 0

    def add_user(self, text: str) -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:12],
            "role": "user",
            "text": text,
            "ts": time.time(),
        }
        self.display.append(item)
        return item

    def append_assistant(self, text: str) -> dict[str, Any]:
        last = self.display[-1] if self.display else None
        if last and last.get("role") == "assistant" and not last.get("closed"):
            last["text"] += text
            return last
        item = {
            "id": uuid.uuid4().hex[:12],
            "role": "assistant",
            "text": text,
            "ts": time.time(),
        }
        self.display.append(item)
        return item

    def close_assistant(self) -> None:
        if self.display and self.display[-1].get("role") == "assistant":
            self.display[-1]["closed"] = True

    def add_tool(
        self, tool_id: str, name: str, title: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.close_assistant()
        item = {
            "id": tool_id,
            "role": "tool",
            "name": name,
            "title": title,
            "input": arguments,
            "status": "running",
            "text": "",
            "images": [],
            "ts": time.time(),
        }
        self.display.append(item)
        return item

    def finish_tool(
        self,
        tool_id: str,
        *,
        ok: bool,
        text: str,
        images: list[str],
        duration_ms: int,
    ) -> dict[str, Any] | None:
        for item in reversed(self.display):
            if item.get("id") == tool_id:
                item["status"] = "ok" if ok else "error"
                item["text"] = text[:6000]
                item["images"] = images[:4]
                item["duration_ms"] = duration_ms
                return item
        return None

    def add_error(self, text: str) -> dict[str, Any]:
        self.close_assistant()
        item = {
            "id": uuid.uuid4().hex[:12],
            "role": "error",
            "text": text,
            "ts": time.time(),
        }
        self.display.append(item)
        return item
