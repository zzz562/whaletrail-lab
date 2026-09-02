#!/usr/bin/env python3
"""WhaleTrail Dashboard — read-only four-tab monitor.

Tabs: Paper / 相似选股 / KOL 评测 / 跟庄复盘.
Dark terminal-style UI (Bloomberg/Fortress-inspired): deep navy surfaces,
amber gold accent, tabular monospace numerics, semantic green/red.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whaletrail.data.baostock_source import to_baostock_code
from whaletrail.data.history import build_daily_history
from whaletrail.data.watchlist import load_watchlist
from whaletrail.metrics.performance import calculate_metrics, compute_trade_pnl
from whaletrail.similarity import normalize, rank_similar
from whaletrail.storage.repository import Repository

st.set_page_config(page_title="WhaleTrail", layout="wide")

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "whaletrail.db"
DATA_CACHE_DIR = ROOT / "data_cache"
WATCHLIST_PATH = ROOT / "config" / "watchlist.yaml"
CN_TZ = ZoneInfo("Asia/Shanghai")

KOL_ROSTER = [
    "feigeshuogushi", "415254141a", "sd145157", "carla121100",
    "fighterpromoter", "deelu179242", "oldk_gillis", "jimisenlin66474",
    "todo_seguridad", "snake_w", "1044669280a", "lilratchetgurl",
    "bbloveu7777", "eliasvancequant", "archdeng007", "barber_mae68154",
    "aw3ff_", "agucdx",
]
KOL_EVAL_FILES = (
    "kol_eval.json", "kol_evaluation.json", "ashare_kol_eval.json",
    "kol_picks.json", "kol_review.json",
)
GENZHUANG_LABEL_FILES = (
    "watchlist_labels.json", "genzhuang_labels.json", "genzhuang.json",
)
VALID_LABELS = {"观察", "接近", "触发"}
KOL_ROSTER_SET = {a.lower() for a in KOL_ROSTER}
