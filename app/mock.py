from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Settings

TextCallback = Callable[[str], Awaitable[None]]


@dataclass
class _Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type}
        for key in ("text", "id", "name", "input"):
            value = getattr(self, key)
            if value is not None or not exclude_none:
                data[key] = value
        return data


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def model_dump(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class _Message:
    content: list[_Block] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


def _tool_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                for item in block.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
    return texts


def _find_id(messages: list[dict[str, Any]], key: str) -> str | None:
    for text in reversed(_tool_texts(messages)):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    return None


class MockKimi:
    """Deterministic agent used for UI tests (KOMPAS_CHAT_MOCK=1)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "model": "mock", "reply": "mock", "usage": {}}

    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback,
        should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        step = sum(1 for message in messages if message.get("role") == "assistant")
        doc = _find_id(messages, "document_id")
        sketch = _find_id(messages, "sketch_id")
        await asyncio.sleep(0.2)

        if step == 0:
            await on_text(
                "План: создам пластину 120×80×8 мм, добавлю четыре отверстия Ø6 "
                "и сохраню файл.\n\n"
            )
            return _Message(
                content=[
                    _Block(type="text", text="Подключаюсь к КОМПАС."),
                    _Block(type="tool_use", id="mock-connect", name="connect", input={"visible": True}),
                ],
                usage=_Usage(1200, 40),
                stop_reason="tool_use",
            )
        if step == 1:
            await on_text("Создаю новый документ-деталь.\n")
            return _Message(
                content=[
                    _Block(type="tool_use", id="mock-part", name="create_part", input={"name": "MockPlate"}),
                ],
                usage=_Usage(300, 30),
                stop_reason="tool_use",
            )
        if step == 2 and doc:
            await on_text("Строю эскиз: контур пластины и четыре отверстия.\n")
            curves: list[dict[str, Any]] = [
                {
                    "kind": "polyline",
                    "points": [[-60, -40], [60, -40], [60, 40], [-60, 40]],
                    "closed": True,
                }
            ]
            for cx, cy in ((-48, -28), (48, -28), (48, 28), (-48, 28)):
                curves.append({"kind": "circle", "center": [cx, cy], "radius": 3})
            return _Message(
                content=[
                    _Block(
                        type="tool_use",
                        id="mock-sketch",
                        name="create_sketch",
                        input={"document_id": doc, "spec": {"plane": "XY", "curves": curves}},
                    )
                ],
                usage=_Usage(800, 120),
                stop_reason="tool_use",
            )
        if step == 3 and doc and sketch:
            await on_text("Выдавливаю пластину на 8 мм.\n")
            return _Message(
                content=[
                    _Block(
                        type="tool_use",
                        id="mock-extrude",
                        name="extrude",
                        input={"document_id": doc, "spec": {"sketch_id": sketch, "depth": 8}},
                    )
                ],
                usage=_Usage(400, 40),
                stop_reason="tool_use",
            )
        if step == 4 and doc:
            await on_text("Проверяю габариты и объём.\n")
            return _Message(
                content=[
                    _Block(type="tool_use", id="mock-inspect", name="inspect_model", input={"document_id": doc})
                ],
                usage=_Usage(200, 30),
                stop_reason="tool_use",
            )
        if step == 5 and doc:
            save_dir = self.settings.ensure_save_dir()
            path = str(save_dir / f"mock_plate_{time.strftime('%H%M%S')}.m3d")
            await on_text("Сохраняю файл.\n")
            return _Message(
                content=[
                    _Block(
                        type="tool_use",
                        id="mock-save",
                        name="save_model",
                        input={"document_id": doc, "path": path, "overwrite": False},
                    )
                ],
                usage=_Usage(200, 25),
                stop_reason="tool_use",
            )
        if step == 6 and doc:
            await on_text("Делаю скриншот модели.\n")
            return _Message(
                content=[
                    _Block(type="tool_use", id="mock-preview", name="model_preview", input={"document_id": doc})
                ],
                usage=_Usage(200, 25),
                stop_reason="tool_use",
            )
        await on_text(
            "Готово. Пластина 120×80×8 мм с четырьмя отверстиями Ø6 построена, "
            "проверена и сохранена. Проверьте дерево построения и размеры в КОМПАСе."
        )
        return _Message(
            content=[_Block(type="text", text="Готово.")],
            usage=_Usage(300, 80),
            stop_reason="end_turn",
        )
