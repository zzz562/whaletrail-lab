# tvscreener 数据源整合记录 / Runbook

Date: 2026-08-11

## 背景

WhaleTrail 现有 paper trading 系统以黄金和美股为主，主要数据路径依赖现有 API / yfinance。为了降低 API 额度压力，并补齐 A 股、期货等关注标的，引入 TradingView Screener 的非官方 Python 封装 `tvscreener` 作为补充数据源。

本次定位不是高频交易数据源，而是用于：

- 小时级或天级 watchlist 跟踪；
- paper trading 信号辅助；
- A 股、黄金期货、原油期货、标普 500 等固定关注列表快照；
- 当现有 API 缺失或额度不足时作为 fallback。

## 数据源定位

`tvscreener` 调用 TradingView 的 screener/scanner 接口，适合获取当前快照和技术指标，例如：

- open / high / low / close / volume；
- change percent；
- RSI；
- SMA20 / SMA50 / SMA200；
- TradingView `Recommend.All` 技术评分。

它不是官方 TradingView API，也不是完整历史 K 线服务。paper trading 如果需要历史状态，应由 WhaleTrail 定时保存快照形成自己的历史记录。

## 关注列表

配置文件：`config/watchlist.yaml`

| 名称 | Yahoo 符号 | TradingView 符号 | 说明 |
|---|---|---|---|
| 黄金期货 | `GC=F` | `COMEX:GC1!` | COMEX 连续黄金期货 |
| WTI 原油期货 | `CL=F` | `NYMEX:CL1!` | NYMEX 连续原油期货 |
| 标普500指数 | `^GSPC` | `SP:SPX` | 指数，不建议作为可交易标的 |
| SPY ETF | `SPY` | `AMEX:SPY` | 标普 500 可交易代理 |
| 紫金矿业 | `601899.SS` | `SSE:601899` | A 股 |
| 潍柴动力 | `000338.SZ` | `SZSE:000338` | A 股 |
| 中红医疗 | `300981.SZ` | `SZSE:300981` | A 股 |
| 晓程科技 | `300139.SZ` | `SZSE:300139` | A 股 |
| 金力永磁 | `300748.SZ` | `SZSE:300748` | A 股 |

## 项目路径

项目现在位于：

```text
~/github_code/whaletrail-lab/projects/whaletrail/
```

所有路径以该目录为根。

推荐分层：

```text
WhaleTrail paper trading
  ↓
MarketDataProvider / DataSource
  ↓
existing API / yfinance / akshare / tvscreener
  ↓
统一 QuoteSnapshot 或 OHLCV DataFrame
  ↓
策略、信号、paper account、日报
```

`tvscreener` 应优先承担两类职责：

1. **watchlist 快照**：按 TradingView symbol 批量取固定关注列表；
2. **fallback 日线近似数据**：在没有完整历史 K 线时，把当前快照转为单行 OHLCV，用于小时级/天级扫描。

后续如果需要更严格回测，仍应使用 yfinance/akshare 或专门历史行情源。

## 端点分组

TradingView scanner 需要按资产类型走不同 endpoint：

| 资产 | TradingView endpoint | 示例 |
|---|---|---|
| 股票 / ETF / 指数 | `https://scanner.tradingview.com/global/scan` | `SSE:601899`, `AMEX:SPY`, `SP:SPX` |
| 期货 | `https://scanner.tradingview.com/futures/scan` | `COMEX:GC1!`, `NYMEX:CL1!` |

固定列表查询应使用 `symbols.tickers`，不要扫全市场。

## 依赖

Python 包见 `requirements.txt`。本次新增脚本需要：

```bash
pip install -r requirements.txt
```

说明：当前实现直接调用 TradingView scanner HTTP 接口作为轻量 fallback，不强制安装 `tvscreener` 包，也不强制安装 MCP。MCP 更适合 AI 交互分析，不建议作为主采集链路。如果后续要让 AI 助手直接查询 TradingView，可额外安装：

```bash
pip install tvscreener[mcp]
```

## 使用建议

- 小时级任务：每 1 小时拉一次 `config/watchlist.yaml`，保存快照。
- 天级任务：收盘后再拉一次，生成日报。
- 展示中文名时，以本地 watchlist 的 `name` 为准；TradingView 返回的 A 股名称通常是英文。
- `SP:SPX` 是指数，`tradable: false`；paper trading 如需模拟标普交易，使用 `AMEX:SPY`。

## 风险和注意事项

- `tvscreener` 是非官方库，TradingView 接口变化可能导致失效。
- 数据延迟、字段覆盖、A 股财务指标完整性取决于 TradingView。
- 不适合高频、实盘自动下单或合规级行情使用。
- 高频请求可能触发 TradingView 限制；固定 watchlist 的小时级/天级请求风险较低。

## 已实现

- 快照可写入 SQLite 的 `quote_snapshots` 表。
- `paper-live.py` 每次 tick 会拉取 `config/watchlist.yaml` 并保存快照。
- `paper-live.py` 在 yfinance 返回空或报错时，会尝试用 watchlist 中对应 Yahoo symbol 的 TradingView 快照作为 fallback。
- `scripts/fetch-tvscreener-watchlist.py` 支持 `--save-db` 和 `--report`。
- `scripts/watchlist-report.py` 可从 SQLite 最新快照生成 Markdown 报告。

## 常用命令

```bash
cd ~/github_code/whaletrail-lab/projects/whaletrail

# 拉取 watchlist，保存 SQLite，并生成报告
python3 scripts/fetch-tvscreener-watchlist.py --save-db --report results/watchlist_report.md

# 从 SQLite 最新快照生成报告
python3 scripts/watchlist-report.py

# 运行一次 paper-live tick：会同时拉取 watchlist、保存快照、写报告、执行原策略扫描
python3 scripts/paper-live.py tick
```

## MCP 后续可选

如需要 AI 交互查询，再安装并注册 `tvscreener[mcp]`。MCP 用于临时问答和探索，不替代上述确定性采集链路。
