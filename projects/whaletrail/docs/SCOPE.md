# WhaleTrail Scope — 基建定界

> 更新：2026-09-16

## 一句话

**低频率 paper trading：黄金主线，美股指数辅助对冲，A 股 watchlist 观察并逐步纳入低频率 paper；不做高频。**

## In scope

| 层级 | 内容 |
|------|------|
| 主资产 | 黄金：`GLD`（首选）、可选 `IAU`/`GC=F`/`SLV` |
| 辅资产 | 美股指数/个股：`SPY`、`QQQ`、`AAPL` 等 |
| A股（低频率） | tvscreener 快照积累；watchlist 观察为主，逐步纳入低频率 paper |
| 数据 | 日线 OHLCV（yfinance + Parquet 缓存）+ tvscreener 快照 |
| 引擎 | 事件驱动回测、模拟佣金（美股风格） |
| 策略 | gold_sma 为主；bollinger/turtle/momentum/ma_cross 对照 |
| 交付 | CLI、Streamlit 看板（`docs/DASHBOARD.md`）、Telegram 日报 cron |

## Out of scope

- 港股（暂不纳入）
- 分钟线 / tick / 高频
- 实盘下单（暂无交易账号）
- LEAN / Docker / 东方财富爬虫

## 数据流（定稿）

```
yfinance ──► ParquetCache ──► Backtester ──► results/*.json
                                      │
                                      ├── dashboard.py (:8766)
                                      └── daily-report → Ollama → Telegram
```

## 默认参数

| 项 | 默认 |
|----|------|
| 主标的 | `GLD` |
| 主策略 | `gold_sma` |
| 对冲对照 | `SPY` / `QQQ` |
| 回测区间 | 2018-01-01 → 近端 |
| 初始资金 | 100_000 USD |
| 佣金 | 5 bps，无最低 5 元 |

## 决策记录

1. 放弃 A股：akshare/东财不稳定，且策略重心不在 A股。
2. 放弃港股：同上，减少分叉。
3. 黄金用 `GLD` 而非 `GC=F`：ETF 连续日线更稳，paper 更友好。
4. 美股保留：作为对冲与相对强弱，不是主战场。
5. A 股纳入低频率 paper trading 目标（2026-08-13）：数据走 tvscreener 快照积累成日线，不走 yfinance 历史回测；稳定性受限，不追求高频。
6. live paper 增加交易时段检查（2026-08-17）：`paper-live.py`（美股）与 `ashare-paper.py`（A股）此前不检查营业时间，周末也会扫描并更新 paper 仓位。现由 `whaletrail/engine/session.py` 统一门禁：美股 Mon–Fri 09:30–16:00 ET + K 线当日新鲜度兜底（覆盖节假日）；A股交易日+时段窗口。
7. A股节假日走深交所官方日历（2026-08-17）：周末排除无法覆盖十一/春节等长假。`ashare-paper.py` 交易日判断改用深交所官方接口 `whaletrail/data/trading_calendar.py`（含调休），缓存 `data_cache/trading_calendar_cn.txt`、按月增量拉取；未公告月份与断网时回退周一~五判断。美股已由 paper-live 的 K 线新鲜度兜底，无需日历。
8. ~~Live 信号统一为已收盘日线~~（2026-08-18，已被决策 13 推翻）：曾把 paper-live 改成日线信号、按当日开盘价记账。
9. 回测数据加价格量纲门禁（2026-08-18）：发现 GLD 回测结果的价格是 GC=F 量级（2018 年 $1227–1340，真实 GLD 为 $113–131），缓存张冠李戴。`whaletrail/data/cache.py` 按 symbol 校验中位价区间（`PRICE_BOUNDS`），读写双向拦截；`scripts/verify-cache.py` 在 Mac mini 审计存量缓存并可用 `--drop-invalid` 清理。
10. A 股 paper 补交易规则与成本（2026-08-18）：信号=昨收、成交=今收（消除"信号价即成交价"的前视）；佣金万2.5（¥5 底）+ 卖出印花税 0.05% + 单边滑点 0.1%；涨跌停封板无法成交、挂单顺延（创业板/科创板 20%，主板 10%）；T+1；整手 100 股、每笔名义 ¥5 万。代码：`scripts/ashare-paper.py`。
11. Live 簿记按 (symbol, strategy) 隔离 + 行情质检（2026-08-18）：多策略此前共用 `positions["GLD"]` 互相踩踏，gold_sma_v2 的 ATR 止损被污染。仓位/止损 key 统一为 `symbol|strategy`（`whaletrail/strategy/base.py position_key`），旧 state 加载时自动迁移。行情经 `validate_daily`（bar 数 / 非正价 / 单日 >25% 异动）+ 跨标的同价检测，不合格不出信号。代码：`scripts/paper-live.py`。
12. 参数稳健性用网格验证（2026-08-18）：SMA 20/50 是否过拟合，用 `scripts/param-sweep.py` 的 fast×slow 网格 + B&H 基准判断：邻域成片为高原则可信，孤峰则过拟合。默认区间 2011 起，把 2011–2015 黄金熊市纳入样本。sweep 结果不写入 runs 表。
13. Live 保持 5m/10m 盯盘，回测侧对齐 intradaily（2026-08-18，推翻决策 8）：日线化损失了盘内响应，改为让回测验证"正在跑的策略"。回测引擎改为按 bar 驱动、周期无感（`whaletrail/engine/backtester.py`，订单仍在下一根 bar 开盘成交，防前视不变）；`run-backtest.py`/`param-sweep.py` 支持 `--interval 5m|10m|15m|30m|1h`；intraday 数据走 `whaletrail/data/intraday.py`（yfinance 5m 上限 60 天，Parquet 缓存跨窗口累积；10m 由 5m 重采样）；metrics 年化按 bar 周期数折算。live 信号只用已完成 bar、按现价（≈下一根 bar 开盘）记账。注意：5m 参数（SMA20/50 等）本身未经优化，先用 param-sweep --interval 5m 体检再谈信任。
14. 5m live 面板降级为观察信号（2026-08-18）：`param-sweep --interval 5m` 全网格 0/35 跑赢 B&H、中位 Sharpe -1.24，5m 快速交叉无正期望、佣金磨损严重（40 日 $2.7k/10 万）。扫描与推送保留，但 Telegram/看板统一标记"🔎 观察（勿跟单）"（`paper-live.py OBSERVATION_ONLY`），不作为进场依据；找到 5m 正期望参数前不恢复 paper 跟单。日线层面另注：gold_sma 20/50 在 2011→今也跑输 B&H（+80.6% vs +193.8%，Sharpe 0.23 vs 0.38），其价值在压回撤（-16.6% vs -45.6%），参数稳健性不足（3/35 胜出且散点分布），是否继续作为主策略待复审。
15. 运行面诚实化（2026-08-31）：看板改 launchd `ai.whaletrail-dashboard`（重启自愈，不再手动 nohup）；`daily-report.sh` 结束日改为当天，并打印 GLD/SPY 买入持有对照（冻结的 `2026-08-12` 会让日报变成旧回测复印件）；`sentiment.py` 在 X API 全失败或 0 条新评分时不覆盖 `sentiment_latest.json`；`ashare-paper.py` 对缺 `qty` 的旧 LONG 按 ¥5 万名义补齐手数。代码：`scripts/daily-report.sh`、`scripts/sentiment.py`、`scripts/ashare-paper.py`、`scripts/ai.whaletrail-dashboard.plist`。
16. 不做大而全平台（2026-08-31）：产品形状收成两本薄账——黄金日线（GLD 策略 vs B&H vs SPY 对照 + 情绪天气）和 A 股 8 标的 15:30 paper。不扩市场、不扩到 100 KOL、不恢复 5m 跟单。`gold_sma` 是否替换仍按决策 14 的网格标准，不在这次改。
17. A股相似选股（DTW）+ baostock 全市场日线（2026-09-01）：移植 ValarmClub 的「找相似走势」到 WhaleTrail（`whaletrail/similarity.py`，纯 NumPy 重写，不引入 dtaidistance），看板新增「🔍 相似选股」页（`scripts/dashboard.py`）。数据源用 baostock（免费无 token、国内直连，非 akshare/东财，不推翻决策 1），全市场日线落 SQLite `daily_kline`（`whaletrail/data/baostock_source.py` + `scripts/fetch-baostock-universe.py`）。定位是**读线**：形态筛选 + 观察，不是交易信号、不扩交易范围（决策 16 的两本薄账不变），与 `whale_flag`（量价异常）叠加使用；tvscreener 快照路径（决策 5）继续承担 8 只 paper。详见 `notes/2026-09-01-ashare-similarity-dtw.md`。
18. 看板收成四页（2026-09-02）：Streamlit 是人看的只读 UI，侧栏仅 **Paper** / **相似选股** / **KOL 评测** / **跟庄复盘**。Telegram / OpenClaw 不走主路径（进程不停）。**跟庄 ≠ KOL**：跟庄只表示现有 watchlist 上的标签「观察 / 接近 / 触发」，用已收盘日 K，不是当日阴、不是左压，不与 KOL 混页；KOL 评测是 A 股荐股推文 vs 事后对照（18 账号冻结），文案不称跟庄。Paper 黄金账是 GLD `gold_sma` vs 买入持有 vs SPY；`gold_sma` 弱于 B&H，价值在压回撤；5m/live 仅观察；A 股 paper 是 15:30 日频。GLD / GC=F 只作监控/对照，须标「不是银行牌价 / 不是纸黄金账」。A 股阴线高低点用 baostock 复权日 K、仅 watchlist；tvscreener 是快照，不是已完成日线；交易日历 = 深交所官方。**仍未决（本次不改、不假装已定）：** 纸黄金独立 paper 的数据源与日切；`watchlist.yaml`；`yin-right.json`（不新建）。代码：`scripts/dashboard.py`、`docs/DASHBOARD.md`。
19. 看板收成五页（2026-09-03）：主区 `st.tabs`（非侧栏）名字严格为 **黄金 Paper** / **A股 Paper** / **相似选股** / **KOL 评测** / **跟庄复盘**；同一 URL，不按 UA 分端。黄金账 = GLD 日线 `gold_sma` vs 买入持有 vs SPY；金价对照 = GC=F 日线。两份 yfinance Parquet 不得混用，禁止把 GC=F 价格写入 GLD 缓存。黄金两列日历 = 美股交易日，不是北京银行日切，不是深交所。A股 Paper = 仅 15:30 paper 账（tvscreener 快照 + 深交所日历）。「观察 / 接近 / 触发」只留在跟庄复盘（baostock 复权日 K、仅现有 watchlist）。纸黄金 / AU9999 本轮无源、不上板、不冒充。GLD / GC=F 文案须标「不是银行牌价 / 不是境内可玩」。**仍未决（不假装已定）：** 纸黄金独立 paper 的数据源与日切；`watchlist.yaml`；`yin-right.json`（不新建）。代码：`scripts/dashboard.py`、`docs/DASHBOARD.md`。
20. baostock 日 K 加列 + 静态快照（2026-09-16）：相似选股仍走 baostock，不接东财/akshare/Tushare。`daily_kline` 在 OHLCV/amount 之外补 `turn`（换手）、`tradestatus`、`pct_chg`、`is_st`、`pe_ttm`、`pb_mrq`，供后续量柱/筹码（本地用 OHLC+turn 算）/ST·估值过滤；同脚本落 `ashare_universe`（上市日/退市/状态）、`ashare_industry`（**仅申万一级**，无概念板块）、`ashare_index_constituents`（上证50/沪深300/中证500）。季频财务、前复权第二套日 K、概念/龙虎榜本次不拉。旧行 `tradestatus IS NULL` 视为缺字段，拉取脚本自动从缺口日重拉（`--skip-refill` 可关）。代码：`whaletrail/data/baostock_source.py`、`whaletrail/storage/schema.py`、`scripts/fetch-baostock-universe.py`。详见 `notes/2026-09-16-baostock-kline-extras.md`。
21. 相似选股三通道拟合（2026-09-16）：K 线 DTW + 换手对齐 L1 + 窗口内本地 CYQ（Wasserstein）按全市场分位加权，默认 0.50/0.30/0.20，看板可调；缺换手则关掉筹码通道。量不用无约束 DTW（会把不同日的放量尖峰拉成一样）。仍是读线观察，不扩可交易名单，不拉东财筹码、不复权第二套日 K、不落筹码表。代码：`whaletrail/chips.py`、`whaletrail/similarity.py`（`rank_multi`）、`scripts/dashboard.py`、`scripts/ashare-similar.py`。详见 `notes/2026-09-16-similarity-volume-chip.md`。
22. 相似选股改召回–重排（2026-09-16）：不再三通道直接加权。全市场只按 K 线 DTW 召回（默认 80），再在召回池内按换手 L1 + 筹码 EMD 分位重排（默认量 0.60 / 筹 0.40）。K 线不像的票进不了结果，避免茅台因筹码形状混进紫金邻居。看板对照改为点选一只，不再 Top5 三张通铺。代码：`retrieve_rank`。仍观察、不扩名单。
23. 精排默认按远东×斯迪克标定（2026-09-16）：正向案例 `sh.600869` 远东股份 vs `sz.300806` 斯迪克（2026-02-25–08-26）。K 线 DTW 第 55、换手 L1 在召回池偏弱、筹码 EMD 前 16%。量 0.60/筹 0.40 精排第 35；改为筹 0.65/量 0.35 进前 20。默认精排偏筹码，看板提供偏筹码/均衡/偏换手三档。表增列收盘相关。代码：`DEFAULT_RANK_WEIGHTS`、`RANK_PRESETS`。
24. 相似选股候选股改看最近窗（2026-09-17，修正 a98d70c 的「同一日期窗」）：圈定日期此前同时切模板和全市场，于是问的是「2025-12~2026-05 谁长得像超声电子」，与「现在谁长得像它大涨前」无关。实测 `sz.000823` 2025-12-02～2026-05-21（111 根）那次：把候选换成各自最近 111 根后，原榜前 20 掉到 86~5070 名（5188 只），榜面全换；用最近窗重算的榜（金发拉比/昭衍新药/飞龙股份…）与看板重跑一致。现约定：**标记窗口只取模板**（demo 波形/筹码），**候选取各自最近 N 根**（N=模板根数，尾部=各自最新交易日），入池门槛改为「最近序列 ≥ 85% N」——此前要求模板窗内满 bar，大涨后才上市的票被整批丢掉，现在能进池。看板文案、1v1 对照标题、`logs/similar-scan.log` 新增 `cand_end` 同步。代码：`whaletrail/similarity.py`（`build_scan_pool`/`slice_by_dates`/`tail_bars`）、`scripts/dashboard.py`。CLI `scripts/ashare-similar.py` 走同一个 `build_scan_pool`：`--start/--end` 给历史模板窗，不给时取参考股最近 `--window` 根；老窗口同样只为参考股深取历史。两入口同窗实测排名逐只一致（`scripts/check-similar-parity.py` 可复跑）。定位不变：读线观察，不扩可交易名单。

## 决策记录规范

- 每个重大决策写一条，带日期；一句话说清"定了什么、为什么、影响哪里"。
- 决策若由代码执行，注明代码位置（如 `whaletrail/engine/broker.py`），不要重复抄数字。
- 新会话改业务边界前，先读本节；改完同步更新。
