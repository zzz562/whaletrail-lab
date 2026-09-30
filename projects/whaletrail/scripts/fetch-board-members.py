#!/usr/bin/env python3
"""Fetch 板块 / 指数 **配置表**（成分 + 权重）into ``board_members``.

Only the *configuration* comes from vendors — every 板块/指数 series is computed
locally from ``daily_kline``.

Three sources, all domestic (direct connection, system proxy deliberately
ignored):

- ``em``   东方财富 (``concept_em`` / ``industry_em`` / ``region_em`` /
  ``other_em``): the board-membership table via the ``datacenter-web`` report
  ``RPT_BOARD_CONSTITUENT`` (~94k rows, ~190 requests).  The ``push2``
  ``clist/get`` endpoint that akshare uses is WAF-throttled per source IP
  (works from a VPS, dropped from the home line 2026-09-30), while the
  datacenter report answers normally and carries the exchange in ``SECUCODE``.
  ``BOARD_TYPE_NEW``: 1=地域 2=行业 3=概念 4=其他（风格/事件/资金）.  No weights.
- ``csi``  中证指数公司 (``index_csi``): the official ``closeweight`` xls per
  index code — constituents, weights, exchange and the vendor's effective date.
- ``cn``   国证/深证信息 (``index_cn``): ``sample-detail/download`` xls per code —
  the member list is complete but **weights are published only for the top 10
  names** (sum 24–54% of the index), so 国证/深证 indexes cannot be reproduced
  from file weights; use equal weight or our own free-float estimate.
  中证 files carry the full weight table (sums to 100%), verified 2026-09-30 to
  reproduce 沪深300/中证500/中证1000/上证50 within 0.02–0.03pp per day.

Members are normalised to this repo's ``sh./sz./bj.`` codes.  北交所 (``bj.``)
members are stored faithfully but have no bars in ``daily_kline``, so they drop
out of local aggregation.

Usage:
  python scripts/fetch-board-members.py                     # all three sources
  python scripts/fetch-board-members.py --sources csi,cn
  python scripts/fetch-board-members.py --em-pages 3 --no-alert   # smoke test
  python scripts/fetch-board-members.py --snapshot 2026-09-30
"""

from __future__ import annotations

import argparse
import io
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.reporting.telegram import send as tg_send  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB_PATH = ROOT / "results" / "whaletrail.db"

EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_DC_REPORT = "RPT_BOARD_CONSTITUENT"
EM_BOARD_TYPES = {
    "1": "region_em",
    "2": "industry_em",
    "3": "concept_em",
    "4": "other_em",
}
EM_SUFFIX_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

CSI_INDEXES = {
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000906": "中证800",
    "000985": "中证全指",
    "000688": "科创50",
}
CN_INDEXES = {
    "399001": "深证成指",
    "399005": "中小100",
    "399006": "创业板指",
    "399303": "国证2000",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}

EXCHANGE_PREFIX = {
    "上海证券交易所": "sh",
    "深圳证券交易所": "sz",
    "北京证券交易所": "bj",
    "Shanghai Stock Exchange": "sh",
    "Shenzhen Stock Exchange": "sz",
    "Beijing Stock Exchange": "bj",
}

# 国内三源全部直连：macOS 系统代理（Clash）时通时断，走它反而添乱。
SESSION = requests.Session()
SESSION.trust_env = False


# ── helpers ──────────────────────────────────────────────────────
def to_repo_code(raw, exchange: str | None = None) -> str | None:
    """Map a 6-digit code (+ optional exchange name) to ``sh.600690`` form."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    digits = digits.zfill(6)
    if len(digits) != 6:
        return None
    if exchange:
        prefix = EXCHANGE_PREFIX.get(str(exchange).strip())
        if prefix:
            return f"{prefix}.{digits}"
    if digits.startswith("920"):  # 北交所新号段
        return f"bj.{digits}"
    if digits[0] == "9":  # 沪B
        return f"sh.{digits}"
    if digits[0] == "6":
        return f"sh.{digits}"
    if digits[0] in "0123":  # 含深B 200xxx
        return f"sz.{digits}"
    if digits[0] in "48":  # 北交所老号段
        return f"bj.{digits}"
    return None


def secucode_to_repo(secucode) -> str | None:
    """``000001.SZ`` / ``920489.BJ`` → ``sz.000001`` / ``bj.920489``."""
    sec = str(secucode or "").strip().upper()
    if "." not in sec:
        return None
    digits, _, suffix = sec.partition(".")
    prefix = EM_SUFFIX_PREFIX.get(suffix)
    if prefix is None or not digits.isdigit():
        return None
    return f"{prefix}.{digits.zfill(6)}"


def _norm_date(value) -> str | None:
    """``20260831`` / ``2026-09-29`` → ``2026-08-31``."""
    s = str(value).strip()
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s if len(s) == 10 else None


def _find_col(df: pd.DataFrame, include: list[str], exclude: list[str] | None = None) -> str | None:
    """First column whose header contains every *include* string and no *exclude*."""
    for col in df.columns:
        name = str(col)
        if all(k in name for k in include) and not any(k in name for k in (exclude or [])):
            return col
    return None


def _get(url: str, *, retries: int = 3, timeout: int = 30, referer: str | None = None) -> requests.Response:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp
            last = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001 - retried below
            last = exc
        time.sleep(1.5 * (attempt + 1) + random.random())
    raise RuntimeError(f"GET 失败（{retries} 次）：{url}（{last}）")


def _em_report_page(page: int, page_size: int, *, retries: int = 3) -> dict:
    """One page of the 东财 board-constituent report."""
    url = (
        f"{EM_DC_URL}?reportName={EM_DC_REPORT}&columns=ALL&pageSize={page_size}"
        f"&pageNumber={page}&source=WEB&client=WEB"
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=40)
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("result") is not None or payload.get("success") is False:
                    return payload
                raise RuntimeError("空 result")
            last = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001 - retried below
            last = exc
        time.sleep(1.5 * (attempt + 1) + random.random())
    raise RuntimeError(f"东财报表请求失败（第 {page} 页）：{last}")


# ── sources ──────────────────────────────────────────────────────
def fetch_em(
    repo: Repository,
    snapshot: str,
    *,
    sleep_s: float,
    page_size: int,
    types: set[str],
    max_pages: int | None,
) -> dict:
    """东方财富 板块成分表（无权重），按板块分组落库。"""
    stats = {"boards": 0, "members": 0, "skipped": 0, "rows": 0}
    names = repo.universe_names()
    boards: dict[tuple[str, str, str], list[dict]] = {}
    page = 1
    collected = 0
    total: int | None = None
    while True:
        payload = _em_report_page(page, page_size)
        result = payload.get("result") or {}
        if total is None:
            total = int(result.get("count") or 0)
        rows = result.get("data") or []
        if not rows:
            break
        collected += len(rows)
        for row in rows:
            btype = str(row.get("BOARD_TYPE_NEW") or "")
            if btype not in types:
                continue
            board_id = str(row.get("BOARD_CODE_BK") or "").strip()
            if not board_id:
                board_id = f"BK{str(row.get('BOARD_CODE') or '').zfill(4)}"
            code = secucode_to_repo(row.get("SECUCODE"))
            if code is None:
                stats["skipped"] += 1
                continue
            key = (f"EM:{board_id}", EM_BOARD_TYPES[btype], str(row.get("BOARD_NAME") or ""))
            boards.setdefault(key, []).append({"code": code, "name": names.get(code, "")})
        print(f"  东财 {page} 页 · 累计 {collected}/{total} 行 · 板块 {len(boards)} 个")
        if total is not None and collected >= total:
            break
        if max_pages is not None and page >= max_pages:
            print(f"  （--em-pages {max_pages} 限制，提前结束）")
            break
        page += 1
        time.sleep(sleep_s)

    for (board_id, board_type, board_name), members in boards.items():
        stats["boards"] += 1
        stats["members"] += repo.save_board_members(
            board_id=board_id,
            board_type=board_type,
            board_name=board_name,
            source="eastmoney",
            snapshot_date=snapshot,
            rows=members,
        )
    stats["rows"] = collected
    return stats


def _parse_index_xls(
    content: bytes,
    *,
    code_col: list[str],
    name_col: list[str],
    weight_col: list[str],
    date_col: list[str],
    exchange_col: list[str] | None,
) -> tuple[list[dict], str | None]:
    df = pd.read_excel(io.BytesIO(content), engine="xlrd", dtype=str)
    c_code = _find_col(df, code_col, ["Eng"])
    c_name = _find_col(df, name_col, ["Eng"])
    c_weight = _find_col(df, weight_col, ["Eng"])
    c_date = _find_col(df, date_col, ["Eng"])
    c_ex = _find_col(df, exchange_col or ["__none__"], ["Eng"]) if exchange_col else None
    if c_code is None:
        raise RuntimeError(f"找不到成分代码列，实际列={list(df.columns)}")
    rows: list[dict] = []
    vendor_date: str | None = None
    for _, rec in df.iterrows():
        code = to_repo_code(rec[c_code], rec[c_ex] if c_ex else None)
        if code is None:
            continue
        if vendor_date is None and c_date is not None:
            vendor_date = _norm_date(rec[c_date])
        weight = None
        if c_weight is not None:
            try:
                weight = float(str(rec[c_weight]).replace("%", "").strip())
            except (TypeError, ValueError):
                weight = None
        rows.append(
            {
                "code": code,
                "name": str(rec[c_name]).strip() if c_name is not None else "",
                "weight": weight,
            }
        )
    for row in rows:
        row["vendor_date"] = vendor_date
    return rows, vendor_date


def fetch_csi(repo: Repository, snapshot: str, *, sleep_s: float) -> dict:
    """中证指数公司官方 closeweight 表（成分 + 权重 + 交易所）。"""
    stats = {"boards": 0, "members": 0, "failed": 0}
    for code, name in CSI_INDEXES.items():
        url = (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
            f"autofile/closeweight/{code}closeweight.xls"
        )
        try:
            content = _get(url, referer="https://www.csindex.com.cn/").content
            rows, vendor_date = _parse_index_xls(
                content,
                code_col=["Constituent Code", "成份券代码"],
                name_col=["Constituent Name", "成份券名称"],
                weight_col=["权重"],
                date_col=["日期Date", "日期"],
                exchange_col=["交易所Exchange", "交易所"],
            )
        except Exception as exc:  # noqa: BLE001 - one index must not kill the run
            print(f"  [csi {code} {name}] 失败：{exc}")
            stats["failed"] += 1
            continue
        saved = repo.save_board_members(
            board_id=f"CSI:{code}",
            board_type="index_csi",
            board_name=name,
            source="csindex",
            snapshot_date=snapshot,
            rows=rows,
        )
        stats["boards"] += 1
        stats["members"] += saved
        weighted = [r for r in rows if r["weight"] is not None]
        wsum = sum(r["weight"] for r in weighted)
        print(
            f"  [csi {code} {name}] {saved} 只 · 权重 {len(weighted)}/{len(rows)} "
            f"合计 {wsum:.2f}% · 生效日 {vendor_date}"
        )
        time.sleep(sleep_s)
    return stats


def fetch_cn(repo: Repository, snapshot: str, *, sleep_s: float) -> dict:
    """国证 / 深证信息样本股表（成分 + 相对权重）。"""
    stats = {"boards": 0, "members": 0, "failed": 0}
    for code, name in CN_INDEXES.items():
        url = f"https://www.cnindex.com.cn/sample-detail/download?indexcode={code}"
        try:
            content = _get(url, referer="https://www.cnindex.com.cn/").content
            if content[:4] != b"\xd0\xcf\x11\xe0":
                raise RuntimeError(f"不是 xls（head={content[:16]!r}）")
            rows, vendor_date = _parse_index_xls(
                content,
                code_col=["样本代码"],
                name_col=["样本简称"],
                weight_col=["权重"],
                date_col=["日期"],
                exchange_col=None,
            )
        except Exception as exc:  # noqa: BLE001 - one index must not kill the run
            print(f"  [cn {code} {name}] 失败：{exc}")
            stats["failed"] += 1
            continue
        saved = repo.save_board_members(
            board_id=f"CN:{code}",
            board_type="index_cn",
            board_name=name,
            source="cnindex",
            snapshot_date=snapshot,
            rows=rows,
        )
        stats["boards"] += 1
        stats["members"] += saved
        weighted = [r for r in rows if r["weight"] is not None]
        wsum = sum(r["weight"] for r in weighted)
        print(
            f"  [cn {code} {name}] {saved} 只 · 权重 {len(weighted)}/{len(rows)}（仅前 10 名）"
            f" 合计 {wsum:.2f}% · 生效日 {vendor_date}"
        )
        time.sleep(sleep_s)
    return stats


# ── main ─────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    parser.add_argument("--sources", default="em,csi,cn", help="逗号分隔：em,csi,cn（默认全部）")
    parser.add_argument("--snapshot", help="快照日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--em-types", default="1,2,3,4", help="东财板块类型：1地域 2行业 3概念 4其他")
    parser.add_argument("--em-pages", type=int, help="东财最多抓 N 页（冒烟测试用）")
    parser.add_argument("--page-size", type=int, default=500, help="东财报表每页行数（默认 500）")
    parser.add_argument("--sleep", type=float, default=0.3, help="请求间隔秒（默认 0.3）")
    parser.add_argument("--no-alert", action="store_true", help="不推送 Telegram 告警")
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - {"em", "csi", "cn"}
    if unknown:
        parser.error(f"未知来源：{sorted(unknown)}")
    types = {t.strip() for t in args.em_types.split(",") if t.strip()}
    bad_types = types - set(EM_BOARD_TYPES)
    if bad_types:
        parser.error(f"未知板块类型：{sorted(bad_types)}（可选 1,2,3,4）")
    snapshot = args.snapshot or date.today().isoformat()

    started = time.monotonic()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 抓取板块/指数配置表 · 快照 {snapshot} · 来源 {sources}")

    repo = Repository(args.db)
    results: dict[str, dict] = {}
    try:
        if "em" in sources:
            print("东财 板块成分（datacenter 报表）…")
            results["em"] = fetch_em(
                repo,
                snapshot,
                sleep_s=args.sleep,
                page_size=args.page_size,
                types=types,
                max_pages=args.em_pages,
            )
            print(f"  合计：{results['em']}")
        if "csi" in sources:
            print("中证指数公司 成分权重…")
            results["csi"] = fetch_csi(repo, snapshot, sleep_s=args.sleep)
            print(f"  合计：{results['csi']}")
        if "cn" in sources:
            print("国证/深证信息 样本股…")
            results["cn"] = fetch_cn(repo, snapshot, sleep_s=args.sleep)
            print(f"  合计：{results['cn']}")

        boards = repo.board_list()
        members = sum(b["members"] for b in boards)
        by_type: dict[str, int] = {}
        for b in boards:
            by_type[b["board_type"]] = by_type.get(b["board_type"], 0) + 1
        print(
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 完成：最新快照 {len(boards)} 个板块/指数 · "
            f"{members} 条成分 · {by_type}"
        )
    finally:
        repo.close()

    elapsed = time.monotonic() - started
    problems: list[str] = []
    for src, stats in results.items():
        if stats.get("boards", 0) == 0:
            problems.append(f"{src}：一个板块都没落库")
        if stats.get("failed"):
            problems.append(f"{src}：{stats['failed']} 个指数失败")
    if problems and not args.no_alert:
        tg_send(
            "⚠️ 板块/指数配置表抓取异常（{}）\n{}".format(
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                "\n".join(f"• {p}" for p in problems),
            ),
            quiet=True,
        )
        print("已推送 Telegram 告警")
    print(f"耗时 {elapsed / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
