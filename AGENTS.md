# gwht — WhaleTrail 量化交易平台

个人量化实验台：黄金策略为主，A 股 watchlist 跟庄为辅。gwht 是独立项目，不依赖 gvalar。

## 机器角色

| 机器 | 角色 |
|------|------|
| Mac mini | **唯一开发机 / 源码唯一来源**：写代码、diff、docs、git push。同时是运行/部署机：回测、实时扫描、Telegram、cron、launchd、看板 `:8766` |
| MacBook | **观察者 / 只读**：`git pull` 后人工查看/阅读代码，不开发、不 push、不跑服务 |

开发 = Mac mini 写 + 跑。MacBook 是观察者：只 `git pull` 后人工查看代码，经 SSH 端口转发看 Mac mini 服务（见 `projects/whaletrail/docs/ENVIRONMENT.md`）。

## 开发 / 运行循环

| 任务 | 在哪台 |
|------|--------|
| 写代码、diff、docs、commit、push | Mac mini |
| 纯 Python 回测 / 数据抓取（需 venv + 代理） | Mac mini |
| 实时扫描 / Telegram / sentiment / cron / launchd | Mac mini |
| Ollama、OpenClaw Gateway | Mac mini |
| 看板生产实例 | Mac mini `:8766`（MacBook 经隧道访问） |

## Code sync

```
Mac mini（开发）──push──> GitHub ──pull──> MacBook（客户端）
```

```bash
# Mac mini 提交（唯一开发机）
cd ~/Projects/whaletrail-lab
git add -A && git commit -m "..." && git push origin main

# MacBook 只读同步
cd ~/github_code/whaletrail-lab
git pull origin main
```

## 文档地图

| 文档 | 内容 |
|------|------|
| `projects/whaletrail/docs/SCOPE.md` | 业务边界 + 决策记录（唯一决策账本） |
| `projects/whaletrail/docs/ARCHITECTURE.md` | 代码逻辑不变量 |
| `projects/whaletrail/docs/ENVIRONMENT.md` | 机器拓扑、服务、端口、密钥、运行命令 |
| `projects/whaletrail/docs/DEPLOY.md` | Mac mini 运维（launchd/cron/日志/排障） |

## 不变量

- 主策略 `gold_sma`（GLD SMA 20/50）；低频率 paper，不做高频。
- 黄金/美股回测走 yfinance + Parquet 缓存；A 股走 tvscreener 快照积累（低频率），不走 yfinance 历史回测；港股暂不纳入。
- 佣金 5 bps / 无最低 / 无滑点；市场单按开盘价成交（防前视）。
- 不提交 `.venv/`、`data_cache/`、`results/`、`logs/`、`*.log`、`*.err`。

## Conventions

- 分支 `main`；提交信息 imperative、简洁。
- 小步提交；不提交密钥。
- 决策变化写进 SCOPE.md 的决策记录，不在会话里口头带过。
