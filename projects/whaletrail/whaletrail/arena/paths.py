"""Filesystem locations for the arena. Code stays in git; ledgers stay under results/."""

from __future__ import annotations

import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    return Path(os.environ.get("WT_DATA_ROOT", CODE_ROOT)).expanduser().resolve()


def db_path() -> Path:
    return data_root() / "results" / "whaletrail.db"


def arena_dir() -> Path:
    return data_root() / "results" / "kol_arena"


def inbox_dir() -> Path:
    return arena_dir() / "inbox"


def processed_dir() -> Path:
    return arena_dir() / "processed"


def rejected_dir() -> Path:
    return arena_dir() / "rejected"


def roster_path() -> Path:
    return CODE_ROOT / "config" / "arena_roster.yaml"
