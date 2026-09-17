#!/usr/bin/env python3
"""Parity check: the 相似选股 page and ashare-similar.py must rank identically.

Drives the dashboard script headlessly (streamlit AppTest, i.e. the real page
code) and the CLI with the same reference and template window, then compares
the printed rows.  Both read the local SQLite ``daily_kline``, so run it on
Mac mini.

  python scripts/check-similar-parity.py
  python scripts/check-similar-parity.py --symbol sz.000823 --start 2025-12-02 --end 2026-05-21
  python scripts/check-similar-parity.py --symbol sz.000823 --window 88
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

LOG_PATH = ROOT / "logs" / "similar-scan.log"
CODE_RE = re.compile(r"^(sh|sz|bj)\.\d{6}$")


def page_top(symbol: str, start: str | None, end: str | None, top: int) -> list[str]:
    """Top rows as the page produced them, read back from the scan log."""
    before = len(LOG_PATH.read_text(encoding="utf-8").splitlines()) if LOG_PATH.exists() else 0
    app = AppTest.from_file(str(ROOT / "scripts" / "dashboard.py"), default_timeout=900)
    app.query_params["page"] = "similar"
    app.run()
    if start:
        app.date_input(key="similar_scan_start").set_value(date.fromisoformat(start))
        app.date_input(key="similar_scan_end").set_value(date.fromisoformat(end))
        app.run()
    app.text_input[0].set_value(symbol.split(".")[-1])
    app.run()
    app.button(key="similar_scan_btn").click()
    app.run()
    if app.exception:
        raise SystemExit(f"page raised: {[str(e.value) for e in app.exception]}")
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    if len(lines) - before != 1:
        raise SystemExit("page did not write exactly one scan log line")
    return json.loads(lines[-1].split(" ", 2)[2])["top20"][:top]


def cli_top(symbol: str, start: str | None, end: str | None, window: int, top: int) -> tuple[list[str], str]:
    args = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts" / "ashare-similar.py"),
            "--symbol", symbol, "--top", str(top)]
    args += ["--start", start, "--end", end] if start else ["--window", str(window)]
    run = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)
    if run.returncode != 0:
        raise SystemExit(f"cli failed ({run.returncode}): {run.stdout}{run.stderr}")
    lines = run.stdout.splitlines()
    codes = []
    for line in lines:
        for token in line.split():
            if CODE_RE.match(token):
                codes.append(token)
                break
    head = next((ln for ln in lines if "相似选股" in ln), "")
    return codes[:top], head


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="sz.000823", help="Reference, e.g. sz.000823")
    parser.add_argument("--start", help="Template window start YYYY-MM-DD (with --end)")
    parser.add_argument("--end", help="Template window end YYYY-MM-DD (with --start)")
    parser.add_argument("--window", type=int, default=88, help="Template length when no dates (default 88)")
    parser.add_argument("--top", type=int, default=20, help="Rows to compare (default 20)")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end go together")
    if args.symbol.split(".")[-1] == args.symbol:
        parser.error("--symbol needs a market prefix, e.g. sz.000823")

    page = page_top(args.symbol, args.start, args.end, args.top)
    cli, head = cli_top(args.symbol, args.start, args.end, args.window, args.top)
    print(f"page top{args.top}: {page}")
    print(f"cli  top{args.top}: {cli}")
    print(f"cli header: {head.strip()}")
    if page == cli:
        print("MATCH — page and CLI rank identically")
        return 0
    print("MISMATCH — the two entry points disagree")
    return 1


if __name__ == "__main__":
    sys.exit(main())
