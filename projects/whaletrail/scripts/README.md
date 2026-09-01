# Scripts

| 脚本 | 用途 | 调用方式 |
|------|------|----------|
| `run-backtest.py` | 回测入口，策略+标的+日期+本金 | CLI / cron |
| `paper-live.py` | 实时多策略扫描 + Telegram 推送 | launchd 守护 (`tick` / `loop`) |
| `daily-report.sh` | 日报：回测 → 摘要 → stdout | cron / 手动 |
| `analyze.py` | 回测结果格式化（日报子模块） | 被 daily-report.sh 调用 |
| `dashboard.py` | Streamlit 看板 `:8766`（暗色终端风） | launchd `ai.whaletrail-dashboard`；设计见 `docs/DASHBOARD.md` |
| `sentiment.py` | X/Twitter KOL 情绪扫描 → Ollama 打分 | cron |
| `fetch-tvscreener-watchlist.py` | TradingView scanner 快照拉取 | cron / 手动 |
| `ashare-paper.py` | A股低频率 paper（快照积累→日线→SMA 信号） | cron / 手动 |
| `seed-ashare-history.py` | A股日线历史种子（tvdatafeed → quote_snapshots） | 手动 |
| `watchlist-report.py` | SQLite → Markdown watchlist 报表 | cron / 手动 |

策略注册表见 `whaletrail/strategy/registry.py`。
