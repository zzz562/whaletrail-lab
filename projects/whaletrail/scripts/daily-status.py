#!/usr/bin/env python3
"""Daily data-layer status report for Telegram.

One message a day covering every asset the pipeline maintains:

- A股日线/指数/静态表（baostock，工作日 16:30/20:00 班次）— completeness via
  ``check-ashare-data.py --no-alert``（最近 3 个交易日的覆盖 + 基准指数）
- 板块/指数配置表（``board_members``，周六刷新）
- 黄金/美股日线（yfinance → ``data_cache/*.parquet``）— 只在有人跑回测/日报时刷新，
  所以这里报「最后更新时间 + 是否超期」
- watchlist 快照（``quote_snapshots``，OpenClaw ``whaletrail-ashare`` 15:30 写）
- 常驻服务（dashboard / stage / live / gateway / tunnel）
- 最近抓取班次结果（``logs/fetch-baostock.log`` 里带时间戳的汇总行）

Exit code 0 = 全部正常，1 = 有需要处理的项目。

Usage:
  python scripts/daily-status.py               # 打印 + 推送
  python scripts/daily-status.py --no-alert    # 只打印
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.reporting.telegram import send as tg_send  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB_PATH = ROOT / "results" / "whaletrail.db"
FETCH_LOG = ROOT / "logs" / "fetch-baostock.log"
CACHE = ROOT / "data_cache"
SERVICES = (
    "ai.whaletrail-dashboard",
    "ai.whaletrail-dashboard-stage",
    "ai.whaletrail-live",
    "ai.openclaw.gateway",
    "com.zeph.reverse-tunnel",
)
GOLD_STALE_DAYS = 3  # 黄金日线靠手动/日报刷新，超过这么多天没动就提示
BOARD_STALE_DAYS = 10  # 配置表周六刷新，超过一轮半没动就提示


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def _last_stamped_summary(path: Path) -> str | None:
    """Last ``[ts] 完成：...`` line of a fetch log."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    hits = re.findall(r"^\[([\d\-: ]+)\] (完成：.+)$", text, flags=re.M)
    if not hits:
        return None
    ts, body = hits[-1]
    return f"{ts} · {body}"


def _days_since(mtime: float) -> int:
    return int((datetime.now().timestamp() - mtime) // 86400)


def collect(db: str) -> tuple[list[str], list[str], list[str]]:
    """Return ``(ok_lines, warn_lines, info_lines)``."""
    ok: list[str] = []
    warn: list[str] = []
    info: list[str] = []

    # ── A股（复用体检脚本的判定）────────────────────────────────
    rc, out = _run([sys.executable, "scripts/check-ashare-data.py", "--no-alert", "--daily"])
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    if rc == 0:
        ok.append(first.split("OK：", 1)[-1].replace(" | ", " · ").replace("• ", ""))
    else:
        warn.append((first or "体检脚本异常").replace("⚠️ ", ""))
        warn.extend("   " + ln.lstrip("• ").strip() for ln in lines[1:4])
    summary = _last_stamped_summary(FETCH_LOG)
    if summary:
        info.append("  班次 " + summary)

    repo = Repository(db)
    try:
        industry_date = repo.conn.execute(
            "SELECT COALESCE(MAX(update_date),'-') FROM ashare_industry"
        ).fetchone()[0]
        board_date, board_n = repo.conn.execute(
            "SELECT COALESCE(MAX(snapshot_date),'-'), COUNT(DISTINCT board_id) FROM board_members"
        ).fetchone()
        board_members = repo.conn.execute(
            "SELECT COUNT(*) FROM board_members WHERE snapshot_date = ?", (board_date,)
        ).fetchone()[0]
        snapshot_at = repo.conn.execute(
            "SELECT COALESCE(MAX(timestamp),'-') FROM quote_snapshots"
        ).fetchone()[0]
        universe = repo.conn.execute(
            "SELECT COUNT(*) FROM ashare_universe WHERE status='1'"
        ).fetchone()[0]
    finally:
        repo.close()

    ok.append(f"静态表 行业 {industry_date} · 上市 {universe} 只")

    if board_date == "-":
        warn.append("配置表 板块/指数还没抓过")
    else:
        age = (date.today() - date.fromisoformat(board_date)).days
        line = f"配置表 {board_n} 个板块/指数 · {board_members} 条成分 · 快照 {board_date}"
        if age > BOARD_STALE_DAYS:
            warn.append(line + f"（{age} 天前，周六刷新任务可能没跑）")
        else:
            ok.append(line)

    # ── 黄金/美股日线缓存 ────────────────────────────────────────
    stamps = [
        (name.replace(".parquet", ""), _days_since((CACHE / name).stat().st_mtime))
        for name in ("GLD.parquet", "SPY.parquet")
        if (CACHE / name).exists()
    ]
    if not stamps:
        warn.append("黄金 parquet 缓存不存在（先跑一次 run-backtest.py）")
    else:
        detail = " · ".join(f"{n} {d} 天前" for n, d in stamps)
        if max(d for _, d in stamps) > GOLD_STALE_DAYS:
            warn.append(f"黄金/美股日线 {detail}（日报任务已不在，需手动或重排）")
        else:
            ok.append(f"黄金/美股日线 {detail}")

    ok.append("watchlist 快照 " + str(snapshot_at)[:16].replace("T", " "))

    # ── 常驻服务 ─────────────────────────────────────────────────
    _, out = _run(["launchctl", "list"])
    missing = [s for s in SERVICES if s not in out]
    if missing:
        warn.append("服务未加载：" + "、".join(missing))
    else:
        ok.append(f"常驻服务 {len(SERVICES)}/{len(SERVICES)} 在飞")
    return ok, warn, info


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    parser.add_argument("--no-alert", action="store_true", help="只打印，不推送")
    args = parser.parse_args()

    ok, warn, info = collect(args.db)
    body = [f"📊 WhaleTrail 每日数据状态（{datetime.now():%m-%d %H:%M}）", ""]
    if warn:
        top = [w for w in warn if not w.startswith(" ")]
        body.append(f"需处理 {len(top)} 项：")
        body.extend(("• " + w) if not w.startswith(" ") else w for w in warn)
        body.append("")
    body.append("正常：")
    body.extend(f"• {o}" for o in ok)
    if info:
        body.append("最近班次：")
        body.extend(info)
    text = "\n".join(body)
    print(text)

    if not args.no_alert:
        if tg_send(text, quiet=True):
            print("已推送 Telegram")
        else:
            print("⚠️ Telegram 推送失败")
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main())
