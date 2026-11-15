from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .config import ROOT, home_dir, load_settings
from .server import create_app

# pythonw starts the process without console streams; uvicorn logging needs them.
if sys.stdout is None or sys.stderr is None:
    _log_dir = home_dir() / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _stream = open(_log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _stream
    if sys.stderr is None:
        sys.stderr = _stream

INSTANCE_FILE = "instance.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_once(port: int, timeout: float = 0.8) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _wait_health(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _health_once(port, 0.7):
            return True
        time.sleep(0.2)
    return False


def _instance_path() -> Path:
    return home_dir() / INSTANCE_FILE


def _existing_port() -> int | None:
    path = _instance_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        port = int(data["port"])
    except Exception:
        return None
    return port if _health_once(port) else None


def _write_instance(port: int) -> None:
    try:
        _instance_path().write_text(
            json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8"
        )
    except OSError:
        pass


def _clear_instance() -> None:
    try:
        _instance_path().unlink(missing_ok=True)
    except OSError:
        pass


def _launch_panel(url: str, width: int) -> subprocess.Popen | None:
    host_exe = ROOT / "host" / "KompasChatPanel.exe"
    if not host_exe.exists():
        return None
    return subprocess.Popen(
        [
            str(host_exe),
            "--url",
            url,
            "--width",
            str(width),
            "--data-dir",
            str(home_dir() / "WebView2"),
        ],
        cwd=str(host_exe.parent),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kimi chat panel for KOMPAS-3D")
    parser.add_argument("--no-panel", action="store_true", help="run only the local server")
    parser.add_argument("--port", type=int, default=0, help="fixed local port")
    parser.add_argument("--width", type=int, default=0, help="panel width in pixels")
    args = parser.parse_args(argv)

    settings = load_settings()

    if not args.no_panel and not args.port:
        existing = _existing_port()
        if existing:
            _launch_panel(f"http://127.0.0.1:{existing}/", settings.panel_width)
            return 0

    port = args.port or _free_port()
    url = f"http://127.0.0.1:{port}/"

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    if not _wait_health(port):
        print("Не удалось запустить локальный сервер панели.", file=sys.stderr)
        server.should_exit = True
        return 2

    if args.no_panel:
        print(f"Kimi panel server: {url}")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        server.should_exit = True
        return 0

    _write_instance(port)
    width = args.width or settings.panel_width
    process = _launch_panel(url, width)
    if process is None:
        print("Не найден host/KompasChatPanel.exe, открываю в браузере.", file=sys.stderr)
        webbrowser.open(url)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        server.should_exit = True
        _clear_instance()
        return 0

    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    finally:
        server.should_exit = True
        _clear_instance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
