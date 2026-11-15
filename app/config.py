from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "KompasChat"
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "kompas3d-mcp"


def home_dir() -> Path:
    override = os.environ.get("KOMPAS_CHAT_HOME")
    if override:
        path = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        path = Path(appdata) / APP_NAME if appdata else Path.home() / f".{APP_NAME.lower()}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / "_.probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return path
    except OSError:
        fallback = ROOT / ".data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def default_python() -> str:
    candidate = VENDOR / ".venv" / "Scripts" / "python.exe"
    return str(candidate)


def _dpapi_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32crypt  # noqa: F401
    except Exception:
        return False
    return True


def protect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("dpapi:"):
        return value
    if not _dpapi_available():
        return "plain:" + value
    import win32crypt

    blob = win32crypt.CryptProtectData(value.encode("utf-8"), APP_NAME, None, None, None, 0)
    return "dpapi:" + base64.b64encode(blob).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain:"):
        return value[6:]
    if not value.startswith("dpapi:"):
        return value
    try:
        import win32crypt

        blob = base64.b64decode(value[6:])
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")
    except Exception:
        return ""


@dataclass
class Settings:
    api_key: str = ""
    auth_token: str = ""
    use_kimi_code: bool = False
    oauth_access_token: str = ""
    oauth_refresh_token: str = ""
    oauth_expires_at: int = 0
    base_url: str = "https://api.kimi.ai/coding/"
    model: str = "k3.1-pro"
    max_tokens: int = 8192
    max_steps: int = 24
    tool_mode: str = "core"
    panel_width: int = 440
    startup_connect: bool = True
    mcp_command: str = field(default_factory=default_python)
    mcp_args: list[str] = field(default_factory=lambda: ["-m", "kompas_mcp.server"])
    mcp_cwd: str = field(default_factory=lambda: str(VENDOR))
    save_dir: str = ""
    temperature: float = 0.2

    def __post_init__(self) -> None:
        if not self.save_dir:
            docs = Path.home() / "Documents" / "KompasChat"
            self.save_dir = str(docs)

    @property
    def config_path(self) -> Path:
        return home_dir() / "config.json"

    def load(self) -> "Settings":
        path = self.config_path
        if not path.exists():
            return self
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return self
        for key, value in raw.items():
            if key == "api_key":
                self.api_key = unprotect(value)
            elif key == "auth_token":
                self.auth_token = unprotect(value)
            elif key in ("oauth_access_token", "oauth_refresh_token"):
                setattr(self, key, unprotect(value))
            elif key in self.__dataclass_fields__:
                setattr(self, key, value)
        return self

    def save(self) -> None:
        raw: dict[str, Any] = asdict(self)
        raw["api_key"] = protect(self.api_key)
        raw["auth_token"] = protect(self.auth_token)
        raw["oauth_access_token"] = protect(self.oauth_access_token)
        raw["oauth_refresh_token"] = protect(self.oauth_refresh_token)
        self.config_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def ensure_save_dir(self) -> Path:
        path = Path(self.save_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            fallback = home_dir() / "models"
            fallback.mkdir(parents=True, exist_ok=True)
            self.save_dir = str(fallback)
            return fallback

    def has_credentials(self) -> bool:
        return bool(
            self.api_key
            or self.auth_token
            or self.use_kimi_code
            or os.environ.get("KIMI_AUTH_TOKEN")
        )

    def auth_mode(self) -> str:
        if self.use_kimi_code:
            return "kimi_code"
        if self.api_key or self.auth_token or os.environ.get("KIMI_AUTH_TOKEN"):
            return "api_key"
        return "none"

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = ""
        data["auth_token"] = ""
        data["oauth_access_token"] = ""
        data["oauth_refresh_token"] = ""
        data["has_key"] = self.has_credentials()
        data["auth_mode"] = self.auth_mode()
        data["config_path"] = str(self.config_path)
        data["vendor_python_exists"] = Path(self.mcp_command).exists()
        return data


def load_settings() -> Settings:
    return Settings().load()

