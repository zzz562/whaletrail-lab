# baostock 日 K 加列 + 静态快照（换手 / ST / 行业）

> 日期：2026-09-16 · 状态：代码已落地；Mac mini 全市场加列回填已完成（5219 只 / 3,348,963 行 / 2024-01-02→2026-09-15；`tradestatus IS NULL` = 0；停牌 4844 日 `turn` 空为正规）

相似选股页要用成交量，后续筹码要用换手率。baostock 同一条日 K 接口就能给，不必换源。概念板块 baostock **没有**，不接东财。

## 拉什么 / 不拉什么

| 落库 | 内容 | 接口 |
|------|------|------|
| `daily_kline` 加列 | `turn` `tradestatus` `pct_chg` `is_st` `pe_ttm` `pb_mrq` | `query_history_k_data_plus`（原 OHLCV+amount 同一调用） |
| `ashare_universe` 加列 | 上市日 / 退市日 / type / 上市状态 | `query_stock_basic`（原先只存了 code+name） |
| `ashare_industry` | 申万一级（无概念） | `query_stock_industry` |
| `ashare_index_constituents` | 上证50 / 沪深300 / 中证500 最新成分 | `query_sz50/hs300/zz500_stocks` |

不拉：季频财务、前复权第二套日 K、概念/题材、龙虎榜、筹码成品（直方图用 OHLC+turn 以后本地算）。

日 K 仍是不复权（`adjustflag=3`），和 DTW 增量一致。筹码若要前复权，另表另议。

## 旧行怎么补

SQLite `ALTER TABLE` 加列后，已有 bar 的 `tradestatus` 为 NULL。`fetch-baostock-universe.py` 把这类代码从最早缺口日重拉到今天（`INSERT OR REPLACE`）。停牌日正规填写是 `tradestatus=0` 且 `turn` 空，不会被当成缺口。`--skip-refill` 只拉新日期。

Mac mini（直连，不走代理）：

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/python scripts/fetch-baostock-universe.py
```

首次补字段 ≈ 全市场再走一遍已有日期窗口（此前约 5212 只 / 2025-01 起），耗时与当年回填同量级。静态三张表是几次全市场小查询，秒级。

## 看板

量柱 / 筹码拟合见 `notes/2026-09-16-similarity-volume-chip.md`（决策 21）。按行业或沪深300过滤仍未做 UI。
