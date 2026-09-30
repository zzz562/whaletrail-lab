# 数据资产与调度总览

本文件回答一个问题：**我们的数据从哪来、多久刷一次、落在哪、谁在用、现在有没有断**。
（口径与不变量见 `ARCHITECTURE.md`；机器与端口见 `ENVIRONMENT.md`；运维步骤见 `DEPLOY.md`。）

最后核对：2026-09-30。

## 每交易日（系统 crontab，Mac mini）

| 资产 | 来源 | 触发 | 落库 | 现在 |
|------|------|------|------|------|
| A股全市场日 K | baostock 直连 | cron 16:30 / 20:00（工作日） | `daily_kline`：不复权 OHLCV + `amount`（成交额）+ `turn`（换手）+ `tradestatus` + `pct_chg` + `is_st` + `pe_ttm` + `pb_mrq`，5,224 只 | 最新 2026-09-29 |
| 基准指数日 K | baostock 直连 | 同上 | `index_kline`：上证指数 / 深证成指 / 创业板指 / 上证50 / 沪深300 / 中证500 / 中证1000 / 国证2000（8 条），2015 起 | 最新 2026-09-29 |
| 静态表 | baostock 直连 | 同上（每班次都刷） | `ashare_universe`（5,561 只，上市 5,224）、`ashare_industry`（证监会 83 大类）、`ashare_index_constituents`（sz50/hs300/zz500，850 行） | 9-28～9-30 |
| 每日数据状态 | 本地判定 | cron 21:45（工作日） | `logs/daily-status.log` + Telegram | 每天一条 |

要点：

- **16:30 那班拿不到当天数据**：baostock 的日 K 是 17:30 入库，所以当天日线由 **20:00 那班**落库，16:30 那班负责补漏和刷静态表。
- 增量按每只代码的 `MAX(trade_date)` 起点补，失败的单只是**下一班自动重试**（不是永久缺口）；单只失败超 300 只、指数腿失败、超 90 分钟预算或硬失败都会推 Telegram。
- 断网/服务端故障时：单查询 90 秒 SIGALRM 截断 + 整轮 90 分钟预算 + 并发锁（`results/.fetch-baostock.lock`），所以不会出现「卡死几天无人知」。

## 每周（系统 crontab）

| 资产 | 来源 | 触发 | 落库 | 现在 |
|------|------|------|------|------|
| 板块/指数**配置表** | 东财 `datacenter-web` 报表 + 中证官网 closeweight xls + 国证 sample-detail xls | cron 周六 09:00 | `board_members`：1,042 个板块/指数、104,019 条成分（`concept_em` 400 / `industry_em` 496 / `region_em` 31 / `other_em` 104 / `index_csi` 7 / `index_cn` 4），带权重与生效日 | 快照 2026-09-30 |

中证权重完整（可复算指数，实测 15 日平均误差 0.02–0.03pp）；国证只给前 10 名权重，只能用它的成分表。

## 高频 / 事件驱动

| 资产 | 来源 | 触发 | 落库 | 现在 |
|------|------|------|------|------|
| 美股 5m 盘中扫描 | yfinance | launchd `ai.whaletrail-live`，每 10 分钟（仅美股时段） | `results/paper_live_state.json`（看板黄金 paper 页读它）；Telegram 推送已停用 | 运行中 |
| A股 paper + watchlist 快照 | tvscreener 直连 | OpenClaw cron `whaletrail-ashare`，工作日 15:30 | `quote_snapshots` → Telegram | 最新 2026-09-29 15:30 |

## 手动（没有排程）

| 资产 | 来源 | 触发 | 落库 | 现在 |
|------|------|------|------|------|
| **黄金/美股日线** | yfinance + Parquet 缓存 | 手动跑 `run-backtest.py` / `daily-report.sh` | `data_cache/GLD.parquet`、`SPY.parquet`、`GC=F.parquet` | **GLD/SPY 停在 9-14（16 天）**；`GC=F` 9-4 — 见下方缺口 |
| 日报 | 本地 | 手动 `scripts/daily-report.sh gold_sma GLD` | Telegram（原 OpenClaw 08:30 任务已不存在） | — |
| 板块轮动分析 | 本地算 | 手动 `scripts/sector-rotation.py` | `results/rotation/*`（CSV + PNG） | 停 2026-09-18 |
| A股历史种子 | tvdatafeed | 手动 `seed-ashare-history.py` | `quote_snapshots`（source=tvdatafeed） | 停 2026-08-13 |
| 板块/指数序列 | 本地算 | **待建**（第二步） | 计划：`board_members` × `daily_kline` | 未开始 |

## 派生数据（不抓取，按需本地计算）

| 派生 | 代码 | 输入 | 说明 |
|------|------|------|------|
| 筹码分布（CYQ 直方图） | `whaletrail/chips.py` | `daily_kline` 的 OHLC + `turn` | 需要 120–250+ 根收敛；只算当前模板/候选，**不入库**，和行情商的筹码峰不会一致 |
| 相似选股召回与打分 | `similarity.py`、`screen.py`、`context.py` | 同上 + 板块属性/相对强弱背景 | DTW 召回 → 筹码/做盘/量/箱体/均线距离；背景列不进分数 |
| 板块与指数序列 | 待建 | `board_members` + `daily_kline` | 等权/权重加权、成交额占比、上涨家数；概念有重叠，按板块独立算 |

## 常驻服务（launchd）

`ai.whaletrail-dashboard`（生产 :8766）、`ai.whaletrail-dashboard-stage`（预发 :8768）、`ai.whaletrail-live`（5m 扫描）、`ai.openclaw.gateway`（Telegram 桥）、`com.zeph.reverse-tunnel`、`com.zeph.wifi-watchdog`（3 分钟）、`homebrew.mxcl.ollama`。每日状态报表会检查前五个是否加载。

## 缺口与待办

1. **黄金/美股日线停更**：`data_cache/GLD.parquet`、`SPY.parquet` 停在 2026-09-14，`GC=F` 停在 9-4。原因是原 OpenClaw 08:30 日报任务已不在，没人再触发 yfinance 日线刷新（paper-live 只做 5m 扫描）。当前每日状态会把它列为「需处理」。要么重排一个工作日刷新的任务，要么接受手动。
2. **板块/指数序列还没算**（第二步）：配置表已入库，聚合脚本未建。
3. **概念板块只有当前成分快照**，历史回放有前视；中证按月调整、概念每周变。
4. **北交所不在库里**（`daily_kline` 只有 sh/sz），板块成分里的 `bj.` 成员（概念板块约 3.6%）在聚合时自动脱落。
5. 币圈/港股/日股按 SCOPE 不纳入；美股只做 watchlist 快照，没有全市场日线。
