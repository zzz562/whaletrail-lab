# WhaleTrail — 黄金为主的日线 Paper Trading

> **定位：** 把黄金做明白；美股指数/个股仅作对冲与辅助对照。  
> **不做：** A股、港股、高频/实时 tick。

纯 Python 事件驱动回测 + Streamlit 看板 + OpenClaw/Telegram 日报。

---

## 范围

| 优先级 | 资产 | 示例 | 数据源 |
|--------|------|------|--------|
| **主** | 黄金相关 | `GLD`（首选）、`GC=F`、`SLV` | yfinance + Parquet 缓存 + TradingView scanner |
| **辅** | 美股指数/个股 | `SPY`、`QQQ`、`AAPL` | 同上 |
| **不做** | A股 / 港股 | — | 已从工程主路径移除 |

**为什么是 GLD 不是 GC=F？**  
`GLD` 是黄金 ETF，日线连续、分红简单，适合 paper 与策略对比。  
`GC=F` 期货有换月与空数据问题，仅作可选对照。

---

## 快速开始

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail   # Mac mini
# 或
cd ~/github_code/whaletrail-lab/projects/whaletrail  # MacBook

# 依赖
.venv/bin/pip install -r requirements.txt

# 拉数据（需要代理时，见 docs/ENVIRONMENT.md）
export WT_PROXY_URL=http://127.0.0.1:7892   # 或 HTTPS_PROXY

# 黄金主回测
.venv/bin/python scripts/run-backtest.py gold_sma GLD 2018-01-01 2024-12-31 100000

# 美股对冲对照
.venv/bin/python scripts/run-backtest.py ma_cross SPY 2020-01-01 2024-12-31 100000

# 日报（回测 + Ollama 中文摘要）
./scripts/daily-report.sh gold_sma GLD

# 看板（暗色终端风；设计/运维见 docs/DASHBOARD.md）
.venv/bin/streamlit run scripts/dashboard.py --server.port 8766
# → http://127.0.0.1:8766/

# TradingView watchlist 快照
.venv/bin/python scripts/fetch-tvscreener-watchlist.py
.venv/bin/python scripts/watchlist-report.py
```

---

## 数据源

| 来源 | 模块 | 说明 |
|------|------|------|
| Yahoo Finance | `whaletrail/data/yfinance_source.py` | 日线 OHLCV + Parquet 缓存 |
| TradingView Scanner | `whaletrail/data/tvscreener_source.py` | 实时快照（watchlist） |
| 本地 Parquet | `whaletrail/data/cache.py` | 缓存归并 |
| YAML Watchlist | `whaletrail/data/watchlist.py` | 关注列表加载（`config/watchlist.yaml`） |

## 脚本入口

详见 `scripts/README.md`。

## 策略库


| 策略 | 文件 | 默认用途 |
|------|------|----------|
| `gold_sma` | `strategies/gold_sma.py` | **黄金主策略** SMA 20/50 |
| `ma_cross` | `strategies/ma_cross.py` | 通用双均线（对冲标的） |
| `bollinger` | `strategies/bollinger.py` | 布林带突破 |
| `turtle` | `strategies/turtle.py` | 海龟 / 唐奇安通道 |
| `momentum` | `strategies/momentum.py` | 动量趋势 |

主线：先在 **GLD** 上把策略做明白，再用 **SPY/QQQ** 看对冲或相对强弱。

---

## 架构

```
Telegram / Cron
      │
      ▼
scripts/run-backtest.py  →  JSON results/
scripts/analyze.py       →  Ollama 一句话中文
scripts/daily-report.sh  →  串联日报
scripts/dashboard.py     →  Streamlit 生产 :8766 / 预发 :8768（见 docs/DASHBOARD.md）

whaletrail/
├── data/          YFinanceSource + ParquetCache
├── engine/        事件驱动回测（Account / Broker / Backtester）
├── strategy/      可插拔策略
├── storage/       SQLite（可选）
└── metrics/       收益 / 回撤 / 夏普
```

---

## 数据原则

1. **主源：** yfinance（日线）
2. **缓存：** `data_cache/` Parquet，减少外网抖动
3. **代理：** 访问 Yahoo 时设 `HTTPS_PROXY`（如 BoostNet `7892`）
4. **不做：** 分钟线、tick、A股/港股接口

---

## 与旧系统

- LEAN / Docker / gold-paper：**已废弃**
- 归档：`~/archive/Lean/`、`~/archive/OpenClaw-PaperTrading/`（Mac mini）

---

## 路线图（收窄后）

- [x] 日线回测引擎 + 黄金策略
- [x] 看板
- [x] 策略库（SMA / 布林 / 海龟 / 动量）
- [ ] 本地缓存默认开启、失败可重试
- [ ] GLD vs SPY 基准对比
- [ ] 日线 paper live（信号扫描，非高频）
- [ ] 以后有券商账号再谈实盘

---

## 相关路径

| 路径 | 说明 |
|------|------|
| 本仓库 | `projects/whaletrail/` in [whaletrail-lab](https://github.com/zzz562/whaletrail-lab) |
| Mac mini 运行 | `~/Projects/whaletrail-lab/projects/whaletrail/` |
| Mac mini 运维 | `docs/DEPLOY.md` |
| 看板设计 / 运维 | `docs/DASHBOARD.md` |
