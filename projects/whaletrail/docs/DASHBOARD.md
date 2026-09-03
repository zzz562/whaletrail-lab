# WhaleTrail Dashboard — 看板设计 / 运维笔记

> 暗色交易终端风（Bloomberg / Fortress 参考）  
> 更新：2026-09-03

看板是 **只读监控面**，不是下单台，不写策略。数据来自 `results/` 与 SQLite；缺文件就空状态，不编数字。样式必须保持暗色 + 金色强调，不要退回默认浅色 Streamlit。导航是**主区顶部的 `st.tabs`**，五个 tab；侧栏隐藏（手机也看得到 tab）。

---

## 定位

| 维度 | 说明 |
|------|------|
| 代码 | `scripts/dashboard.py` |
| 主题 | `.streamlit/config.toml`（`base = dark`，`primaryColor = #e6b450`） |
| 端口 | `:8766`（Mac mini 本机；公网经反向隧道 + VPS nginx 80 暴露） |
| 入口 | `http://139.224.244.214/`（公网稳定入口，无需转发）；`http://localhost:8766/`（本机转发兜底） |
| 导航 | 主区 `st.tabs`（非侧栏）。**同一 URL，不按 UA 分端**：手机与桌面同一页面、同一五个 tab，tab 条横向可滚 |
| 角色 | 人看的 UI。Telegram / OpenClaw 不在主路径（进程照常跑） |

---

## 页面与数据

主区 `st.tabs` 仅这五个名字，顺序固定（不要第六页「运行状态」）：

**黄金 Paper · A股 Paper · 相似选股 · KOL 评测 · 跟庄复盘**

| 页 | 读什么 | 空状态 |
|----|--------|--------|
| **黄金 Paper** | 黄金账：`results/backtest_*.json` 中 GLD `gold_sma` + `data_cache/GLD.parquet` 的买入持有 + `data_cache/SPY.parquet` 对照；金价对照：`GC=F` 日线，**只读自己的缓存文件**（`data_cache/GC=F.parquet` 或 `GC_F.parquet`）；5m/live：`results/paper_live_state.json`（仅观察）。 | 缺回测 / 缺缓存 / 缺 live 则该节空渲染；GC=F 缓存缺失则对照列为空/null，不拿 GLD 价格冒充。 |
| **A股 Paper** | 仅 `results/ashare_paper_state.json`（15:30 日频 paper 账：tvscreener 快照 + 深交所官方日历）。**不放「观察 / 接近 / 触发」**，不放黄金矿股。 | 缺 state 则空渲染，不编仓位与成交。 |
| **相似选股** | SQLite `daily_kline`（baostock）优先，否则 watchlist 快照积累。DTW 观察，不是交易账，不扩可交易名单。 | 无历史则提示跑 `fetch-baostock-universe.py`。 |
| **KOL 评测** | 冻结 18 个 A 股荐股账号名册 + `results/` 里已存的荐股/事后对照（如 `kol_eval*.json`）。**不是跟庄。** 不调 live X API，不用黄金情绪 JSON 冒充评测。 | 无存储评测 → 空表，不编准确率。 |
| **跟庄复盘** | 仅现有 A 股 watchlist（含黄金矿股）。标签只允许「观察 / 接近 / 触发」，来自已存结果（若有）。日 K 来自 baostock `daily_kline` 的**已收盘**复权 bar。tvscreener 快照不是已完成日线。 | 无已收盘日 K / 无已存标签 → 空或 null，不编 OHLC、不新建 `yin-right.json`。 |

健康 / 新鲜度不占主 tab，放在页脚 expander。

`results/`、`data_cache/`、`.venv/` **不入 git**。MacBook 副本常常没有 live / 完整回测；空状态必须可渲染。

---

## 数据源契约（别混）

| 序列 | 源 | 缓存文件 | 日历 |
|------|-----|----------|------|
| GLD 日线（策略 + 买入持有） | yfinance | `data_cache/GLD.parquet` | 美股交易日 |
| SPY 日线（对照） | yfinance | `data_cache/SPY.parquet` | 美股交易日 |
| GC=F 日线（金价对照） | yfinance | `data_cache/GC=F.parquet` / `GC_F.parquet` | 美股交易日 |
| A 股 paper（15:30） | tvscreener 快照 | `results/ashare_paper_state.json` | 深交所官方日历 |
| 跟庄复盘日 K | baostock（复权） | SQLite `daily_kline` | 深交所官方日历 |

硬规则：

- **两份 yfinance Parquet 不得混用。** 永不把 `GC=F` 价格写进 GLD 缓存文件（决策 9 的 `PRICE_BOUNDS` 双向拦截）。看板只读不写；GC=F 列只从 GC=F 自己的文件读，缺失就空/null。
- **黄金两列（GLD、GC=F）的日历都是美股交易日**——不是北京银行日切，不是深交所日历。
- A 股 paper 只走 15:30 日频 + 深交所日历；不拿 tvscreener 快照冒充已完成日线。
- 「观察 / 接近 / 触发」**只存在于跟庄复盘**（baostock 复权日 K、仅 watchlist），不出现在 A股 Paper。
- 黄金矿股在跟庄复盘，不在黄金 Paper。

---

## 文案约束

- 不写「建议买入」「可跟单」。
- 5m / live 标「观察」，不作进场依据；永不标「可跟单」。
- GLD / GC=F 只作监控/对照，标明「不是银行牌价 / 不是境内可玩」。
- **纸黄金 / AU9999 本轮无数据源，不上板、不冒充。** 没有纸黄金 paper 页；该账的数据源与日切仍未决。
- KOL 评测不称「跟庄」。跟庄一词只用于 watchlist 标签页。
- 缺数就空，不把决策记录里的历史百分比抄进 UI 冒充当前结果。

---

## 设计系统（不要改 palettes 除非连着改 CSS）

| Token | 值 | 用途 |
|-------|-----|------|
| `--wt-bg` | `#0a0e17` | 页面底 |
| `--wt-surface` | `#111826` | 卡片 / 表 |
| `--wt-border` | `#1e2a3a` | 边线 |
| `--wt-gold` | `#e6b450` | 强调、选中、主收益 |
| `--wt-blue` | `#38bdf8` | 对照 / 对冲 |
| `--wt-up` | `#4ade80` | 涨、BUY |
| `--wt-down` | `#f87171` | 跌、SELL、回撤 |
| `--wt-muted` | `#8b98a9` | 标签、次要文字 |
| 字体 | Inter + JetBrains Mono | 文案 / 数字 |

原则：饱和色只表达方向（涨跌 / 买卖 / 健康）。数字用等宽 + `tabular-nums`。不要彩虹、不要大面积 emoji 当标题。减少 chrome。

代码里的复用入口（都在 `dashboard.py`）：

- `_page_header` / `_card` / `_card_row` / `_pill` / `_show` / `_alt_dark`
- `_style_base` + `_num_style` / `_side_style` / `_label_style`

新增一页：在 `PAGES` 里加一项 + 一个 `page_*`。当前 `PAGES` 只应有上面五个名字。

---

## 怎么看 / 怎么重启

```bash
# MacBook：只转发看板
ssh -f -N -L 8766:127.0.0.1:8766 macmini

# 浏览器
open http://localhost:8766/
# 改完 CSS / config.toml 后必须硬刷新：Cmd+Shift+R
```

```bash
# Mac mini：看板由 launchd `ai.whaletrail-dashboard` 托管（KeepAlive）
launchctl list | grep whaletrail-dashboard
lsof -iTCP:8766 -sTCP:LISTEN

# 首次部署（仓库已 pull 后）
cp ~/Projects/whaletrail-lab/projects/whaletrail/scripts/ai.whaletrail-dashboard.plist ~/Library/LaunchAgents/
kill $(lsof -tiTCP:8766 -sTCP:LISTEN) 2>/dev/null
launchctl bootout gui/$(id -u)/ai.whaletrail-dashboard 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/ai.whaletrail-dashboard.plist

# 主题 / CSS 改完：重启进程（KeepAlive 会拉起来）
launchctl kickstart -k gui/$(id -u)/ai.whaletrail-dashboard
```

`ai.whaletrail-live` 是 paper-live 扫描，**不是**这块 Streamlit。不要改 LaunchAgent plist。

---

## 开发约定

Mac mini `~/Projects/whaletrail-lab` 是唯一源码来源。重大改动在 mini 上测、commit、push；MacBook 只 `fetch` + `reset --hard origin/main`（先 stash 本地笔记）。

看板依赖的现场数据只在 mini：`paper_live_state.json`、`ashare_paper_state.json`、完整 `whaletrail.db`。在 MacBook 上改完样式后，必须到 mini 用真实数据跑一遍 AppTest + 浏览器。

```bash
# AppTest（可选关掉 60s fragment）
WHALETRAIL_NO_AUTOREFRESH=1 .venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('scripts/dashboard.py', default_timeout=60)
at.run()
"
```

导航是主区 `st.tabs`，AppTest 里用 `at.tabs` 而不是 `at.sidebar.radio`（侧栏已隐藏）。tab 顺序：黄金 Paper → A股 Paper → 相似选股 → KOL 评测 → 跟庄复盘。一次 `at.run()` 会渲染全部 tab 的 body。

---

## 踩过的坑（别再踩）

1. **`Repository.latest_quote_snapshots` 必须存在** — `watchlist-report.py` 仍靠它；跟庄复盘的已完成日 K **不要**用这份快照冒充。
2. **回测 JSON 旧文件没有 `metrics`** — 看板用 `calculate_metrics` + `compute_trade_pnl` 现场补；新跑的 `run-backtest.py` 会写入 JSON 和 SQLite。
3. **`trades.side` 入库必须小写** `buy`/`sell`（schema CHECK）；展示可以是 `BUY`/`SELL`。
4. **pandas 3 + 混类型列** — 状态表的「端口」必须全是字符串，否则 pyarrow 炸。
5. **布尔列不要丢给 `st.dataframe`** — 会变成复选框；先转成 `"今日"` / `"—"`。
6. **Altair / Vega 图表给明确 `width`+`height`** — 只靠 `width="stretch"` 时，容器宽度算成 0，图会塌成一条空框。
7. **`st.fragment` 只包面板，不要包整页路由函数** — 否则切 tab 会乱。
8. **导航用主区 `st.tabs`，不要退回侧栏** — 手机上侧栏默认收起，用户看不到入口。tab 条 `overflow-x:auto` + `flex-wrap:nowrap`，窄屏横滚。
9. **改 `.streamlit/config.toml` 必须重启进程**，浏览器 rerun 不够。
10. **当日日 K 不算完成** — 跟庄复盘不用同一根未收盘阴线，不编左压。
11. **GC=F 不能读 GLD 的 parquet** — 两个文件各自读；拿错了会把 $3,xxx 金价当成 GLD 净值，重蹈决策 9 的覆车。

---

## 明确不做

- 把看板做成下单台 / 改成浅色 SaaS 后台
- 在 MacBook 长期改看板并直接 push（绕过 mini）
- 把 `results/` 或密钥提交进 git
- 用 LLM 生成日报数字（`analyze.py` 已改为直接格式化）
- 恢复 5m 跟单、扩港股、扩 KOL 名单、改 `watchlist.yaml`、新建 `yin-right.json`
- 纸黄金 / AU9999 上板（本轮无源，不冒充）、按 UA 分端做两套页面
