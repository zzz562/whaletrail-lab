"""Sina (新浪财经) minute-bar source for the A-share intraday layer.

Two free endpoints, both direct (no proxy), no token:

- ``hq.sinajs.cn`` batch spot: up to ~800 codes per request, returns current
  price, cumulative volume/amount and day OHLC.  Polled once a minute during
  trading hours to aggregate 10-minute bars locally (route B).
- ``quotes.sina.cn`` ``CN_MarketDataService.getKLineData``: per-symbol kline
  with ``scale=1/5/10/15/60``.  Used once after close to calibrate the day's
  bars into true exchange-style OHLCV (route A, ``scale=10``).

Both endpoints are unofficial; the caller is responsible for pacing, health
probing and fallback.  Requests bypass the system proxy (``trust_env=False``)
because Sina is a domestic origin.
"""

from __future__ import annotations

import json
import math
import re
import time

import requests

SNAPSHOT_URL = "https://hq.sinajs.cn/list={codes}"
KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20x=/CN_MarketDataService.getKLineData"
)
BATCH = 800

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}

_JSONP_RE = re.compile(r"var x=\((.*)\)", re.S)


def strip_dot(code: str) -> str:
    """``sh.600690`` → ``sh600690`` (Sina uses no dot)."""
    return code.replace(".", "")


class SinaMinuteSource:
    """Batch spot + per-symbol kline for A-share minute bars."""

    def __init__(self, *, timeout_s: int = 12, retries: int = 3) -> None:
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = requests.Session()
        self.session.trust_env = False  # domestic origin, bypass system proxy

    def _get(self, url: str, params: dict | None = None) -> requests.Response | None:
        for attempt in range(self.retries):
            if attempt:
                time.sleep(1.0 * attempt)
            try:
                resp = self.session.get(
                    url, params=params, headers=HEADERS, timeout=self.timeout_s
                )
                if resp.status_code == 200:
                    return resp
            except requests.RequestException:
                continue
        return None

    def fetch_snapshots(self, codes: list[str]) -> dict[str, dict]:
        """Batch spot for *codes* (``sh.``/``sz.``) → ``{code: snapshot}``.

        Snapshot keys: ``price, cum_volume, cum_amount, date, time``.
        Suspended / bad codes are absent from the result.
        """
        out: dict[str, dict] = {}
        lookup = {strip_dot(c): c for c in codes}
        symbols = list(lookup)
        for i in range(0, len(symbols), BATCH):
            chunk = symbols[i : i + BATCH]
            resp = self._get(SNAPSHOT_URL.format(codes=",".join(chunk)))
            if resp is None:
                continue
            for line in resp.text.splitlines():
                line = line.strip()
                if not line.startswith("var hq_str_"):
                    continue
                head, _, rest = line.partition("=")
                sym = head[len("var hq_str_") :].strip()
                code = lookup.get(sym)
                if code is None:
                    continue
                start = rest.find('"') + 1
                end = rest.rfind('"')
                if start <= 0 or end <= start:
                    continue
                parts = rest[start:end].split(",")
                if len(parts) < 32:
                    continue
                try:
                    price = float(parts[3])
                    cum_volume = float(parts[8])
                    cum_amount = float(parts[9])
                except (ValueError, IndexError):
                    continue
                if price <= 0 or cum_volume <= 0:
                    continue
                out[code] = {
                    "price": price,
                    "cum_volume": cum_volume,
                    "cum_amount": cum_amount,
                    "date": parts[30],
                    "time": parts[31],
                }
        return out

    def fetch_kline_bars(
        self, code: str, *, scale: int = 10, datalen: int = 120
    ) -> list[dict]:
        """Per-symbol kline → ``[{ts, open, high, low, close, volume, amount}]``.

        ``ts`` is the bar end time (``YYYY-MM-DD HH:MM:SS``), matching Sina's
        kline label convention.  Empty list on failure or dead code.
        """
        params = {
            "symbol": strip_dot(code),
            "scale": str(scale),
            "ma": "no",
            "datalen": str(datalen),
        }
        resp = self._get(KLINE_URL, params)
        if resp is None:
            return []
        m = _JSONP_RE.search(resp.text)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list) or not data:
            return []

        rows: list[dict] = []
        for b in data:
            ts = str(b.get("day") or "")
            if not ts:
                continue
            try:
                o = float(b.get("open", "nan"))
                h = float(b.get("high", "nan"))
                lo = float(b.get("low", "nan"))
                c = float(b.get("close", "nan"))
                v = float(b.get("volume", "nan"))
                a = float(b.get("amount", "nan"))
            except (TypeError, ValueError):
                continue
            if any(math.isnan(x) for x in (o, h, lo, c)):
                continue
            rows.append(
                {
                    "ts": ts,
                    "open": o,
                    "high": h,
                    "low": lo,
                    "close": c,
                    "volume": 0.0 if math.isnan(v) else v,
                    "amount": 0.0 if math.isnan(a) else a,
                }
            )
        return rows
