from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from datetime import date
from typing import Any, AsyncIterator

from .config import Settings
from .kimi import KimiClient, KimiError
from .mcp_bridge import MCPBridge
from .prompts import system_prompt
from .session import ChatSession

TOOL_TITLES = {
    "connect": "Подключение к КОМПАС",
    "attach_active_model": "Текущая модель",
    "create_part": "Новый документ",
    "open_model": "Открытие модели",
    "create_sketch": "Эскиз",
    "extrude": "Выдавливание",
    "revolve": "Вращение",
    "inspect_model": "Проверка модели",
    "save_model": "Сохранение",
    "export_model": "Экспорт",
    "model_preview": "Скриншот",
    "api_describe": "Справка API",
    "api_constants": "Константы API",
    "api_invoke": "COM-вызов",
    "sdk_search": "Поиск в SDK",
    "sdk_read": "Чтение SDK",
    "list_variables": "Переменные",
    "set_variable": "Изменение переменной",
}

MAX_TOOL_TEXT = 24_000
MAX_IMAGES = 2


def _tool_title(name: str) -> str:
    return TOOL_TITLES.get(name, name)


def _shrink_image(data_b64: str, mime: str) -> tuple[str, str] | None:
    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return None
    if len(raw) <= 700_000:
        return data_b64, mime
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(raw))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((1500, 1500))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=86, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        return data_b64, mime


def _content_blocks(result: Any) -> tuple[list[dict[str, Any]], list[str], str]:
    blocks: list[dict[str, Any]] = []
    images: list[str] = []
    texts: list[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            text = getattr(item, "text", "") or ""
            texts.append(text)
            if len(text) > 6000:
                text = text[:5500] + "\n… (сокращено)"
            blocks.append({"type": "text", "text": text})
        elif kind == "image":
            data = getattr(item, "data", "")
            mime = getattr(item, "mimeType", None) or "image/png"
            prepared = _shrink_image(data, mime)
            if prepared is None:
                continue
            data, mime = prepared
            if len(images) < MAX_IMAGES:
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": data},
                    }
                )
                images.append(f"data:{mime};base64,{data}")
        elif kind == "resource":
            resource = getattr(item, "resource", None)
            text = getattr(resource, "text", None)
            if text:
                texts.append(str(text))
                blocks.append({"type": "text", "text": str(text)[:MAX_TOOL_TEXT]})
            else:
                uri = getattr(resource, "uri", "")
                blocks.append({"type": "text", "text": f"resource: {uri}"})
        elif kind == "resource_link":
            uri = getattr(item, "uri", "")
            name = getattr(item, "name", "") or "resource"
            texts.append(f"{name}: {uri}")
            blocks.append({"type": "text", "text": f"{name}: {uri}"})
        else:
            text = str(item)
            texts.append(text)
            blocks.append({"type": "text", "text": text[:MAX_TOOL_TEXT]})
    if not blocks:
        blocks = [{"type": "text", "text": "Готово."}]
    joined = "\n".join(texts).strip()
    if len(joined) > MAX_TOOL_TEXT:
        joined = joined[:MAX_TOOL_TEXT] + "\n… (сокращено)"
    return blocks, images, joined or "Готово."


class Agent:
    def __init__(self, settings: Settings, bridge: MCPBridge, kimi: KimiClient) -> None:
        self.settings = settings
        self.bridge = bridge
        self.kimi = kimi

    async def events(self, session: ChatSession, user_text: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        task = asyncio.create_task(self._run(session, user_text, queue), name="agent-turn")
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run(
        self,
        session: ChatSession,
        user_text: str,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        try:
            session.turn += 1
            session.add_user(user_text)
            session.messages.append(
                {"role": "user", "content": [{"type": "text", "text": user_text}]}
            )
            await emit({"type": "turn_start", "turn": session.turn})

            tools = self.bridge.anthropic_tools()
            if not tools:
                await self.bridge.wait_ready(25.0)
                tools = self.bridge.anthropic_tools()
            if not tools:
                await emit(
                    {
                        "type": "warning",
                        "message": "MCP-инструменты КОМПАС пока недоступны: "
                        + (self.bridge.error or "сервер ещё запускается"),
                    }
                )

            save_dir = self.settings.ensure_save_dir()
            system = system_prompt(
                self.settings.model, date.today().isoformat(), str(save_dir)
            )
            steps = max(1, int(self.settings.max_steps))

            for step in range(steps):
                if session.stop_event.is_set():
                    break

                async def on_text(chunk: str) -> None:
                    if session.stop_event.is_set():
                        return
                    session.append_assistant(chunk)
                    await emit({"type": "text", "text": chunk})

                final = await self.kimi.stream_turn(
                    system=system,
                    messages=session.messages,
                    tools=tools,
                    on_text=on_text,
                    should_stop=session.stop_event.is_set,
                )

                content = [
                    block.model_dump(exclude_none=True)
                    for block in getattr(final, "content", []) or []
                    if getattr(block, "type", "") in ("text", "tool_use")
                ]
                session.messages.append({"role": "assistant", "content": content})

                usage = {}
                if getattr(final, "usage", None):
                    try:
                        usage = final.usage.model_dump()
                    except Exception:
                        usage = {}
                await emit(
                    {
                        "type": "assistant_end",
                        "usage": usage,
                        "stop_reason": getattr(final, "stop_reason", None),
                    }
                )

                if session.stop_event.is_set():
                    break

                tool_uses = [
                    block
                    for block in getattr(final, "content", []) or []
                    if getattr(block, "type", "") == "tool_use"
                ]
                if not tool_uses:
                    break

                results: list[dict[str, Any]] = []
                for block in tool_uses:
                    tool_id = getattr(block, "id", "") or f"tool_{step}"
                    safe_name = getattr(block, "name", "")
                    name = self.bridge.resolve_name(safe_name)
                    arguments = getattr(block, "input", None) or {}
                    title = _tool_title(name)
                    session.add_tool(tool_id, name, title, arguments)
                    await emit(
                        {
                            "type": "tool_start",
                            "id": tool_id,
                            "name": name,
                            "title": title,
                            "input": arguments,
                        }
                    )
                    started = time.time()
                    ok = True
                    try:
                        result = await self.bridge.call(name, arguments)
                        ok = not bool(getattr(result, "isError", False))
                        blocks, images, card_text = _content_blocks(result)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        ok = False
                        blocks = [{"type": "text", "text": f"Ошибка инструмента: {exc}"}]
                        images = []
                        card_text = f"Ошибка инструмента: {exc}"
                    duration = int((time.time() - started) * 1000)
                    session.finish_tool(
                        tool_id,
                        ok=ok,
                        text=card_text,
                        images=images,
                        duration_ms=duration,
                    )
                    await emit(
                        {
                            "type": "tool_result",
                            "id": tool_id,
                            "ok": ok,
                            "text": card_text[:6000],
                            "images": images,
                            "duration_ms": duration,
                        }
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": blocks,
                            "is_error": not ok,
                        }
                    )

                session.messages.append({"role": "user", "content": results})
                if session.stop_event.is_set():
                    break

            session.close_assistant()
            await emit({"type": "done", "stopped": session.stop_event.is_set()})
        except KimiError as exc:
            session.add_error(str(exc))
            await emit({"type": "error", "message": str(exc)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            session.add_error(message)
            await emit({"type": "error", "message": message})
        finally:
            session.busy = False
            await queue.put(None)
