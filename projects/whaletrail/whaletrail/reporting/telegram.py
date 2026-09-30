"""Minimal Telegram push for scheduled jobs (stdlib only).

Credentials come from ``TG_BOT_TOKEN`` / ``TG_CHAT_ID``, falling back to the
git-ignored ``~/.config/whaletrail/telegram.env`` so cron entries need no shell
wrapper.  Telegram is unreachable from the mainland without the local proxy, so
the sender tries the environment proxy first, then a direct connection, then
``127.0.0.1:7892``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

DEFAULT_CHAT_ID = "5102138680"
DEFAULT_PROXY = "http://127.0.0.1:7892"
ENV_FILE = Path.home() / ".config" / "whaletrail" / "telegram.env"


def _from_env_file(key: str) -> str:
    """Return *key* from the git-ignored env file, or ``""``."""
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def credentials() -> tuple[str, str]:
    """Return ``(token, chat_id)`` from the environment or the env file."""
    token = os.environ.get("TG_BOT_TOKEN") or _from_env_file("TG_BOT_TOKEN")
    chat = (
        os.environ.get("TG_CHAT_ID")
        or _from_env_file("TG_CHAT_ID")
        or DEFAULT_CHAT_ID
    )
    return token, chat


def _post(url: str, payload: bytes, proxy: str | None, timeout: int) -> None:
    handlers = (
        [urllib.request.ProxyHandler({})]
        if proxy is None
        else [urllib.request.ProxyHandler({"http": proxy, "https": proxy})]
    )
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    opener.open(req, timeout=timeout)


def _routes() -> list[tuple[str | None, str]]:
    """Proxy routes to try, in order."""
    env_proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    routes: list[tuple[str | None, str]] = []
    if env_proxy:
        routes.append((env_proxy, f"env proxy {env_proxy}"))
    routes.append((None, "direct"))
    if env_proxy != DEFAULT_PROXY:
        routes.append((DEFAULT_PROXY, f"default proxy {DEFAULT_PROXY}"))
    return routes


def send(
    text: str,
    *,
    chat_id: str | None = None,
    timeout: int = 15,
    quiet: bool = False,
    parse_mode: str | None = None,
) -> bool:
    """Push *text* to Telegram; ``True`` when the API accepted it."""
    token, default_chat = credentials()
    if not token:
        if not quiet:
            print(f"  ⚠️ Telegram: no TG_BOT_TOKEN (env or {ENV_FILE})")
        return False

    body: dict[str, object] = {"chat_id": chat_id or default_chat, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    errors: list[str] = []
    for proxy, label in _routes():
        try:
            _post(url, payload, proxy, timeout)
            return True
        except Exception as exc:  # noqa: BLE001 - try the next route, report at the end
            errors.append(f"{label}: {exc}")
    if not quiet:
        print("  ⚠️ Telegram 发送失败：" + " | ".join(errors))
    return False
