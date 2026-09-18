# Architecture — 代码逻辑不变量

> 本文档记录代码层面"不能违反"的约定，避免跨会话实现冲突。业务边界见 SCOPE.md，运行环境见 ENVIRONMENT.md。

## 模块地图

```
whaletrail/
├── data/          DataLayer 门面 + yfinance(历史日线) + intraday(5m/10m/1h) + tvscreener(快照) + baostock(A股全市场日K+静态+基准指数日K) + Parquet 缓存 + watchlist + A股交易日历(trading_calendar)
├── engine/        事件驱动回测（Backtester / Broker / Account / Clock / Event）+ 实时交易时段检查（session）
├── strategy/      策略基类 + 注册表 + 6 个策略
├── metrics/       收益/回撤/夏普/胜率/盈亏比（FIFO 计算 PnL）
├── reporting/     watchlist Markdown 报表
├── storage/       SQLite（runs / trades / snapshots / daily_kline / index_kline / ashare_*）
├── similarity.py  收盘 DTW 召回 + 换手 L1/筹码 EMD 重排（retrieve_rank；rank_multi 仍保留）
├── chips.py       窗口内 CYQ 直方图 + 1 维 Wasserstein
└── indicators.py  共享指标（sma / atr / cross_signal）
```

## 数据流（定稿）

```
yfinance ─► YFinanceSource ─► ParquetCache(data_cache/) ─► Backtester ─► results/*.json
                                                               │             └► SQLite whaletrail.db
                                                               ├─ dashboard.py (:8766)
                                                               └─ daily-report.sh ─► analyze.py ─► Telegram

tvscreener ─► TVScreenerSource ─► quote_snapshots ─► build_daily_history ─► ashare-paper.py
                                          └────────► watchlist_report.md

baostock  ─► BaostockSource ─► daily_kline / index_kline / ashare_universe / ashare_industry / ashare_index_constituents
                                          └────────► dashboard 相似选股（daily_bars → retrieve_rank：K 召回，量/筹重排）
```

## 数据层组合

数据源按角色组合，不做朴素 fallback：

| 用途 | 主源 | 说明 |
|------|------|------|
| 历史日线（回测） | yfinance + Parquet 缓存 | tvscreener 不提供历史；缓存做覆盖检查 + 头尾补缺口，减少 yfinance 配额 |
| intraday 历史（5m/10m/1h 回测） | yfinance + Parquet 累积 | `data/intraday.py`；yfinance 5m 上限 60 天，缓存 key `<symbol>_<interval>` 跨窗口累积；10m 由 5m 重采样 |
| 实时快照 / watchlist / A股 paper | tvscreener | `get_quotes`；快照积累进 `quote_snapshots`，经 `build_daily_history` 生成日线 |
| A股全市场日 K / 相似选股 / 行业 / 基准指数 | baostock | `daily_kline`（不复权 OHLCV + turn/ST/PE/PB）；`ashare_universe`；证监会行业分类（83 大类，非申万）；sz50/hs300/zz500；`index_kline` 8 条基准指数日 K。无概念板块。Mac mini 直连，工作日 16:30/20:00 cron 例行 |
| paper-live 5m 信号 | yfinance | 实时扫描仍走 yfinance（`intraday.fetch_bars`，不写缓存） |

入口：`whaletrail/data/layer.py` 的 `DataLayer`。快照源的 yfinance fallback 是待办（需 tv/yahoo 符号映射）。

A 股低频率 paper 入口：`scripts/ashare-paper.py`（SMA 20/50 昨收信号 + 成本/涨跌停/T+1 模型 + 跟庄量价代理，状态存 `results/ashare_paper_state.json`）。

## 引擎不变量（重要）

1. **无前视偏差顺序**（`backtester._process_bar`；引擎按 bar 驱动、周期无感，日线与 intraday 同一套语义）：
   1. 成交上一根 bar 的挂单（按本 bar open）
   2. 喂本 bar 给策略（新订单进 `pending_orders`）
   3. 挂单提交给 broker，下一根 bar 执行
   4. 记录本 bar equity 快照
2. **市场单按 open 成交**；限价单在 bar 的 high/low 范围内按限价成交。
3. **全额成交**（无部分成交）；委托数量向下取整到整股；佣金 `max(0.0005 * qty * price, 0)`，无滑点。订单单根 bar 有效：本 bar 未成交的挂单由 `broker.cancel_unfilled()` 作废，不会滞留后续 bar 按过时价格成交。
4. **Account**：`equity = cash + Σ(position qty * latest_price)`；买扣现金，卖加现金，佣金累加 `total_commission`。持仓估值以 `last_prices` 缓存兜底（apply_fill / mark_prices 记录），价格表缺 symbol 时按最后已知价估值，不会静默从净值中消失。
5. **数据源协议**：`get_daily(symbol, start, end) -> DataFrame`，列 `open/high/low/close/volume`，索引 `DatetimeIndex` 升序、tz-naive。

## 策略

- 基类 `Strategy`：实现 `on_bar`；用 `buy` / `sell` / `order_target_percent` 下单；`pending_orders` 每 bar 被引擎清空。
- 注册表 `strategy/registry.py` 是**唯一**策略来源：`STRATEGY_CLASSES`（回测用）+ `_build_signal_registry()`（paper-live 信号用）。新增策略两处都要登记。
- `gold_sma`：SMA 20/50；金叉 → 目标仓位 80%；死叉 → 0%。

## Live paper 不变量

- `paper-live.py` 只在美股交易时段（Mon–Fri 09:30–16:00 ET，`engine/session.py`）扫描。信号周期 5m（`INTERVAL` 常量，10m 由 5m 重采样），只用**已完成 bar**（剔除成型中的当根 bar），按**现价**（≈下一根 bar 开盘）记账——与回测 `--interval 5m` 的"bar N 收盘出信号 → bar N+1 开盘成交"同构。数据须过 `validate_bars`（bar 数 ≥260 / 非正价 / 单 bar >25% 异动）与跨标的同价检测，不合格不出信号。paper 仓位与 ATR 止损按 `symbol|strategy` 隔离（`strategy/base.py` 的 `position_key`），旧 state 加载时自动迁移。
- `ashare-paper.py` 只在 A 股交易日运行：交易日判断走深交所官方日历（`data/trading_calendar.py`，覆盖周末/节假日/调休，缓存于 `data_cache/`，断网回退周一~周五），时段窗口 09:30–16:00 CST（`engine/session.py` 的 `ashare_hours`）。15:30 CST 的 cron 在此窗口内。信号用截至昨日的日线、成交记今日收盘价；成本模型为佣金万2.5（¥5 底）+ 卖出印花税 0.05% + 单边滑点 0.1%；涨跌停封板（创业板/科创板 20%、主板 10%）无法成交则挂单顺延；T+1；整手 100 股、每笔名义 ¥5 万。

## 标的与市场边界

- `parse_symbol`（`data/symbols.py`）是 yfinance 回测标的守门人：**拒绝** A 股（`\d{6}.(SH|SZ)`）和港股（`\d{1,5}.HK`）。这只约束 yfinance 历史回测路径。
- A 股低频率 paper 走 tvscreener 快照积累（`quote_snapshots`），不走 yfinance；不要把 A 股 yahoo 代码接进 yfinance 回测。
- 港股暂不纳入。

## 缓存

- `ParquetCache`：`data_cache/<symbol>.parquet`，按日期 upsert（新行覆盖旧行）；文件名字符 `/`、`\`、`:` 替换为 `_`。读写均过 `price_scale_violation` 中位价区间校验（`PRICE_BOUNDS`，防 GLD/GC=F 张冠李戴）；存量缓存用 `scripts/verify-cache.py` 审计（`--drop-invalid` 清理）。
- 参数稳健性工具：`scripts/param-sweep.py`（fast×slow 网格 + B&H 基准，默认 2011 起含黄金熊市；结果只写 JSON，不进 runs 表）。
- yfinance 的 `GC=F` 等期货用 `auto_adjust=False`，其余 `auto_adjust=True`。

## 存储

- SQLite `results/whaletrail.db`（WAL）：`runs`、`trades`、`portfolio_snapshots`、`quote_snapshots`、`daily_kline`、`index_kline`、`ashare_universe`、`ashare_industry`、`ashare_index_constituents`。
- `runs.symbols`、`metrics_json`、`positions_json`、`raw_json` 为 JSON 文本。
- 回测结果 JSON：`trades[]`（含 FIFO 计算的 `pnl`）、`equity_curve[]`、`final_equity`、`total_commission`、`total_return`、`metrics`、`strategy`、`symbol`、`role`、`market`、`start`、`end`。

## 指标约定

- `calculate_metrics`：默认 252 交易日年化，intraday 由调用方传 `periods_per_year`（5m = 252×78）；无风险利率 2%；夏普 / 回撤 / 胜率 / 盈亏比。
- `compute_trade_pnl`：FIFO 配对卖单与最早买单，卖单带 `pnl` / `pnl_per_share`。
- `indicators` 另提供 `volume_zscore` / `is_breakout` / `whale_flag`：跟庄量价代理（放量 z≥2 + 20 日收盘新高），ashare-paper 观察用——是价量趋势代理，不是大单/龙虎榜数据。
