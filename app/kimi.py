from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

import anthropic

try:  # the Anthropic SDK ships its own httpx fork
    import httpx2 as httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx  # type: ignore

from .config import Settings
from .kimi_oauth import OAuthError, ensure_access_token

TextCallback = Callable[[str], Awaitable[None]]


class KimiError(RuntimeError):
    pass


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Kimi отклонил ключ или сессию (401). Проверьте ключ из Kimi Code Console "
            "или войдите через Kimi Code заново."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "У ключа нет доступа к выбранной модели. Проверьте тариф Kimi Code."
    if isinstance(exc, anthropic.RateLimitError):
        return "Лимит запросов Kimi исчерпан. Подождите или проверьте квоту в консоли."
    if isinstance(exc, anthropic.APIStatusError):
        detail = ""
        try:
            payload = exc.response.json()
            detail = str(payload.get("error", {}).get("message") or payload)[:600]
        except Exception:
            detail = (exc.response.text or "")[:600]
        return f"Kimi вернул ошибку {exc.status_code}: {detail or exc.message}"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "Нет связи с api.kimi.ai. Проверьте интернет, VPN и прокси."
    if isinstance(exc, httpx.ReadTimeout):
        return "Kimi не ответил вовремя. Попробуйте ещё раз или смените модель."
    return f"{type(exc).__name__}: {exc}"


class KimiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _client(self) -> anthropic.AsyncAnthropic:
        if self.settings.use_kimi_code:
            try:
                token = await ensure_access_token(self.settings)
            except OAuthError as exc:
                raise KimiError(str(exc)) from exc
            return anthropic.AsyncAnthropic(
                auth_token=token,
                base_url=self.settings.base_url.rstrip("/") + "/",
                timeout=300.0,
                max_retries=1,
            )
        auth_token = self.settings.auth_token or os.environ.get("KIMI_AUTH_TOKEN", "")
        if auth_token:
            return anthropic.AsyncAnthropic(
                auth_token=auth_token,
                base_url=self.settings.base_url.rstrip("/") + "/",
                timeout=300.0,
                max_retries=1,
            )
        if not self.settings.api_key:
            raise KimiError(
                "Не задан ключ Kimi. Войдите через Kimi Code в настройках панели "
                "или вставьте ключ из Kimi Code Console."
            )
        return anthropic.AsyncAnthropic(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url.rstrip("/") + "/",
            timeout=300.0,
            max_retries=1,
        )

    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback,
        should_stop: Callable[[], bool] | None = None,
    ) -> Any:
        client = await self._client()
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": max(256, int(self.settings.max_tokens)),
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if should_stop and should_stop():
                        break
                    if event.type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None and getattr(delta, "type", "") == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                await on_text(text)
                return await stream.get_final_message()
        except KimiError:
            raise
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise KimiError(_friendly_error(exc)) from exc

    async def test(self) -> dict[str, Any]:
        client = await self._client()
        try:
            message = await client.messages.create(
                model=self.settings.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "ping"}],
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise KimiError(_friendly_error(exc)) from exc
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        return {
            "ok": True,
            "model": getattr(message, "model", self.settings.model),
            "reply": text[:200],
            "usage": getattr(message, "usage", None).model_dump()
            if getattr(message, "usage", None)
            else {},
        }
