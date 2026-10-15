"""Load and validate ``config/ashare_sources.yaml``."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "ashare_sources.yaml"


@dataclass
class SourceConfig:
    name: str
    enabled: bool
    role: str
    egress: str
    page_size: int
    max_pages: int
    request_delay_s: float
    retries: int
    timeout_s: int
    vps_proxy: str
    token_env: str | None


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Return the raw YAML mapping (already dict) or a minimal default."""
    target = path or CONFIG_PATH
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def source_config(raw: dict[str, Any], name: str) -> SourceConfig:
    src = (raw.get("sources") or {}).get(name) or {}
    return SourceConfig(
        name=name,
        enabled=bool(src.get("enabled", False)),
        role=str(src.get("role", "")),
        egress=str(src.get("egress", "direct")),
        page_size=int(src.get("page_size", 100)),
        max_pages=int(src.get("max_pages", 60)),
        request_delay_s=float(src.get("request_delay_s", 0.6)),
        retries=int(src.get("retries", 4)),
        timeout_s=int(src.get("timeout_s", 20)),
        vps_proxy=str(src.get("vps_proxy", "socks5h://127.0.0.1:1080")),
        token_env=src.get("token_env") or None,
    )
