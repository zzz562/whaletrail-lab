#!/usr/bin/env python3
"""WhaleTrail Sentiment — X/Twitter gold KOL sentiment scoring.

Fetches latest tweets from gold KOLs, scores them with DeepSeek V4 Flash
(primary, batched) with a local Ollama qwen3:4b fallback, and aggregates into a
daily gold sentiment index.

Usage:
  python scripts/sentiment.py                        # scan all gold KOLs
  python scripts/sentiment.py --account PeterLBrandt # single account

Requires: TWITTER_BEARER_TOKEN env var (has default); DEEPSEEK_API_KEY env var
or the OpenClaw gateway service env file for the primary scorer.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Config ───────────────────────────────────────────────────────
BEARER_TOKEN = os.environ.get(
    "TWITTER_BEARER_TOKEN",
    "AAAAAAAAAAAAAAAAAAAAADgc%2FAEAAAAAD9qWDxYIkEWNKx1ZJgdjh0hcaOM%3DiWf0oM3TXl0JStLtXWP5ay2QIG3xEJoC7WCrcrEXEFVuDnRzwm",
)
OLLAMA_MODEL = "qwen3:4b"
# Service addresses: WT_* env vars override the Mac mini defaults.
# See docs/ENVIRONMENT.md "配置项（环境变量）".
OLLAMA_URL = os.environ.get("WT_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
# deepseek-chat resolves to deepseek-v4-flash (non-reasoning) on the API.
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
BATCH_SIZE = 25
RESULTS_DIR = ROOT / "results"
STATE_FILE = RESULTS_DIR / "sentiment_state.json"
PROXY = os.environ.get("WT_PROXY_URL") or os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7890"

os.environ.setdefault("HTTPS_PROXY", PROXY)

# Gold KOLs from WHALE_WATCH.md section 1
GOLD_KOLS = [
    "PeterLBrandt", "LukeGromen", "SantiagoAuFund", "KitcoNewsNOW",
    "GoldPredictors", "KobeissiLetter", "DonDurrett", "TheDailyGold",
    "badcharts1", "KimbleCharting", "GoldSilver_com", "TheGoldAdvisor",
    "Oliver_MSA", "GoldCore", "spotgoldprice", "goldminingnews",
    "SWGoldReport", "Huanusa",
]

SCORING_PROMPT = (
    "Classify gold sentiment: {tweet}\n"
    "Reply one line: SCORE: bullish|bearish|neutral CONFIDENCE: 1-5"
)


# Transport / HTTP failures for the current scan. Cleared at scan() start.
_x_errors: list[str] = []


# ── X API helpers ────────────────────────────────────────────────
def x_get(path: str) -> dict:
    """Make an X API v2 GET request. Empty dict on failure; records _x_errors."""
    url = f"https://api.x.com{path}"
    req = Request(url, headers={"Authorization": f"Bearer {BEARER_TOKEN}"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        _x_errors.append(str(e))
        print(f"  ⚠️ X API error: {e}")
        return {}


def get_user_id(username: str, cache: dict) -> Optional[str]:
    """Resolve username → user ID, with local cache."""
    if username in cache:
        return cache[username]
    data = x_get(f"/2/users/by/username/{username}")
    uid = data.get("data", {}).get("id")
    if uid:
        cache[username] = uid
    return uid


def get_recent_tweets(user_id: str, count: int = 5) -> list[dict]:
    """Fetch latest tweets for a user."""
    data = x_get(
        f"/2/users/{user_id}/tweets"
        f"?max_results={count}&tweet.fields=created_at,text"
        f"&exclude=retweets,replies"
    )
    return data.get("data", [])


# ── Scoring ──────────────────────────────────────────────────────
def _deepseek_api_key() -> Optional[str]:
    """Resolve the DeepSeek API key from env or the gateway service env file."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env_file = Path(
        os.environ.get(
            "WT_OPENCLAW_ENV_FILE",
            str(Path.home() / ".openclaw/service-env/ai.openclaw.gateway.env"),
        )
    )
    try:
        for line in env_file.read_text().splitlines():
            m = re.match(r"export DEEPSEEK_API_KEY='(.*)'", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


def _parse_score(raw: str) -> dict:
    """Parse a one-line score reply into score/confidence/keyword."""
    score_match = re.search(r"SCORE:\s*(bullish|bearish|neutral)", raw, re.IGNORECASE)
    conf_match = re.search(r"CONFIDENCE:\s*(\d)", raw)
    kw_match = re.search(r"KEYWORD:\s*(\S+)", raw, re.IGNORECASE)
    return {
        "score": score_match.group(1).lower() if score_match else "neutral",
        "confidence": int(conf_match.group(1)) if conf_match else 3,
        "keyword": kw_match.group(1).lower() if kw_match else "general",
        "raw": raw[:200],
    }


def score_batch_deepseek(items: list[dict]) -> list[dict]:
    """Score a batch of tweets with DeepSeek V4 Flash (JSON output)."""
    key = _deepseek_api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not available")

    prompt_lines = [
        "Classify each tweet's gold-market sentiment.",
        "score must be one of: bullish, bearish, neutral.",
        "confidence must be an integer from 1 (low) to 5 (high).",
        'Return a JSON object with key "results": an array of objects with keys index, score, confidence.',
        'Example shape: {"results":[{"index":0,"score":"bullish","confidence":5}]}',
        "Tweets:",
    ]
    for i, it in enumerate(items):
        prompt_lines.append(f"{i}: {it['text'][:300]}")
    prompt = "\n".join(prompt_lines)

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64 * len(items) + 64,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    results = parsed.get("results", [])
    return [r for r in results if isinstance(r, dict)]


def score_tweet_ollama(text: str) -> dict:
    """Score a single tweet with local Ollama (fallback)."""
    prompt = SCORING_PROMPT.format(tweet=text[:500])
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    req = Request(
        OLLAMA_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    # Ollama puts chain-of-thought in `thinking`; only the final answer lands in
    # `response`. Do not cap num_predict, which truncates before the answer.
    raw = (data.get("response") or "").strip()
    return _parse_score(raw)


def score_items(items: list[dict]) -> list[dict]:
    """Score new tweets: DeepSeek batched first, Ollama per-tweet fallback."""
    if not items:
        return []

    try:
        out = []
        for i in range(0, len(items), BATCH_SIZE):
            chunk = items[i:i + BATCH_SIZE]
            rows = score_batch_deepseek(chunk)
            by_index = {r.get("index"): r for r in rows}
            for j, it in enumerate(chunk):
                r = by_index.get(j, {})
                out.append(_parse_score(
                    f"SCORE: {r.get('score', 'neutral')} "
                    f"CONFIDENCE: {r.get('confidence', 3)}"
                ))
        print(f"   scored {len(out)} tweets via DeepSeek V4 Flash")
        return out
    except Exception as e:
        print(f"   ⚠️ DeepSeek scoring failed ({e}); falling back to Ollama")

    out = []
    for it in items:
        out.append(score_tweet_ollama(it["text"]))
    print(f"   scored {len(out)} tweets via Ollama fallback")
    return out


# ── Main ─────────────────────────────────────────────────────────
def scan(args) -> dict:
    """Scan gold KOLs and return sentiment report."""
    _x_errors.clear()

    # Load state
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            pass

    user_cache = state.get("user_cache", {})
    seen_tweets = set(state.get("seen_tweets", []))
    today = date.today().isoformat()

    # Filter KOLs if --account specified
    kols = [args.account] if args.account else GOLD_KOLS

    # Collect new tweets first, then score in batches
    new_items = []
    for username in kols:
        print(f"\n🔍 @{username} …", end=" ", flush=True)
        uid = get_user_id(username, user_cache)
        if not uid:
            print("no user ID")
            continue

        tweets = get_recent_tweets(uid, count=5)
        if not tweets:
            print("no tweets")
            continue

        fresh = [tw for tw in tweets if tw["id"] not in seen_tweets]
        print(f"{len(tweets)} tweets, {len(fresh)} new")
        for tw in fresh:
            new_items.append({
                "account": f"@{username}",
                "tweet_id": tw["id"],
                "tweet_text": tw["text"],
                "created_at": tw.get("created_at", ""),
                "text": tw["text"],
            })
            seen_tweets.add(tw["id"])

    # Score all new tweets (batched)
    scored = score_items(new_items)

    entries = []
    scores = {"bullish": 0, "bearish": 0, "neutral": 0}
    for it, s in zip(new_items, scored):
        s["account"] = it["account"]
        s["tweet_id"] = it["tweet_id"]
        s["tweet_text"] = it["tweet_text"]
        s["created_at"] = it["created_at"]
        entries.append(s)
        scores[s["score"]] += 1

        emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➖"}[s["score"]]
        print(f"  {emoji} {s['score']} c={s['confidence']} kw={s['keyword']}")

    # Aggregate index: (+1*bullish -1*bearish) / total
    total = sum(scores.values())
    gsi = round((scores["bullish"] - scores["bearish"]) / max(total, 1), 3)

    fetch_failed = bool(_x_errors) and total == 0
    report = {
        "date": today,
        "gold_sentiment_index": gsi,
        "bullish_count": scores["bullish"],
        "bearish_count": scores["bearish"],
        "neutral_count": scores["neutral"],
        "total_scored": total,
        "entries": entries,
        "scanned_kols": len(kols),
        "fetch_errors": len(_x_errors),
        "fetch_failed": fetch_failed,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS_DIR / f"sentiment_{today}.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    if fetch_failed:
        print(
            f"\n⚠️ X API failed for this run ({len(_x_errors)} errors, 0 scored). "
            "Not replacing sentiment_latest.json."
        )
        return report

    state["user_cache"] = user_cache
    state["seen_tweets"] = list(seen_tweets)[-5000:]  # keep last 5000
    STATE_FILE.write_text(json.dumps(state, indent=2))

    # Only promote latest when we actually scored tweets. A quiet day
    # (0 new tweets, no transport errors) must not flash GSI to 0.
    if total > 0:
        latest = RESULTS_DIR / "sentiment_latest.json"
        latest.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("   no new tweets scored; leaving sentiment_latest.json unchanged")

    return report


def main():
    import argparse

    p = argparse.ArgumentParser(description="WhaleTrail Sentiment Scanner")
    p.add_argument("--account", help="Scan a single X account")
    args = p.parse_args()

    print(f"🐋 WhaleTrail Sentiment | {date.today().isoformat()}")
    print(f"   KOLs: {len(GOLD_KOLS) if not args.account else 1}")

    report = scan(args)
    gsi = report["gold_sentiment_index"]
    label = (
        "🟢 bullish" if gsi > 0.15
        else "🔴 bearish" if gsi < -0.15
        else "🟡 neutral"
    )
    print(f"\n{'='*50}")
    print(f"  Gold Sentiment Index: {gsi:.3f}  {label}")
    print(f"  Bullish: {report['bullish_count']}  "
          f"Bearish: {report['bearish_count']}  "
          f"Neutral: {report['neutral_count']}")
    print(f"  Saved: results/sentiment_{report['date']}.json")
    if report.get("fetch_failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
