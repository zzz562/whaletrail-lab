"""Eastmoney (东方财富) A-share source.

Whole-market spot via ``push2`` ``clist`` (fast layer): after the 15:00 close
the realtime snapshot is the day's final OHLCV.  Per-symbol history via
``push2his`` kline exists for the ``DataSource.get_daily`` contract but the
bulk fast path does not use it.

Both endpoints are rate-limited by source IP and cap ``clist`` at 100 rows per
page, so the fast layer paginates with pacing + retries.  ``egress=vps`` routes
requests through a SOCKS5 tunnel on the Mac mini for throttled home broadband.
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from whaletrail.data.base import DataSource

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# Shanghai/Shenzhen main board + STAR + ChiNext (Beijing excluded; baostock fills it).
CLIST_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
CLIST_FIELDS = "f12,f13,f2,f15,f16,f17,f18,f5,f6,f8,f3"
KLINE_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


def _repo_code(market: object, digits: object) -> str | None:
    """``market=1`` (Shanghai) / ``0`` (Shenzhen) + 6-digit code → ``sh.600690``."""
    code = str(digits or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    prefix = "sh" if str(market).strip() == "1" else "sz"
    return f"{prefix}.{code}"


class EastmoneySource(DataSource):
    """Fast OHLCV layer from Eastmoney; implements the daily contract too."""

    def __init__(
        self,
        *,
        egress: str = "direct",
        page_size: int = 100,
        max_pages: int = 60,
        request_delay_s: float = 0.6,
        retries: int = 4,
        timeout_s: int = 20,
        vps_proxy: str = "socks5h://127.0.0.1:1080",
    ) -> None:
        self.page_size = page_size
        self.max_pages = max_pages
        self.request_delay_s = request_delay_s
        self.retries = retries
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.trust_env = False
        self.proxies = (
            {"http": vps_proxy, "https": vps_proxy} if egress == "vps" else None
        )

    def _get(self, url: str, params: dict) -> requests.Response | None:
        last: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                time.sleep(self.request_delay_s * (attempt + 1))
            try:
                resp = self.session.get(
                    url, params=params, headers=HEADERS,
                    timeout=self.timeout_s, proxies=self.proxies,
                )
                if resp.status_code == 200:
                    return resp
            except requests.RequestException as exc:
                last = exc
        if last is not None:
            raise last
        return None

    def fetch_market_spot(self) -> pd.DataFrame:
        """Return today's whole-market OHLCV snapshot indexed by ``sh./sz.`` code.

        Columns: ``open, high, low, close, volume, amount, turn, pct_chg``.
        ``volume`` is converted from 手 to 股.  Empty DataFrame when throttled.
        """
        rows: list[dict] = []
        for page in range(1, self.max_pages + 1):
            params = {
                "pn": page,
                "pz": self.page_size,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": CLIST_FS,
                "fields": CLIST_FIELDS,
            }
            try:
                resp = self._get(CLIST_URL, params)
            except requests.RequestException:
                break
            if resp is None:
                break
            data = (resp.json() or {}).get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            rows.extend(diff)
            if len(diff) < self.page_size:
                break
            if len(rows) >= int(data.get("total") or 0):
                break
            time.sleep(self.request_delay_s)

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        for col in ["f2", "f15", "f16", "f17", "f18", "f6", "f8", "f3"]:
            df[col] = pd.to_numeric(df[col].replace("-", pd.NA), errors="coerce")
        df["f5"] = pd.to_numeric(df["f5"].replace("-", pd.NA), errors="coerce")

        out = pd.DataFrame(
            {
                "open": df["f17"],
                "high": df["f15"],
                "low": df["f16"],
                "close": df["f2"],
                "volume": df["f5"] * 100,  # 手 -> 股
                "amount": df["f6"],
                "turn": df["f8"],
                "pct_chg": df["f3"],
            }
        )
        codes = [_repo_code(m, d) for m, d in zip(df["f13"], df["f12"])]
        out.index = pd.Index(codes, name="code")
        out = out.dropna(subset=["open", "high", "low", "close"])
        return out

    def get_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Per-symbol daily K via push2his (unadjusted ``fqt=0``)."""
        secid = (
            ("1." if symbol.startswith("sh.") else "0.") + symbol[3:]
            if symbol[:3] in ("sh.", "sz.")
            else symbol
        )
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": KLINE_FIELDS,
            "klt": "101",
            "fqt": "0",
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        }
        try:
            resp = self._get(KLINE_URL, params)
        except requests.RequestException:
            return pd.DataFrame()
        if resp is None:
            return pd.DataFrame()
        klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
        rows = [k.split(",") for k in klines]
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows, columns="date open close high low volume amount amp pct_chg change turn".split()
        )
        for col in ["open", "close", "high", "low", "volume", "amount", "turn", "pct_chg"]:
            df[col] = pd.to_numeric(df[col].replace("", pd.NA), errors="coerce")
        df["volume"] = df["volume"] * 100
        df = df.set_index(pd.to_datetime(df["date"]))
        df.index.name = "date"
        df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
        return df[["open", "high", "low", "close", "volume"]]
