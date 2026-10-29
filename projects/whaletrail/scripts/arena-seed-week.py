#!/usr/bin/env python3
"""Replay last week's public posts into the arena so the board has real fills.

Capture time is the post time, so a shout that was already public before
09:25 can fill at that day's open. Posts after 09:25 wait for the next open.
This does not call a model. Names come from 6-digit codes and exact universe
names of at least 3 characters.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from whaletrail.arena.ledger import (  # noqa: E402
    ensure_participants,
    ingest,
    load_roster,
    load_universe,
    open_db_and_roster,
    settle_through,
)
from whaletrail.arena.paths import db_path, roster_path  # noqa: E402
from whaletrail.arena.rules import CN, parse_cn_time  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402

START = "2026-10-12T16:00:00Z"
JOINED = "2026-10-13"
CAP = 100
QUERIES = [
    '(#A股 OR A股) (复盘 OR 涨停 OR 低吸 OR 龙头) -is:retweet lang:zh',
    '(收评 OR 午评) (涨停 OR 军工 OR 龙头) -is:retweet lang:zh',
    '(明天 OR 明日 OR 开盘) (低吸 OR 上车 OR 重点) (股 OR 票) -is:retweet lang:zh',
    '(连板 OR 打板 OR 首板 OR 盲盒) (A股 OR 涨停) -is:retweet lang:zh',
]
FORWARD = ("明天", "明日", "开盘", "低吸", "马前炮", "盲盒", "上车", "重点关注")
RECAP = ("收评", "午评", "复盘", "龙虎榜", "恭喜", "落袋", "已经涨停")
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _bearer() -> str:
    spec = importlib.util.spec_from_file_location("wt_sentiment", ROOT / "scripts" / "sentiment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BEARER_TOKEN


def _xget(token: str, path: str) -> dict:
    from urllib.request import Request, urlopen

    req = Request(f"https://api.x.com{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"X {exc}")
        return {}


def search(token: str, query: str, pages: int = 2) -> list[dict]:
    found = []
    token_page = None
    for _ in range(pages):
        params = {
            "query": query,
            "max_results": 100,
            "start_time": START,
            "tweet.fields": "created_at,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,name,description",
        }
        if token_page:
            params["next_token"] = token_page
        data = _xget(token, "/2/tweets/search/recent?" + urlencode(params))
        users = {u["id"]: u for u in (data.get("includes") or {}).get("users") or []}
        for tweet in data.get("data") or []:
            user = users.get(tweet.get("author_id")) or {}
            found.append({
                "post_id": tweet["id"],
                "text": tweet.get("text") or "",
                "created_at": tweet.get("created_at"),
                "handle": (user.get("username") or "").lower(),
                "name": user.get("name") or user.get("username") or "",
            })
        token_page = (data.get("meta") or {}).get("next_token")
        if not token_page:
            break
        time.sleep(1)
    return found


def timeline(token: str, handle: str) -> list[dict]:
    user = _xget(token, f"/2/users/by/username/{handle}?user.fields=name")
    uid = (user.get("data") or {}).get("id")
    if not uid:
        return []
    params = urlencode({
        "max_results": 20,
        "start_time": START,
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,text",
    })
    data = _xget(token, f"/2/users/{uid}/tweets?{params}")
    rows = []
    for tweet in data.get("data") or []:
        rows.append({
            "post_id": tweet["id"],
            "text": tweet.get("text") or "",
            "created_at": tweet.get("created_at"),
            "handle": handle,
            "name": (user.get("data") or {}).get("name") or handle,
        })
    return rows


def find_instruments(text: str, names: list[tuple[str, str]], by_code: dict[str, str]) -> list[dict]:
    found: list[dict] = []
    seen = set()
    masked = text
    for name, code in names:
        if name and name in masked and code not in seen:
            found.append({"code": code, "name": name, "evidence": name})
            seen.add(code)
            masked = masked.replace(name, " ")
            if len(found) >= 8:
                break
    for digits in CODE_RE.findall(text):
        for cand in (f"sh.{digits}", f"sz.{digits}"):
            if cand in by_code and cand not in seen:
                found.append({"code": cand, "name": by_code[cand], "evidence": digits})
                seen.add(cand)
    return found


def stance_for(text: str, names: list[dict]) -> str:
    if len(names) > 3:
        return "list"
    if not names:
        return "ambiguous"
    recap = any(word in text for word in RECAP)
    forward = any(word in text for word in FORWARD)
    if recap and not forward:
        return "recap"
    return "forward"


def write_roster(path: Path, people: list[dict]) -> None:
    lines = [
        "# 擂台名册。入赛日之前的帖子不记成绩。",
        "# 2026-10-13 起含上周回放：截单时间用帖子自己的发布时间。",
        f'joined_on: "{JOINED}"',
        "participants:",
    ]
    for person in people:
        name = str(person["name"]).replace('"', "'")
        lines.append(f"  - handle: {person['handle']}")
        lines.append(f'    name: "{name}"')
        lines.append(f"    kind: {person['kind']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    token = _bearer()
    posts = []
    for query in QUERIES:
        batch = search(token, query)
        print(f"search {len(batch)}")
        posts.extend(batch)
    by_id = {}
    for post in posts:
        if post["handle"] and post["created_at"]:
            by_id[post["post_id"]] = post
    authors: dict[str, str] = {}
    for post in by_id.values():
        authors.setdefault(post["handle"], post["name"])
    roster = load_roster(roster_path())
    people = [{"handle": p["handle"], "name": p["display_name"], "kind": p["kind"]} for p in roster]
    have = {p["handle"] for p in people}
    for handle, name in sorted(authors.items()):
        if handle in have or handle in {"grok"}:
            continue
        people.append({"handle": handle, "name": name or handle, "kind": "kol"})
        have.add(handle)
        if len(people) >= CAP:
            break
    people = people[:CAP]
    fetched = 0
    for person in people:
        if person["kind"] != "kol" or fetched >= 25:
            continue
        owned = [p for p in by_id.values() if p["handle"] == person["handle"]]
        if owned:
            continue
        extra = timeline(token, person["handle"])
        for post in extra:
            by_id[post["post_id"]] = post
        fetched += 1
        time.sleep(0.4)
    write_roster(roster_path(), people)
    print(f"roster {len(people)} posts {len(by_id)}")

    now = datetime.now(CN)
    conn = open_db_and_roster(db_path(), roster_path(), now)
    conn.execute(
        """
        UPDATE arena_participants SET joined_on = ?
        WHERE joined_on > ? AND COALESCE(status, 'active') = 'active'
          AND handle NOT IN (SELECT DISTINCT handle FROM arena_orders)
        """,
        (JOINED, JOINED),
    )
    conn.commit()
    by_code, by_name = load_universe(conn)
    names = sorted(
        ((name, code) for code, name in by_code.items() if name and len(name) >= 3),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    is_trading = TradingCalendar().is_trading_day
    orders = 0
    kept = 0
    for post in by_id.values():
        if post["handle"] not in have or post["handle"] == "grok":
            continue
        instruments = find_instruments(post["text"], names, by_code)
        stance = stance_for(post["text"], instruments)
        use_names = instruments[:3] if stance == "forward" else []
        if stance == "list":
            use_names = []
        payload = {
            "handle": post["handle"],
            "display_name": post["name"],
            "post_id": post["post_id"],
            "created_at": parse_cn_time(post["created_at"]).isoformat(),
            "text": post["text"][:2000],
            "permalink": f"https://x.com/{post['handle']}/status/{post['post_id']}",
            "stance": stance if stance != "ambiguous" else "recap",
            "names": use_names,
            "raw": {"replay": JOINED},
        }
        if payload["stance"] == "recap":
            payload["names"] = []
        result = ingest(conn, payload, parse_cn_time(post["created_at"]), by_code, by_name, is_trading)
        if result.get("ok"):
            kept += 1
            orders += int(result.get("orders") or 0)
        else:
            print(f"skip {post['handle']} {result.get('error')}")
    row = conn.execute("SELECT MAX(trade_date) AS d FROM index_kline WHERE code='sh.000300'").fetchone()
    if row["d"]:
        from datetime import date
        settle_through(conn, date.fromisoformat(row["d"]), is_trading)
        print(f"settled through {row['d']}")
    print(f"archived {kept} new orders {orders}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
