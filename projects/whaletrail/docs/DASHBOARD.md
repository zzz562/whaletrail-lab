# WhaleTrail Dashboard — 看板设计 / 运维笔记

> 暗色交易终端风（Bloomberg / Fortress 参考）  
> 更新：2026-09-28

看板是 **只读监控面**，不是下单台，不写策略。数据来自 `results/` 与 SQLite；缺文件就空状态，不编数字。样式必须保持暗色 + 金色强调，不要退回默认浅色 Streamlit。没有顶栏切换。打开 `/` 是相似选股；其他页靠地址参数。侧栏隐藏。

---

## 定位

| 维度 | 说明 |
|------|------|
| 代码 | `scripts/dashboard.py` |
| 主题 | `.streamlit/config.toml`（`base = dark`，`primaryColor = #e6b450`） |
| 端口 | 生产 `:8766`（公网 nginx `:80` `/`）；预发 `:8768`（公网 nginx `:80` `/stage/`）。两进程都在 Mac mini |
| 入口 | 生产 `http://139.224.244.214/`；预发 `http://139.224.244.214/stage/`。本机转发兜底 `http://localhost:8766/` |
| 导航 | 无顶栏。`/` 为相似选股。`?page=gold\|ashare\|similar\|boards\|arena` 打开对应页。不按 UA 分端 |
| 角色 | 人看的 UI。Telegram / OpenClaw 不在主路径（进程照常跑） |

---

## 页面与数据

五个内容页，没有「运行状态」页。`/` 与不认识的 `page` 都落在相似选股。其余页只通过地址打开：

`/?page=gold` 黄金 Paper · `/?page=ashare` A股 Paper · `/?page=boards` 板块指数 · `/?page=arena` 炒股擂台

| 页 | 读什么 | 空状态 |
|----|--------|--------|
| **黄金 Paper** | 黄金账：`results/backtest_*.json` 中 GLD `gold_sma` + `data_cache/GLD.parquet` 的买入持有 + `data_cache/SPY.parquet` 对照；金价对照：`GC=F` 日线，**只读自己的缓存文件**（`data_cache/GC=F.parquet` 或 `GC_F.parquet`）；5m/live：`results/paper_live_state.json`（仅观察）。 | 缺回测 / 缺缓存 / 缺 live 则该节空渲染；GC=F 缓存缺失则对照列为空/null，不拿 GLD 价格冒充。 |
| **A股 Paper** | 仅 `results/ashare_paper_state.json`（15:30 日频 paper 账：tvscreener 快照 + 深交所官方日历）。**不放「观察 / 接近 / 触发」**，不放黄金矿股。 | 缺 state 则空渲染，不编仓位与成交。 |
| **相似选股** | SQLite `daily_kline`。圈定的日期**只切模板股**（那段历史就是 demo 波形和筹码），且跨度上限 **365 天**，超了直接拒绝并说明；候选股一律取各自**最近同根数**交易日（尾部=最新交易日），比形状不比日期。取数窗口按模板跨度自适应。K 线 DTW 批量召回 → 池内按**七组**读数到模板的距离排序，默认**均衡**（筹码 / 做盘 / 量 / 箱体 / 末端 / 近期趋势 / 均线 = .25 / .15 / .15 / .12 / .08 / .12 / .13）。默认**只看主板**，科创板与创业板在召回前丢掉。K 线图画 MA7/30/55/120。改条件后要点金色按钮。结果不因改模板搜索而消失。`logs/similar-scan.log` 记生产上的每次扫描（含候选锚点日 `cand_end` 与 `exclude_boards`）。预发写 `logs/similar-scan.stage.log`。 | 无历史则提示跑 `fetch-baostock-universe.py`。无 turn 则筹码空。模板超一年或候选池塌到 ≤2 只时给提示，不静默空榜。 |
| **炒股擂台** | SQLite `arena_participants` / `arena_posts` / `arena_orders` / `arena_equity`。规则在 `docs/ARENA.md`。页面不写账。 | 名册还没入账 → 一句说明，不编胜率和净值。 |

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
| 擂台成交 | baostock 不复权日 K | SQLite `daily_kline` + `index_kline`（沪深300） | 深交所官方日历 |

硬规则：

- **两份 yfinance Parquet 不得混用。** 永不把 `GC=F` 价格写进 GLD 缓存文件（决策 9 的 `PRICE_BOUNDS` 双向拦截）。看板只读不写；GC=F 列只从 GC=F 自己的文件读，缺失就空/null。
- **黄金两列（GLD、GC=F）的日历都是美股交易日**——不是北京银行日切，不是深交所日历。
- A 股 paper 只走 15:30 日频 + 深交所日历；不拿 tvscreener 快照冒充已完成日线。
- 擂台成绩只在 `?page=arena`。黄金矿股不进黄金 Paper。

---

## 文案约束

- 不写「建议买入」「可跟单」。
- 5m / live 标「观察」，不作进场依据；永不标「可跟单」。
- GLD / GC=F 只作监控/对照，标明「不是银行牌价 / 不是境内可玩」。
- **纸黄金 / AU9999 本轮无数据源，不上板、不冒充。** 没有纸黄金 paper 页；该账的数据源与日切仍未决。
- 擂台文案不写「建议买入」「可跟单」。Grok 是参赛者，不是页面上的建议。
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

新增一页：在 `PAGE_DEFS` 里加一项 + 一个 `page_*`。当前只应有上面五个名字。不要加回顶栏按钮。

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
# Mac mini：生产 launchd `ai.whaletrail-dashboard`（:8766，worktree，不监视文件）
# 预发 launchd `ai.whaletrail-dashboard-stage`（:8768，跑 lab 工作区）
launchctl list | grep whaletrail-dashboard
lsof -iTCP:8766 -sTCP:LISTEN
lsof -iTCP:8768 -sTCP:LISTEN

# 首次部署（仓库已 pull 后）。生产 plist 指向 ~/Projects/whaletrail-prod
cp ~/Projects/whaletrail-lab/projects/whaletrail/scripts/ai.whaletrail-dashboard.plist ~/Library/LaunchAgents/
cp ~/Projects/whaletrail-lab/projects/whaletrail/scripts/ai.whaletrail-dashboard-stage.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/ai.whaletrail-dashboard 2>/dev/null
launchctl bootout gui/$(id -u)/ai.whaletrail-dashboard-stage 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/ai.whaletrail-dashboard.plist
launchctl load -w ~/Library/LaunchAgents/ai.whaletrail-dashboard-stage.plist

# 主题 / CSS 改在预发上看。生产要等发布（见 DEPLOY.md）再 kickstart
launchctl kickstart -k gui/$(id -u)/ai.whaletrail-dashboard-stage
```

`ai.whaletrail-live` 是 paper-live 扫描，**不是**这块 Streamlit。不要改那份 plist。

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

一次 `at.run()` 只渲染当前 `?page=` 的那一页。默认是相似选股。测其他页先设 `at.query_params["page"]`。没有顶栏，不要再用 `at.tabs`。

---

## 踩过的坑（别再踩）

1. **`Repository.latest_quote_snapshots` 必须存在** — `watchlist-report.py` 仍靠它。擂台结算用 `daily_kline`，不用这份快照冒充收盘。
2. **回测 JSON 旧文件没有 `metrics`** — 看板用 `calculate_metrics` + `compute_trade_pnl` 现场补；新跑的 `run-backtest.py` 会写入 JSON 和 SQLite。
3. **`trades.side` 入库必须小写** `buy`/`sell`（schema CHECK）；展示可以是 `BUY`/`SELL`。
4. **pandas 3 + 混类型列** — 状态表的「端口」必须全是字符串，否则 pyarrow 炸。
5. **布尔列不要丢给 `st.dataframe`** — 会变成复选框；先转成 `"今日"` / `"—"`。
6. **Altair / Vega 图表给明确 `width`+`height`** — 只靠 `width="stretch"` 时，容器宽度算成 0，图会塌成一条空框。
7. **`st.fragment` 只包面板，不要包整页路由函数** — 否则切 tab 会乱。
8. **不要加回顶栏，也不要改回侧栏** — 各页靠 `?page=`。手机和桌面同一地址。默认页是相似选股。
9. **改 `.streamlit/config.toml` 必须重启进程**，浏览器 rerun 不够。
10. **当日日 K 没入库就不结算擂台** — 缺 bar 的单保持锁定，不编开盘价。
11. **GC=F 不能读 GLD 的 parquet** — 两个文件各自读；拿错了会把 $3,xxx 金价当成 GLD 净值，重蹈决策 9 的覆车。

---

## 明确不做

- 把看板做成下单台 / 改成浅色 SaaS 后台
- 在 MacBook 长期改看板并直接 push（绕过 mini）
- 把 `results/` 或密钥提交进 git
- 用 LLM 生成日报数字（`analyze.py` 已改为直接格式化）
- 恢复 5m 跟单、扩港股、改 `watchlist.yaml`、新建 `yin-right.json`。擂台加人只改 `config/arena_roster.yaml`
- 纸黄金 / AU9999 上板（本轮无源，不冒充）、按 UA 分端做两套页面
