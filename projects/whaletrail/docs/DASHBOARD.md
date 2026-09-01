# WhaleTrail Dashboard — 看板设计 / 运维笔记

> 暗色交易终端风（Bloomberg / Fortress 参考）  
> 更新：2026-08-13 | 提交：`3af5080`

看板是 **只读监控面**，不写策略、不下单。数据来自 `results/` 与 SQLite；样式必须保持暗色 + 金色强调，不要退回默认浅色 Streamlit。

---

## 定位

| 维度 | 说明 |
|------|------|
| 代码 | `scripts/dashboard.py` |
| 主题 | `.streamlit/config.toml`（`base = dark`，`primaryColor = #e6b450`） |
| 端口 | `:8766`（Mac mini 本机；公网经反向隧道 + VPS nginx 80 暴露） |
| 入口 | `http://139.224.244.214/`（公网稳定入口，无需转发）；`http://localhost:8766/`（本机转发兜底） |

---

## 页面与数据

| 页 | 读什么 | 刷新 |
|----|--------|------|
| 📊 总览 | 最新回测 JSON + sentiment + paper-live + `quote_snapshots` | 60s fragment |
| 📈 回测结果 | `results/backtest_*.json`；缺 `metrics` 时现场算 | 手动 |
| 🔴 实时信号 | `results/paper_live_state.json` | 60s fragment |
| 🐋 情绪监控 | `results/sentiment_latest.json` + `sentiment_20*.json` | 手动 |
| 👀 Watchlist | SQLite `quote_snapshots`（`Repository.latest_quote_snapshots`） | 手动 |
| 🏠 运行状态 | 本机 HTTP 健康检查 + launchd + 文件新鲜度 | 60s fragment |

`results/`、`data_cache/`、`.venv/` **不入 git**。MacBook 副本常常没有情绪 / live / 完整回测；空状态必须可渲染。

---

## 设计系统（不要改 palettes 除非连着改 CSS）

| Token | 值 | 用途 |
|-------|-----|------|
| `--wt-bg` | `#0a0e17` | 页面底 |
| `--wt-surface` | `#111826` | 卡片 / 表 |
| `--wt-border` | `#1e2a3a` | 边线 |
| `--wt-gold` | `#e6b450` | 强调、选中、主收益 |
| `--wt-blue` | `#38bdf8` | 对照 / 对冲 |
| `--wt-up` | `#4ade80` | 涨、BUY、看多 |
| `--wt-down` | `#f87171` | 跌、SELL、回撤 |
| `--wt-muted` | `#8b98a9` | 标签、次要文字 |
| 字体 | Inter + JetBrains Mono | 文案 / 数字 |

原则：饱和色只表达方向（涨跌 / 买卖 / 健康）。数字用等宽 + `tabular-nums`。不要彩虹、不要大面积 emoji 当标题。

代码里的复用入口（都在 `dashboard.py`）：

- `_page_header` / `_card` / `_card_row` / `_pill` / `_ticker_tape`
- `_show`（`st.dataframe` + `hide_index`）
- `_style_base` + `_num_style` / `_rsi_style` / `_side_style`
- `_alt_dark`（Altair 图表必须走这个，再 `properties(width=980, height=…)`）

新增一页：在 `PAGES` 里加一项 + 一个 `page_*`，标题用 `_page_header`，数字用 `_card_row`，表用 `_show`，图用 `_alt_dark`。

---

## 怎么看 / 怎么重启

```bash
# MacBook：只转发看板
ssh -f -N -L 8766:127.0.0.1:8766 macmini
# 或三端口：macmini-fwd（8766 / 18789 / 11434）

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
# 若已有手动 streamlit，先杀掉再 load，避免 :8766 抢端口
kill $(lsof -tiTCP:8766 -sTCP:LISTEN) 2>/dev/null
launchctl bootout gui/$(id -u)/ai.whaletrail-dashboard 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/ai.whaletrail-dashboard.plist

# 主题 / CSS 改完：重启进程（KeepAlive 会拉起来）
launchctl kickstart -k gui/$(id -u)/ai.whaletrail-dashboard
```

`ai.whaletrail-live` 是 paper-live 扫描，**不是**这块 Streamlit。

---

## 开发约定

Mac mini `~/Projects/whaletrail-lab` 是唯一源码来源。重大改动在 mini 上测、commit、push；MacBook 只 `fetch` + `reset --hard origin/main`（先 stash 本地笔记）。

看板依赖的现场数据只在 mini：`sentiment_*.json`、`paper_live_state.json`、完整 `whaletrail.db`。在 MacBook 上改完样式后，必须到 mini 用真实数据跑一遍 AppTest + 浏览器。

```bash
# AppTest（可选关掉 60s fragment）
WHALETRAIL_NO_AUTOREFRESH=1 .venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('scripts/dashboard.py', default_timeout=60)
at.run()
"
```

AppTest 里 **每次** 重新取 `at.sidebar.radio[0]`，不要跨 `run()` 复用 widget 引用。

---

## 踩过的坑（别再踩）

1. **`Repository.latest_quote_snapshots` 必须存在** — `watchlist-report.py` 和 Watchlist 页都靠它。
2. **回测 JSON 旧文件没有 `metrics`** — 看板用 `calculate_metrics` + `compute_trade_pnl` 现场补；新跑的 `run-backtest.py` 会写入 JSON 和 SQLite。
3. **`trades.side` 入库必须小写** `buy`/`sell`（schema CHECK）；展示可以是 `BUY`/`SELL`。
4. **pandas 3 + 混类型列** — 状态表的「端口」必须全是字符串，否则 pyarrow 炸。
5. **布尔列不要丢给 `st.dataframe`** — 会变成复选框；先转成 `"今日"` / `"—"`。
6. **Altair / Vega 图表给明确 `width`+`height`** — 只靠 `width="stretch"` 时，容器宽度算成 0，图会塌成一条空框。
7. **`st.fragment` 只包面板，不要包整页路由函数** — 否则侧栏切页会乱。
8. **侧栏导航用 `st.radio`，不要用 `segmented_control`** — 窄侧栏里 segmented 会变成空白条。用 CSS 藏掉 radio 圆点（`[data-testid="stRadioOption"] > div > div > div:first-child`）。
9. **改 `.streamlit/config.toml` 必须重启进程**，浏览器 rerun 不够。

---

## 明确不做

- 把看板做成下单台 / 改成浅色 SaaS 后台
- 在 MacBook 长期改看板并直接 push（绕过 mini）
- 把 `results/` 或密钥提交进 git
- 用 LLM 生成日报数字（`analyze.py` 已改为直接格式化）
