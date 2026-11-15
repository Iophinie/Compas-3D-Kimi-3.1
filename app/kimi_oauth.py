from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings

CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
TOKEN_URL = "https://auth.kimi.ai/api/oauth/token"
DEVICE_URL = "https://auth.kimi.ai/api/oauth/device_authorization"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)


class OAuthError(RuntimeError):
    pass


def kimi_code_home() -> Path:
    override = os.environ.get("KIMI_CODE_HOME")
    return Path(override) if override else Path.home() / ".kimi-code"


def read_local_credentials() -> dict[str, Any] | None:
    folder = kimi_code_home() / "credentials"
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("access_token") and data.get("refresh_token"):
            return data
    return None


def _post_form(
    url: str, payload: dict[str, str], allow_http_error: bool = False
) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if allow_http_error:
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        raise OAuthError(
            f"Kimi Code отклонил запрос авторизации ({exc.code}): {detail[:300]}"
        ) from exc
    except Exception as exc:
        raise OAuthError(f"Нет связи с auth.kimi.ai: {exc}") from exc


async def _refresh(settings: Settings, refresh_token: str) -> str:
    payload = await asyncio.to_thread(
        _post_form,
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    access = payload.get("access_token")
    if not access:
        raise OAuthError("Kimi Code не вернул access_token при обновлении сессии")
    settings.oauth_access_token = access
    settings.oauth_refresh_token = payload.get("refresh_token") or refresh_token
    settings.oauth_expires_at = int(time.time()) + int(payload.get("expires_in", 900))
    settings.save()
    return access


async def ensure_access_token(settings: Settings, force: bool = False) -> str:
    if not settings.use_kimi_code:
        raise OAuthError("Вход через Kimi Code не включён")
    now = int(time.time())
    if not force and settings.oauth_access_token and settings.oauth_expires_at > now + 120:
        return settings.oauth_access_token

    refresh_token = settings.oauth_refresh_token
    if not refresh_token:
        local = read_local_credentials()
        if local:
            settings.oauth_access_token = local["access_token"]
            settings.oauth_refresh_token = local["refresh_token"]
            settings.oauth_expires_at = int(local.get("expires_at", 0))
            if settings.oauth_expires_at > now + 120:
                settings.save()
                return settings.oauth_access_token
            refresh_token = settings.oauth_refresh_token
    if not refresh_token:
        raise OAuthError(
            "Нет сохранённой сессии Kimi Code. Откройте Kimi Code, войдите и нажмите "
            "«Войти через Kimi Code» ещё раз."
        )
    try:
        return await _refresh(settings, refresh_token)
    except OAuthError:
        local = read_local_credentials()
        if local and local.get("refresh_token") != refresh_token:
            settings.oauth_access_token = local["access_token"]
            settings.oauth_refresh_token = local["refresh_token"]
            settings.oauth_expires_at = int(local.get("expires_at", 0))
            if settings.oauth_expires_at > now + 120:
                settings.save()
                return settings.oauth_access_token
            return await _refresh(settings, settings.oauth_refresh_token)
        raise


async def start_device_flow() -> dict[str, Any]:
    data = await asyncio.to_thread(
        _post_form, DEVICE_URL, {"client_id": CLIENT_ID}
    )
    code = data.get("user_code")
    device_code = data.get("device_code")
    if not code or not device_code:
        raise OAuthError("Kimi Code не выдал код устройства. Попробуйте позже.")
    url = data.get("verification_uri_complete") or data.get("verification_uri")
    return {
        "user_code": code,
        "device_code": device_code,
        "verification_url": url,
        "expires_in": int(data.get("expires_in", 600)),
        "interval": max(2, int(data.get("interval", 5))),
    }


async def poll_device_flow(settings: Settings, device_code: str) -> dict[str, Any]:
    data = await asyncio.to_thread(
        _post_form,
        TOKEN_URL,
        {
            "client_id": CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        True,
    )
    access = data.get("access_token")
    if access:
        settings.use_kimi_code = True
        settings.api_key = ""
        settings.auth_token = ""
        settings.oauth_access_token = access
        settings.oauth_refresh_token = data.get("refresh_token", "")
        settings.oauth_expires_at = int(time.time()) + int(data.get("expires_in", 900))
        settings.save()
        return {"status": "success", "oauth": status(settings)}
    error = str(data.get("error", "unknown"))
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "slow_down":
        return {
            "status": "slow_down",
            "interval": max(2, int(data.get("interval", 5))),
        }
    if error == "expired_token":
        return {"status": "expired"}
    if error == "access_denied":
        return {"status": "denied"}
    return {
        "status": "error",
        "error": data.get("error_description") or error,
    }


async def login(settings: Settings) -> dict[str, Any]:
    local = read_local_credentials()
    if not local:
        raise OAuthError(
            "Не нашёл вход Kimi Code на этом компьютере. Откройте Kimi Code, "
            "войдите в аккаунт и повторите."
        )
    previous = (settings.use_kimi_code, settings.oauth_access_token,
                settings.oauth_refresh_token, settings.oauth_expires_at)
    settings.use_kimi_code = True
    settings.oauth_access_token = local["access_token"]
    settings.oauth_refresh_token = local["refresh_token"]
    settings.oauth_expires_at = int(local.get("expires_at", 0))
    try:
        await ensure_access_token(settings)
    except Exception:
        (settings.use_kimi_code, settings.oauth_access_token,
         settings.oauth_refresh_token, settings.oauth_expires_at) = previous
        raise
    settings.save()
    return status(settings)


def logout(settings: Settings) -> None:
    settings.use_kimi_code = False
    settings.oauth_access_token = ""
    settings.oauth_refresh_token = ""
    settings.oauth_expires_at = 0
    settings.save()


def status(settings: Settings) -> dict[str, Any]:
    now = int(time.time())
    return {
        "connected": bool(settings.use_kimi_code),
        "has_refresh_token": bool(settings.oauth_refresh_token),
        "expires_in": max(0, settings.oauth_expires_at - now),
        "account": "Kimi Code",
    }
