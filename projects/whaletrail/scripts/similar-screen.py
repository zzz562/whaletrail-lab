#!/usr/bin/env python3
"""Screen stocks against a template window.  Same function as the 相似选股 page.

  python scripts/similar-screen.py --demo chaosheng --top 30
  python scripts/similar-screen.py --demo yuandong --recall 500 --top 20
  python scripts/similar-screen.py --symbol sz.000823 --start 2026-03-07 --end 2026-09-07

Writes a JSON blob with --json.  Observation only — not a trade list.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import to_baostock_code
from whaletrail.context import cap_text, commentary, load_index_moves, name_context, sector_moves
from whaletrail.screen import DEMOS, WEIGHT_PRESETS, screen_similar, trim_partial_session, window_brief
from whaletrail.similarity import build_scan_pool
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return None if value != value else value
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", choices=sorted(DEMOS), help="Named booklet demo")
    parser.add_argument("--symbol", help="Reference, e.g. sz.000823 or SSE:000823")
    parser.add_argument("--start", help="Template window start YYYY-MM-DD")
    parser.add_argument("--end", help="Template window end YYYY-MM-DD")
    parser.add_argument("--window", type=int, default=0, help="Template length when --start/--end are omitted")
    parser.add_argument("--recall", type=int, default=500, help="Waveform recall pool (default 500)")
    parser.add_argument("--top", type=int, default=30, help="Rows to print (default 30)")
    parser.add_argument(
        "--preset",
        choices=tuple(WEIGHT_PRESETS),
        default="偏筹码",
        help="Feature-group weights (default 偏筹码)",
    )
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--json", dest="json_path", help="Write the full hit list to this path")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    if args.demo:
        spec = DEMOS[args.demo]
        symbol, start, end = spec["symbol"], spec["start"], spec["end"]
    else:
        symbol, start, end = args.symbol, args.start, args.end
    if not symbol or bool(start) != bool(end):
        parser.error("give --demo, or --symbol with --start and --end, or --symbol with --window")
    try:
        code = to_baostock_code(symbol)
    except ValueError:
        code = symbol.strip().lower()

    recent_start = (date.today() - timedelta(days=420)).isoformat()
    repo = Repository(args.db)
    names = repo.universe_names()
    industry = repo.industry_map()
    index_moves = load_index_moves(repo.conn)
    fetch_from = recent_start if not start else min(recent_start, start)
    bars = repo.daily_bars(start=fetch_from)
    repo.close()
    bars, dropped = trim_partial_session(bars)
    if code not in bars:
        print(f"⚠️ {code} 不在 daily_kline。符号要带市场（sz.000823 / sh.600869）。")
        sys.exit(1)
    if not start:
        ref_dates = [str(d)[:10] for d in bars[code].get("trade_date") or []]
        if len(ref_dates) < args.window:
            print(f"⚠️ {code} 只有 {len(ref_dates)} 根，不足 --window {args.window}")
            sys.exit(1)
        start, end = ref_dates[-args.window], ref_dates[-1]

    built = build_scan_pool(bars, code, start, end)
    if built is None:
        print(f"⚠️ {code} 在 {start}→{end} 不足 10 根")
        sys.exit(1)
    template, pool, slice_n = built
    cand_end = max(
        (str((b.get("trade_date") or [""])[-1])[:10] for b in pool.values()),
        default="",
    )
    t0 = time.perf_counter()
    hits, info = screen_similar(
        template,
        pool,
        recall_n=args.recall,
        weights=WEIGHT_PRESETS[args.preset],
        exclude_st=not args.include_st,
    )
    elapsed = time.perf_counter() - t0
    hits = [h for h in hits if h.code != code]
    shown = hits[: args.top]
    title = names.get(code, "")
    brief = window_brief(template)
    dropped_txt = f" · 去掉未齐的 {dropped}" if dropped else ""
    print(
        f"\n🐋 相似选股 · 模板 {title} ({code}) {brief.get('first') or start}→{brief.get('last') or end}"
        f" · {slice_n} 根"
        f" · 候选各自最近 {slice_n} 根到 {cand_end or '—'}{dropped_txt}"
        f" · 召回 {args.recall} · {args.preset} · {elapsed:.2f}s"
    )
    camp = brief.get("campaign") or {}
    if camp:
        print(
            f"模板 A 峰 {camp.get('peak_date')} 高 {camp.get('peak_high'):.2f}"
            f" → 谷 {camp.get('trough_date')} 低 {camp.get('trough_low'):.2f}"
            f" · 跌 {camp.get('drop'):.1%} · {camp.get('span')} 根 · 连阴 {camp.get('run')}"
        )
    print(f"模板 {info['template_stage']}")
    wtxt = " ".join(f"{k}={v:.2f}" for k, v in info["weights"].items())
    print(f"权重 {wtxt}")
    print("属性、流通市值、对上证、对板块不进分数。体量：<30 偏小，30–100 合适，100–300 偏大，300–1000 大，≥1000 超大。")
    sectors = sector_moves(bars, industry, cand_end) if cand_end else {}
    index_day = index_moves.get(cand_end, {})

    def _ctx(code: str) -> dict:
        return name_context(code, bars.get(code) or {}, industry, sectors, index_day, cand_end)

    print(
        f"{'序':<4}{'召回':<6}{'代码':<12}{'名称':<10}{'属性':<6}{'体量':<6}{'市值':>8}"
        f"{'对上证':<6}{'对板块':<6}{'分数':>8}"
        f"{'末峰':>6}{'量比':>8}"
    )
    print("-" * 108)
    shown_ctx = []
    for i, hit in enumerate(shown, start=1):
        f = hit.features
        ctx = _ctx(hit.code)
        shown_ctx.append(ctx)

        def cell(key, nd=1, feat=f):
            val = feat.get(key)
            return "—" if val is None else f"{val:.{nd}f}"

        print(
            f"{i:<4}{hit.recall_rank:<6}{hit.code:<12}{(names.get(hit.code) or '')[:8]:<10}"
            f"{(ctx.get('board') or '—'):<6}{(ctx.get('cap_band') or '—'):<6}"
            f"{cap_text(ctx.get('float_yi')):>8}{(ctx.get('vs_index') or '—'):<6}"
            f"{(ctx.get('vs_sector') or '—'):<6}{hit.score:8.3f}"
            f"{cell('end_n_peaks', 0):>6}{cell('vol_climax', 2):>8}"
        )
    print("\n点评（不改名次）")
    tfeat = info["template_features"]
    for i, (hit, ctx) in enumerate(zip(shown, shown_ctx), start=1):
        title = names.get(hit.code) or hit.code
        print(f"\n{i}. {title} {hit.code}")
        print(commentary(hit.features, tfeat, hit.recall_rank, hit.d_kline, hit.close_corr, ctx))
    if args.json_path:
        payload = {
            "ref": code,
            "ref_name": title,
            "start": start,
            "end": end,
            "cand_end": cand_end,
            "dropped_session": dropped,
            "bars": slice_n,
            "preset": args.preset,
            "weights": info["weights"],
            "template_first": brief.get("first"),
            "template_last": brief.get("last"),
            "template_stage": info["template_stage"],
            "template_features": _jsonable(info["template_features"]),
            "template_brief": _jsonable(brief),
            "elapsed_s": round(elapsed, 2),
            "index_day": _jsonable(index_day),
            "hits": [
                {
                    "rank": i,
                    "code": h.code,
                    "name": names.get(h.code, ""),
                    "score": h.score,
                    "recall_rank": h.recall_rank,
                    "d_kline": h.d_kline,
                    "close_corr": h.close_corr,
                    "stage": h.stage,
                    "groups": _jsonable(h.groups),
                    "features": _jsonable(h.features),
                    "context": _jsonable(_ctx(h.code)),
                    "comment": commentary(
                        h.features, info["template_features"], h.recall_rank,
                        h.d_kline, h.close_corr, _ctx(h.code),
                    ),
                }
                for i, h in enumerate(hits, start=1)
            ],
        }
        Path(args.json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json {args.json_path} ({len(hits)} hits)")


if __name__ == "__main__":
    main()
