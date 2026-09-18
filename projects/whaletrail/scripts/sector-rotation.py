#!/usr/bin/env python3
"""A股板块轮动分析（证监会行业分类口径）。

Reads ``daily_kline`` / ``ashare_industry`` / ``index_kline`` from SQLite and
builds the sector-rotation panel + charts for a given window (default 2026 YTD):

- sector_daily.csv        板块×日面板：等权/市值加权收益、成交额、占比、上涨家数比
- cum_curves.png          2026 累计收益曲线（领涨/领跌板块 vs 沪深300）
- heatmap_biweekly.png    板块×双周收益热力图（轮动节奏）
- amount_share.png        板块成交额占比走势（资金进出）
- style_indexes.png       风格：沪深300/中证500/中证1000/国证2000/创业板指
- rotation_scatter.png    RRG 风格散点：近 60 日超额 vs 近 20 日超额变化
- monthly_rank.csv        月度板块收益排名表

Runs on Mac mini (matplotlib + pandas in venv).  Read-only against the DB.

Usage:
  .venv/bin/python scripts/sector-rotation.py                     # 2026-01-01 → 最新
  .venv/bin/python scripts/sector-rotation.py --start 2026-01-01 --bench sh.000300
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "results" / "whaletrail.db"
OUT_DIR = ROOT / "results" / "rotation"

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Hiragino Sans GB",
    "Arial Unicode MS",
    "SimHei",
]
plt.rcParams["axes.unicode_minus"] = False

WARMUP_DAYS = 40  # 市值加权权重 warmup（个交易日）


def short_name(industry: str) -> str:
    """'C39计算机、通信和其他电子设备制造业' → '计算机、通信和其他电子设备制造业'."""
    s = industry
    if len(s) >= 3 and s[0].isalpha() and s[1].isdigit():
        i = 0
        while i < len(s) and s[i].isascii() and (s[i].isalpha() or s[i].isdigit()):
            i += 1
        s = s[i:]
    return s or industry


def load_panel(db: Path, start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(str(db))
    try:
        start_dt = pd.Timestamp(start) - pd.tseries.offsets.BDay(WARMUP_DAYS)
        kline = pd.read_sql_query(
            """SELECT code, trade_date, close, volume, amount, turn,
                      pct_chg, tradestatus, is_st
               FROM daily_kline WHERE trade_date >= ?""",
            conn,
            params=[start_dt.strftime("%Y-%m-%d")],
        )
        industry = pd.read_sql_query(
            "SELECT code, industry FROM ashare_industry"
            " WHERE industry IS NOT NULL AND industry != ''",
            conn,
        )
        index = pd.read_sql_query(
            "SELECT code, name, trade_date, close, pct_chg FROM index_kline",
            conn,
        )
    finally:
        conn.close()
    return kline, industry, index


def build_sector_daily(kline: pd.DataFrame, industry: pd.DataFrame, start: str) -> pd.DataFrame:
    df = kline.merge(industry, on="code", how="inner")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["code", "trade_date"])

    # 流通股本 ≈ volume/(turn/100)；停牌日 turn 为空 → 沿用上日
    fs = np.where(df["turn"] > 0, df["volume"] * 100.0 / df["turn"], np.nan)
    df["float_shares"] = pd.Series(fs, index=df.index).groupby(df["code"]).ffill()
    df["float_mcap"] = df["close"] * df["float_shares"]
    df["w_lag"] = df.groupby("code")["float_mcap"].shift(1)

    tradable = df[(df["tradestatus"] == 1) & (df["is_st"] == 0) & df["pct_chg"].notna()].copy()
    tradable["wret"] = tradable["pct_chg"] * tradable["w_lag"]

    g = tradable.groupby(["industry", "trade_date"])
    panel = pd.DataFrame(
        {
            "ew_ret": g["pct_chg"].mean(),
            "vw_ret": g["wret"].sum() / g["w_lag"].sum(),
            "amount": g["amount"].sum(),
            "adv_ratio": g["pct_chg"].apply(lambda s: (s > 0).mean()),
            "n": g["pct_chg"].size(),
        }
    ).reset_index()
    panel = panel[panel["trade_date"] >= pd.Timestamp(start)]
    mkt_amount = panel.groupby("trade_date")["amount"].sum().rename("mkt_amount")
    panel = panel.merge(mkt_amount, on="trade_date")
    panel["amount_share"] = panel["amount"] / panel["mkt_amount"]
    panel["sector"] = panel["industry"].map(short_name)
    return panel


def cum_curve(rets: pd.Series) -> pd.Series:
    return (1.0 + rets / 100.0).cumprod()


def plot_cum_curves(panel: pd.DataFrame, bench: pd.DataFrame, out: Path, top_n: int = 6, bottom_n: int = 2, min_members: int = 15) -> None:
    panel = panel[panel["n"] >= min_members]
    vw = panel.pivot(index="trade_date", columns="sector", values="vw_ret").fillna(0.0)
    cum = vw.apply(cum_curve)
    total = cum.iloc[-1].sort_values(ascending=False)
    picks = list(total.head(top_n).index) + list(total.tail(bottom_n).index)

    fig, ax = plt.subplots(figsize=(13, 7))
    for s in picks:
        ax.plot(cum.index, cum[s], lw=1.4, label=f"{s} {total[s] - 1:+.0%}")
    if not bench.empty:
        ax.plot(bench["trade_date"], bench["cum"], color="black", lw=2.2, ls="--", label=f"{bench.attrs.get('name', '基准')} {bench['cum'].iloc[-1] - 1:+.0%}")
    ax.set_title("2026 年以来板块累计收益（流通市值加权）vs 基准")
    ax.legend(fontsize=9, ncol=2)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def plot_heatmap(panel: pd.DataFrame, out: Path, min_members: int, days_per_col: int = 10) -> None:
    p = panel[panel["n"] >= min_members]
    vw = p.pivot(index="trade_date", columns="sector", values="vw_ret").sort_index()
    groups = [vw.iloc[i : i + days_per_col] for i in range(0, len(vw), days_per_col)]
    labels, cols = [], []
    for gseg in groups:
        if gseg.empty:
            continue
        labels.append(gseg.index[-1].strftime("%m-%d"))
        cols.append((1.0 + gseg / 100.0).prod() - 1.0)
    heat = pd.concat(cols, axis=1).T
    heat.index = labels
    heat = heat[[c for c in heat.columns]] * 100.0

    order = cum_curve(vw.fillna(0)).iloc[-1].sort_values(ascending=False).index
    heat = heat[order]

    fig, ax = plt.subplots(figsize=(max(12, len(labels) * 0.9), max(10, len(heat.columns) * 0.32)))
    vmax = np.nanpercentile(np.abs(heat.values), 98)
    im = ax.imshow(heat.T.values, aspect="auto", cmap="RdYlGn_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(heat.index)), heat.index, rotation=45, ha="right")
    ax.set_yticks(range(len(heat.columns)), heat.columns, fontsize=8)
    for i in range(len(heat.index)):
        for j in range(len(heat.columns)):
            v = heat.T.values[j, i]
            if not np.isnan(v):
                ax.text(i, j, f"{v:.1f}", ha="center", va="center", fontsize=6.5)
    ax.set_title("板块 × 双周收益热力图（%，市值加权；行=截至日）")
    fig.colorbar(im, shrink=0.6)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def plot_amount_share(panel: pd.DataFrame, out: Path, top_n: int = 8) -> None:
    share = panel.pivot(index="trade_date", columns="sector", values="amount_share")
    top = share.mean().sort_values(ascending=False).head(top_n).index
    ma = share[top].rolling(20, min_periods=5).mean()

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for s in top:
        axes[0].plot(ma.index, ma[s] * 100, lw=1.3, label=s)
    axes[0].set_title("板块成交额占全市场比例（20 日均，%）")
    axes[0].legend(fontsize=9, ncol=2)
    axes[0].grid(alpha=0.3)

    total = panel.groupby("trade_date")["amount"].sum() / 1e11
    axes[1].plot(total.index, total.values, color="gray", lw=1.5)
    axes[1].set_title("全市场成交额（千亿元）")
    axes[1].grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def plot_style(index: pd.DataFrame, out: Path, start: str) -> None:
    codes = ["sh.000300", "sh.000905", "sh.000852", "sz.399303", "sz.399006"]
    fig, ax = plt.subplots(figsize=(13, 6))
    for c in codes:
        sub = index[index["code"] == c].sort_values("trade_date")
        sub = sub[sub["trade_date"] >= start]
        if sub.empty:
            continue
        sub["trade_date"] = pd.to_datetime(sub["trade_date"])
        cum = (1.0 + sub["pct_chg"] / 100.0).cumprod()
        ax.plot(sub["trade_date"], cum.values, lw=1.6, label=f"{sub['name'].iloc[0]} {cum.iloc[-1] - 1:+.0%}")
    ax.set_title("2026 年以来风格指数累计收益（大盘 → 小盘/成长）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def plot_rotation_scatter(panel: pd.DataFrame, bench: pd.DataFrame, out: Path, min_members: int) -> None:
    p = panel[panel["n"] >= min_members]
    vw = p.pivot(index="trade_date", columns="sector", values="vw_ret").fillna(0.0)
    bench_s = bench.set_index("trade_date")["pct_chg"].reindex(vw.index).fillna(0.0)
    excess = vw.sub(bench_s, axis=0) / 100.0
    rs60 = (1.0 + excess.tail(60)).prod() - 1.0
    rs20 = (1.0 + excess.tail(20)).prod() - 1.0

    fig, ax = plt.subplots(figsize=(11, 9))
    ax.axhline(0, color="gray", lw=0.8)
    ax.axvline(0, color="gray", lw=0.8)
    ax.scatter(rs60 * 100, rs20 * 100, s=40, c=np.sign(rs20), cmap="RdYlGn_r", vmin=-1, vmax=1)
    for s in rs60.index:
        ax.annotate(s, (rs60[s] * 100, rs20[s] * 100), fontsize=7.5, alpha=0.85)
    ax.set_xlabel("近 60 日相对基准累计超额（%）")
    ax.set_ylabel("近 20 日超额（%）")
    ax.set_title("板块轮动象限（右上=持续领涨，右下=转弱，左上=补涨，左下=持续领跌）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def monthly_rank(panel: pd.DataFrame, min_members: int) -> pd.DataFrame:
    p = panel[panel["n"] >= min_members].copy()
    p["month"] = p["trade_date"].dt.strftime("%Y-%m")
    vw = p.pivot_table(index=["month", "sector"], values="vw_ret", aggfunc=lambda s: (1 + s / 100).prod() - 1)
    vw = (vw["vw_ret"] * 100).round(2).rename("ret").reset_index()
    vw["rank"] = vw.groupby("month")["ret"].rank(ascending=False)
    return vw.sort_values(["month", "rank"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--bench", default="sh.000300", help="benchmark index code")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--min-members", type=int, default=15)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    kline, industry, index = load_panel(Path(args.db), args.start)
    print(f"kline {len(kline):,} 行（含 warmup）· 行业映射 {len(industry):,} · 指数 {index['code'].nunique()} 条")

    panel = build_sector_daily(kline, industry, args.start)
    panel.to_csv(out / "sector_daily.csv", index=False)
    dates = sorted(panel["trade_date"].unique())
    print(f"sector_daily: {panel['sector'].nunique()} 板块 × {len(dates)} 日（{pd.Timestamp(dates[0]).date()} → {pd.Timestamp(dates[-1]).date()}）")

    bench_df = index[index["code"] == args.bench].sort_values("trade_date")
    bench_df = bench_df[bench_df["trade_date"] >= args.start].copy()
    bench_df["trade_date"] = pd.to_datetime(bench_df["trade_date"])
    bench_df["cum"] = (1.0 + bench_df["pct_chg"] / 100.0).cumprod()
    bench_df.attrs["name"] = bench_df["name"].iloc[0] if not bench_df.empty else args.bench

    plot_cum_curves(panel, bench_df, out / "cum_curves.png", min_members=args.min_members)
    plot_heatmap(panel, out / "heatmap_biweekly.png", args.min_members)
    plot_amount_share(panel, out / "amount_share.png")
    plot_style(index, out / "style_indexes.png", args.start)
    plot_rotation_scatter(panel, bench_df, out / "rotation_scatter.png", args.min_members)

    monthly = monthly_rank(panel, args.min_members)
    monthly.to_csv(out / "monthly_rank.csv", index=False)
    for m, grp in monthly.groupby("month"):
        top = grp.head(5)
        bot = grp.tail(3).iloc[::-1]
        line_top = " · ".join(f"{r.sector} {r.ret:+.1f}" for r in top.itertuples())
        line_bot = " · ".join(f"{r.sector} {r.ret:+.1f}" for r in bot.itertuples())
        print(f"{m}  领涨: {line_top}")
        print(f"{m}  领跌: {line_bot}")

    summary = {
        "window": [str(pd.Timestamp(dates[0]).date()), str(pd.Timestamp(dates[-1]).date())],
        "sectors": int(panel["sector"].nunique()),
        "bench": args.bench,
        "bench_cum": float(bench_df["cum"].iloc[-1] - 1.0) if not bench_df.empty else None,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"输出目录: {out}")


if __name__ == "__main__":
    main()
