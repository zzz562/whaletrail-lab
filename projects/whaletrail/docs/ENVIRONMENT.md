# Environment — 机器与运行环境

> 本文档回答"某个任务该在哪台机器、用什么命令跑"。代码逻辑见 ARCHITECTURE.md，业务边界见 SCOPE.md。

## 拓扑

| 机器 | 角色 | 路径 | 关键能力 |
|------|------|------|----------|
| Mac mini | 唯一开发机/源码唯一来源 + 运行/部署 | `~/Projects/whaletrail-lab` | 写代码、git push、venv、Clash 代理、Ollama、OpenClaw、cron/launchd。Thunderbolt IP 会变，SSH 不稳时用 `macmini-remote` |
| MacBook | 观察者/只读 | `~/github_code/whaletrail-lab` | git pull 后人工查看代码；无 Telegram/OpenClaw/cron |
| VPS | 公网跳板 | — | OpenClaw 公网入口、SSH 反向隧道 |

## 服务与端口

| 服务 | 主机 | 端口 | MacBook 访问 |
|------|------|------|--------------|
| WhaleTrail 看板（生产） | Mac mini | 8766 | 公网 `http://139.224.244.214/`（VPS nginx `:80` → 反向隧道） |
| WhaleTrail 看板（预发） | Mac mini | 8768 | 公网 `http://139.224.244.214/stage/`（nginx 把 `/stage/` 转到隧道 `8768`）。跑 lab 工作区，改代码不重启生产 |
| OpenClaw Gateway | Mac mini | 18789 | `http://localhost:18789/health` |
| Ollama | Mac mini | 11434 | `http://localhost:11434/api/tags` |
| Clash 代理 | Mac mini | 7890 | 脚本默认 `HTTPS_PROXY` |

## 公网入口（看板）

看板经「Mac mini 反向隧道 + VPS nginx」对外提供，隧道与 nginx 均由常驻进程托管（launchd `com.zeph.reverse-tunnel` / systemd `nginx`），MacBook 无需再起转发。

```bash
open http://139.224.244.214/               # 生产
open http://139.224.244.214/stage/        # 预发
curl -s http://139.224.244.214/_stcore/health
curl -s http://139.224.244.214/stage/_stcore/health
```

链路：VPS nginx（websocket 代理）→ VPS 本机反向隧道 → Mac mini streamlit。阿里云安全组只放了 22 和 80，所以预发挂在 `/stage/`，不另开端口。

| 公网 | VPS nginx | 隧道 | mini |
|------|-----------|------|------|
| `:80` `/` | `127.0.0.1:8766` | `-R 127.0.0.1:8766:localhost:8766` | 生产 `:8766`，代码在 `~/Projects/whaletrail-prod` |
| `:80` `/stage/` | `127.0.0.1:8768/stage/` | `-R 127.0.0.1:8768:localhost:8768` | 预发 `:8768`，`baseUrlPath=stage`，代码在 `~/Projects/whaletrail-lab` |

端口转发（MacBook 上执行）：

```bash
ssh -L 8766:localhost:8766 -L 18789:localhost:18789 -L 11434:localhost:11434 macmini
```

## 代理

脚本访问 Yahoo / X / DeepSeek 需要代理，默认 `http://127.0.0.1:7890`（Mac mini 的 Clash）。

- `run-backtest.py`、`paper-live.py`、`sentiment.py`：读 `HTTPS_PROXY`，未设则回落到 7890。
- `daily-report.sh`：先探测 7890 可用，不可用则直连。

**注意：`7890` / `11434` / `18789` 目前多为 `127.0.0.1` 硬编码，指向 Mac mini 本地。** 在 MacBook 上运行时，必须先建立上面的端口转发，否则会连到 MacBook 自己的 localhost。

## 密钥与凭证

| 凭证 | 变量 | 存放位置 |
|------|------|----------|
| Telegram Bot | `TG_BOT_TOKEN` | `~/.config/whaletrail/telegram.env`（600，cron 告警读它）；副本在 `~/Library/LaunchAgents/ai.whaletrail-live.plist` |
| Telegram Chat | `TG_CHAT_ID` | 同上，默认 `5102138680` |
| X/Twitter | `TWITTER_BEARER_TOKEN` | `sentiment.py` 内置默认值 |
| DeepSeek | `DEEPSEEK_API_KEY` | 环境变量，或 `~/.openclaw/service-env/ai.openclaw.gateway.env` |
| Ollama | 无密钥 | 本地 `http://127.0.0.1:11434`，模型 `qwen3:4b` |

## 运行矩阵

| 脚本 | 用途 | 运行机 | 依赖 |
|------|------|--------|------|
| `scripts/run-backtest.py` | 回测 | Mac mini（主）；MacBook 需 venv+代理 | yfinance、venv、代理 |
| `scripts/analyze.py` | 回测结果格式化 | 任意（本地读 results） | venv |
| `scripts/daily-report.sh` | 日报串联 | Mac mini（cron） | venv、代理 |
| `scripts/paper-live.py` | 实时扫描（非交易时段自动跳过）；Telegram 推送已停用（`WT_TG_PUSH=0`），仍写 `results/paper_live_state.json` 供看板黄金 paper 页 | Mac mini（launchd） | yfinance、代理 |
| `scripts/sentiment.py` | X 情绪扫描 | Mac mini（cron） | X token、DeepSeek/Ollama、代理 |
| `scripts/fetch-tvscreener-watchlist.py` | TradingView watchlist 快照 | Mac mini（cron/手动） | 直连 TV，无需代理 |
| `scripts/ashare-paper.py` | A股低频率 paper（快照积累→日线→信号；交易日历+时段门禁） | Mac mini（cron/手动） | venv、tvscreener、SQLite、直连 SZSE（无需代理） |
| `scripts/fetch-baostock-universe.py` | A股全市场日 K（OHLCV+turn/ST/PE/PB）+ 证监会行业分类 + 指数成分 + 基准指数日 K；单查询超时 + 整轮预算 + 并发锁 + 失败 Telegram 告警 | Mac mini（cron 工作日 16:30/20:00；手动亦可；首次加列后会自动重拉缺字段的旧 bar） | venv、baostock、直连（无需代理） |
| `scripts/check-ashare-data.py` | A股数据完整性体检（最近 3 个交易日的日线覆盖率 + 基准指数），不达标推送 Telegram | Mac mini（cron 工作日 21:45） | venv、SQLite、SZSE 日历、`TG_BOT_TOKEN` |
| `scripts/ashare-similar.py` | 全市场相似选股 CLI（K 召回 + 量/筹重排） | Mac mini（手动） | venv、SQLite `daily_kline` |
| `scripts/seed-ashare-history.py` | A股日线历史种子 | Mac mini（手动） | venv、tvdatafeed、代理 |
| `scripts/watchlist-report.py` | watchlist Markdown 报表 | 任意（本地读 SQLite） | venv |
| `scripts/dashboard.py` | Streamlit 看板。生产 launchd `ai.whaletrail-dashboard`（worktree，`:8766`）；预发 `ai.whaletrail-dashboard-stage`（lab，`:8768`） | Mac mini | venv、`WT_DATA_ROOT`（生产指向 lab 的 `results/`） |

## venv（MacBook 本地需要时）

```bash
cd ~/github_code/whaletrail-lab/projects/whaletrail
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

MacBook 当前没有 `.venv`；本地跑回测前先建 venv，并确保 7890 代理可用（或经隧道连 Mac mini）。

## 配置项（环境变量）

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `WT_PROXY_URL` | `http://127.0.0.1:7890` | 代理（优先于 `HTTPS_PROXY`） |
| `WT_OLLAMA_URL` | `http://127.0.0.1:11434/api/generate` | Ollama 打分端点 |
| `WT_OPENCLAW_ENV_FILE` | `~/.openclaw/service-env/ai.openclaw.gateway.env` | DeepSeek 密钥文件 |
| `HTTPS_PROXY` | — | 标准代理变量，`WT_PROXY_URL` 未设时生效 |

MacBook 本地跑脚本时，先建端口转发（默认值即可用），或设 `WT_*` 指向其它位置。

## 已知耦合

- 默认地址仍指向 Mac mini 本地（`127.0.0.1:7890`、`127.0.0.1:11434`、`~/.openclaw/...`）。MacBook 迭代需建立端口转发，或用 `WT_*` 环境变量覆盖。
