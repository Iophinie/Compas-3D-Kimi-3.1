from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .config import ROOT, Settings, load_settings
from .kimi import KimiClient, KimiError
from .kimi_oauth import (
    OAuthError,
    login as oauth_login,
    logout as oauth_logout,
    poll_device_flow,
    start_device_flow,
    status as oauth_status,
)
from .mcp_bridge import MCPBridge
from .mock import MockKimi
from .session import ChatSession

WEB_DIR = ROOT / "web"


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class SettingsPatch(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=65_536)
    max_steps: int | None = Field(default=None, ge=1, le=80)
    tool_mode: str | None = None
    panel_width: int | None = Field(default=None, ge=320, le=900)
    startup_connect: bool | None = None
    mcp_command: str | None = None
    mcp_args: list[str] | None = None
    mcp_cwd: str | None = None
    save_dir: str | None = None


class MCPCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bridge = MCPBridge(settings)
        kimi = (
            MockKimi(settings)
            if os.environ.get("KOMPAS_CHAT_MOCK") == "1"
            else KimiClient(settings)
        )
        session = ChatSession()
        agent = Agent(settings, bridge, kimi)
        app.state.settings = settings
        app.state.bridge = bridge
        app.state.kimi = kimi
        app.state.session = session
        app.state.agent = agent
        app.state.oauth_flow = {}
        bridge.start_manager()
        try:
            yield
        finally:
            await bridge.stop()

    app = FastAPI(title="Kimi for KOMPAS-3D", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "app": "kompas-chat"}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> dict[str, Any]:
        state = request.app.state
        session: ChatSession = state.session
        bridge: MCPBridge = state.bridge
        return {
            "settings": state.settings.public(),
            "mcp": bridge.status(),
            "busy": session.busy,
            "history": session.display,
        }

    @app.post("/api/settings")
    async def update_settings(patch: SettingsPatch, request: Request) -> dict[str, Any]:
        state = request.app.state
        settings: Settings = state.settings
        data = patch.model_dump(exclude_none=True)
        restart_mcp = False
        for key in ("mcp_command", "mcp_args", "mcp_cwd"):
            if key in data and getattr(settings, key) != data[key]:
                restart_mcp = True
        if "api_key" in data:
            value = data["api_key"].strip()
            if value and value != "***":
                settings.api_key = value
                settings.use_kimi_code = False
                settings.auth_token = ""
            data.pop("api_key", None)
        for key, value in data.items():
            setattr(settings, key, value)
        if settings.tool_mode not in ("core", "all"):
            settings.tool_mode = "core"
        settings.save()
        if restart_mcp:
            state.bridge.request_restart()
        return {"ok": True, "settings": settings.public()}

    @app.post("/api/test-key")
    async def test_key(request: Request) -> dict[str, Any]:
        state = request.app.state
        try:
            return await state.kimi.test()
        except KimiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"{type(exc).__name__}: {exc}"
            ) from exc

    @app.post("/api/oauth/login")
    async def oauth_login_route(request: Request) -> dict[str, Any]:
        state = request.app.state
        settings: Settings = state.settings
        try:
            info = await oauth_login(settings)
        except OAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "oauth": info, "settings": settings.public()}

    @app.post("/api/oauth/start")
    async def oauth_start_route(request: Request) -> dict[str, Any]:
        state = request.app.state
        try:
            flow = await start_device_flow()
        except OAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        state.oauth_flow = {
            "device_code": flow["device_code"],
            "interval": flow["interval"],
            "expires_at": time.time() + flow["expires_in"],
        }
        return {
            "ok": True,
            "user_code": flow["user_code"],
            "verification_url": flow["verification_url"],
            "interval": flow["interval"],
            "expires_in": flow["expires_in"],
        }

    @app.post("/api/oauth/poll")
    async def oauth_poll_route(request: Request) -> dict[str, Any]:
        state = request.app.state
        flow = state.oauth_flow or {}
        if not flow.get("device_code"):
            raise HTTPException(status_code=400, detail="Вход не начат")
        if time.time() > float(flow.get("expires_at", 0)):
            state.oauth_flow = {}
            return {"status": "expired"}
        try:
            result = await poll_device_flow(state.settings, flow["device_code"])
        except OAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.get("status") == "success":
            state.oauth_flow = {}
        return result

    @app.post("/api/oauth/cancel")
    async def oauth_cancel_route(request: Request) -> dict[str, Any]:
        request.app.state.oauth_flow = {}
        return {"ok": True}

    @app.post("/api/oauth/logout")
    async def oauth_logout_route(request: Request) -> dict[str, Any]:
        state = request.app.state
        oauth_logout(state.settings)
        return {"ok": True, "settings": state.settings.public()}

    @app.get("/api/oauth/status")
    async def oauth_status_route(request: Request) -> dict[str, Any]:
        return oauth_status(request.app.state.settings)

    @app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        state = request.app.state
        session: ChatSession = state.session
        if session.busy:
            raise HTTPException(status_code=409, detail="Агент уже работает")
        if not state.settings.has_credentials():
            raise HTTPException(
                status_code=400, detail="Сначала задайте ключ Kimi в настройках"
            )
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Пустой запрос")
        session.busy = True
        session.stop_event = asyncio.Event()

        async def stream() -> Any:
            try:
                async for event in state.agent.events(session, text):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                yield json.dumps(
                    {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                ) + "\n"
            finally:
                session.busy = False

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/stop")
    async def stop(request: Request) -> dict[str, Any]:
        state = request.app.state
        state.session.stop_event.set()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset(request: Request) -> dict[str, Any]:
        state = request.app.state
        if state.session.busy:
            raise HTTPException(status_code=409, detail="Агент работает")
        state.session.reset()
        return {"ok": True}

    @app.get("/api/history")
    async def history(request: Request) -> dict[str, Any]:
        state = request.app.state
        return {"history": state.session.display, "busy": state.session.busy}

    @app.post("/api/mcp/restart")
    async def mcp_restart(request: Request) -> dict[str, Any]:
        state = request.app.state
        state.bridge.request_restart()
        return {"ok": True}

    @app.post("/api/mcp/call")
    async def mcp_call(payload: MCPCallRequest, request: Request) -> JSONResponse:
        state = request.app.state
        try:
            result = await state.bridge.call(payload.name, payload.arguments)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        content: list[dict[str, Any]] = []
        for item in getattr(result, "content", None) or []:
            kind = getattr(item, "type", "")
            if kind == "text":
                content.append({"type": "text", "text": item.text})
            elif kind == "image":
                content.append(
                    {
                        "type": "image",
                        "mime": getattr(item, "mimeType", "image/png"),
                        "data": getattr(item, "data", ""),
                    }
                )
        return JSONResponse(
            {"ok": not bool(getattr(result, "isError", False)), "content": content}
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
