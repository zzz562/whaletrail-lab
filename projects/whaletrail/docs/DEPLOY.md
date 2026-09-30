# Deploy — Mac mini 运行与运维

> Mac mini 是唯一开发机/源码唯一来源 + 运行/部署机。本文档只覆盖 whaletrail 相关服务；gwht 不依赖 gvalar 手册。

## 连接

```bash
ssh macmini        # Thunderbolt → VPS fallback
ssh macmini-fwd    # Thunderbolt + 端口转发
ssh macmini-remote # 强制走 VPS
```

| 端点 | IP |
|------|-----|
| MacBook Thunderbolt | link-local，会变 |
| Mac mini Thunderbolt | link-local，会变；当前 `169.254.133.209`（旧 `169.254.230.133` 已失效） |
| VPS 跳板 | `139.224.244.214:2222` |

Thunderbolt 链路 IP 会在插拔/重启后漂移。`ssh macmini` 依赖 `~/.ssh/config` 的当前 HostName + ping 失败则走 VPS；日常排障优先 `ssh macmini-remote`。

## 代码部署

Mac mini 是源码唯一来源：在 mini 上写代码、commit、push；MacBook 只读 `git pull`。

```bash
# Mac mini 提交并推送
cd ~/Projects/whaletrail-lab
git add -A && git commit -m "..." && git push origin main

# MacBook 只读同步
cd ~/github_code/whaletrail-lab
git pull origin main
```

若 Mac mini 无法访问 GitHub，改用 rsync 从 Mac mini 同步到 MacBook（方向反转）：

```bash
rsync -avz macmini:~/Projects/whaletrail-lab/ ~/github_code/whaletrail-lab/ \
  --exclude .venv --exclude data_cache --exclude results --exclude logs
```

## 端口转发（MacBook 访问 Mac mini 服务）

```bash
ssh -L 8766:localhost:8766 -L 18789:localhost:18789 -L 11434:localhost:11434 macmini
```

## launchd 服务

| Label | 用途 |
|-------|------|
| `ai.whaletrail-live` | paper trading 实时扫描（仅美股交易时段，周末/节假日自动跳过）· Telegram 推送已停用（`WT_TG_PUSH=0`，2026-09-29）；扫描仍写 `results/paper_live_state.json` 供看板黄金 paper 页 |
| `ai.whaletrail-dashboard` | Streamlit 生产看板 `:8766`（worktree `~/Projects/whaletrail-prod`，不监视文件，KeepAlive） |
| `ai.whaletrail-dashboard-stage` | Streamlit 预发看板 `:8768`（lab 工作区，公网 `/stage/`） |
| `ai.openclaw.gateway` | OpenClaw AI Agent 网关 |
| `homebrew.mxcl.ollama` | 本地 LLM（qwen3:4b） |
| `com.zeph.reverse-tunnel` | SSH 反向隧道 → VPS |
| `com.zeph.wifi-watchdog` | Wi-Fi 自检 + 隧道自愈（每 3 分钟） |

## 看板生产 / 预发

生产代码在 `~/Projects/whaletrail-prod`（`git worktree add --detach`，停在某个 SHA，不监视文件）。预发就是 `~/Projects/whaletrail-lab` 里正在改的文件。两边都读 lab 的 `results/`、`data_cache/`、`logs/`：生产靠 plist 里的 `WT_DATA_ROOT`；旧 SHA 的 worktree 上另外有这三项目录的符号链接，避免那一版脚本还不认识 `WT_DATA_ROOT`。

在 lab 里存盘只重载 `:8768`（公网 `/stage/`）。要给公网 `/` 的用户看，在 mini 上：

```bash
SHA=$(git -C ~/Projects/whaletrail-lab rev-parse HEAD)
git -C ~/Projects/whaletrail-prod checkout "$SHA"
git -C ~/Projects/whaletrail-lab branch -f prod "$SHA"
launchctl kickstart -k gui/$(id -u)/ai.whaletrail-dashboard
```

`prod` 是书签，指向 `main` 上已经发布的提交。不要在 `prod` 上开发，worktree 保持 detached。

反向隧道（`~/Library/LaunchAgents/com.zeph.reverse-tunnel.plist`，不入库）除了 `2222:localhost:22` 和 `127.0.0.1:8766:localhost:8766`，还有 `127.0.0.1:8768:localhost:8768`。改完这条要重启隧道，`2222` 会闪断。VPS nginx 只在 `/etc/nginx/conf.d/wt-dashboard.conf`：`/` 转生产，`/stage/` 转预发。安全组没有放开 8088，预发不走单独端口。

## Wi-Fi 看门狗

保持 mini 的 `BZL-IoT` Wi-Fi 在线，并在公网可达后确保反向隧道存活（脚本 `scripts/mini-wifi-watchdog.sh`，plist 模板 `scripts/com.zeph.wifi-watchdog.plist`）。

```bash
# 首次部署（mini 上，仓库已 pull 后）
mkdir -p ~/.config && chmod 700 ~/.config
# BZL-IoT 为开放网络（免密），无需密码文件
cp ~/Projects/whaletrail-lab/projects/whaletrail/scripts/com.zeph.wifi-watchdog.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.zeph.wifi-watchdog.plist
# 验证
launchctl list | grep wifi-watchdog
tail -5 ~/Projects/whaletrail-lab/projects/whaletrail/logs/wifi-watchdog.log
```

行为：Wi-Fi 未关联 BZL-IoT 且无 IP/默认路由时重连（BZL-IoT 免密，直接尝试加入）；ping 通 VPS 但 TCP 拒连时写 `TCP_BLOCKED` 日志标记；隧道进程不在跑时按标准流程 bootout + load 重启。日志 `logs/wifi-watchdog.log`。

## Cron（OpenClaw）

```bash
openclaw cron list                       # CLI 需 gateway 的 node（~/.nvm/.../v24.19.0/bin/node）；系统 node 22 会拒跑
openclaw cron run whaletrail-ashare      # 手动触发 A 股 paper
```

| 任务 | 调度 | 说明 |
|------|------|------|
| `whaletrail-ashare` | 工作日 15:30 CST | A股低频率 paper（`ashare-paper.py`，脚本内自检交易日历+时段）→ Telegram |

2026-09-29 核查：`whaletrail-daily`（08:30 日报）与 `whaletrail-sentiment`（09:00 情绪）已不在 cron 列表里；日报改手动 `scripts/daily-report.sh gold_sma GLD`。同日晚间起，`ai.whaletrail-live` 的 GLD/SPY paper 推送停用（`WT_TG_PUSH=0`），扫描本身继续跑。

## A股 baostock 全市场（相似选股 + 板块轮动数据）

Mac mini 直连，不走代理。日 K 加列后旧行 `tradestatus` 为空，脚本会自动从缺口日重拉。

系统 crontab（mini）每工作日 16:30 / 20:00 两班例行增量抓取（全市场日 K + 静态表 + `index_kline` 基准指数），日志 `logs/fetch-baostock.log`；21:45 再跑一次完整性体检。20:00 班兜底 baostock 当日 EOD 发布晚于 16:30 的情况：

```cron
30 16 * * 1-5 cd /Users/zeph/Projects/whaletrail-lab/projects/whaletrail && .venv/bin/python scripts/fetch-baostock-universe.py >> logs/fetch-baostock.log 2>&1
0 20 * * 1-5 cd /Users/zeph/Projects/whaletrail-lab/projects/whaletrail && .venv/bin/python scripts/fetch-baostock-universe.py >> logs/fetch-baostock.log 2>&1
45 21 * * 1-5 cd /Users/zeph/Projects/whaletrail-lab/projects/whaletrail && .venv/bin/python scripts/check-ashare-data.py >> logs/check-ashare-data.log 2>&1
```

抓取脚本的三道防线（2026-09-29 加，起因是 9-23 那班撞上 baostock 半关连接后空转 6 天）：

- `--query-timeout`（默认 90s）：单只查询用 SIGALRM 截断。baostock 的 socket 读循环在服务端半关连接后 `while True: recv(8192)` 永不返回（`baostock/util/socketutil.py`），只靠 socket timeout 治不了。
- `--max-minutes`（默认 90）：整轮超预算即停抓并告警，不会拖到下一班。
- 失败计数 + Telegram 告警：单只失败超过 `--fail-threshold`（默认 300）或基准指数有失败就推送；`--no-alert` 关闭。
- 并发锁 `results/.fetch-baostock.lock`：上一班没跑完时下一班直接跳过（`--no-lock` 关闭）。

注意 baostock 同一时刻只认一个会话：两个进程同时跑（手动 + cron，或两次手动）会互相踢下线，报 `10001001 用户未登录`，症状是后半段标的静默失败（2026-09-29 补 2018 历史时踩到，1055 只只补了 685 只）。锁只挡 cron 之间，手动补数前先确认没有班次在跑。

告警经 `whaletrail/reporting/telegram.py` 发送：token 取 `TG_BOT_TOKEN`/`TG_CHAT_ID`，缺省读 `~/.config/whaletrail/telegram.env`（600，git 之外），依次尝试环境代理 → 直连 → `127.0.0.1:7892`。体检口径：最近 3 个交易日（深交所日历）在 `daily_kline` 覆盖 ≥95% 上市名单、`index_kline` 8 条基准齐全，不达标推送并 exit 1。

手动补数/体检：

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/python scripts/fetch-baostock-universe.py --no-alert        # 手动补数不推送
# 补历史：例如把最早一根是 2019-01-02 的标的补到 2018 年
CODES=$(sqlite3 results/whaletrail.db "SELECT group_concat(code, ',') FROM (SELECT code, MIN(trade_date) mn FROM daily_kline GROUP BY code) WHERE mn = '2019-01-02'")
.venv/bin/python scripts/fetch-baostock-universe.py --from 20180101 --to 20181231 --codes "$CODES" --no-alert
.venv/bin/python scripts/check-ashare-data.py --no-alert              # 只体检
```

## 板块/指数 配置表（板块与指数「自己算」）

日线/指数点位走 baostock（见上），**配置表**（成分 + 权重）单独抓，板块与指数的序列一律用 `daily_kline` 本地计算，不取任何第三方的板块指数点位：

```cron
0 9 * * 6 cd /Users/zeph/Projects/whaletrail-lab/projects/whaletrail && .venv/bin/python scripts/fetch-board-members.py >> logs/fetch-board-members.log 2>&1
```

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/python scripts/fetch-board-members.py                  # 三源全抓（约 2 分钟）
.venv/bin/python scripts/fetch-board-members.py --sources csi,cn  # 只抓官方指数表
.venv/bin/python scripts/fetch-board-members.py --em-pages 3 --no-alert   # 冒烟
```

落表 `board_members(board_id, board_type, board_name, code, name, weight, vendor_date, source, snapshot_date)`，快照式：每次抓取对每个板块写一组 `snapshot_date` 行，历史可回溯、成分变化看得见。

| 来源 | board_type | 覆盖（2026-09-30） | 权重 |
|------|-----------|--------------------|------|
| 东财 `datacenter-web` 报表 `RPT_BOARD_CONSTITUENT` | `concept_em` / `industry_em` / `region_em` / `other_em` | 400 概念 + 496 行业 + 31 地域 + 104 其他（风格/事件/资金），1,031 个板块 / 9.4 万条成分 | 无 |
| 中证指数公司 closeweight xls | `index_csi` | 上证50 / 沪深300 / 中证500 / 中证1000 / 中证800 / 中证全指 / 科创50 | **完整**（合计 100%） |
| 国证/深证信息 sample-detail xls | `index_cn` | 深证成指 / 中小100 / 创业板指 / 国证2000 | **仅前 10 名**（合计 24–54%），不能复算指数 |

两点实测（2026-09-30）：

- 东财 `push2/api/qt/clist/get`（akshare 走的那条）**按源 IP 限流**：同一 URL 从阿里云 VPS 正常、从家里宽带被丢连接（TCP 通、HTTP 层被断，`/` 仍返回 404）。`datacenter-web` 那条没被限，且带 `SECUCODE`（交易所后缀）与 `BOARD_TYPE_NEW`（1 地域 / 2 行业 / 3 概念 / 4 其他），翻页就能拿全市场成分，比 clist 每板块一次少两个数量级请求。
- 三源都是国内站点，脚本用 `Session(trust_env=False)` **直连、绕开系统代理**（Clash 系统代理时通时断，走它反而添乱）。

口径（用配置表自己算）：

- **中证系列**：权重完整，`Σ w·pct_chg / Σ w` 即可复算——实测 15 个交易日平均误差 0.02–0.03pp（沪深300 / 中证500 / 中证1000 / 上证50，权重为 8-31 生效版本）。
- **国证/深证系列**：只用它的成分表，权重自己算（等权，或用 `volume/turn` 反推自由流通市值，同 `scripts/sector-rotation.py`），不能拿文件的权重列。
- **概念/行业板块**：无权重，按板块独立算等权／加权收益、成交额占比、上涨家数；注意概念高度重叠，**不要跨板块加总成交额**。
- 成分表只有「当前」口径（中证按月调整、概念每周变），回放历史会有前视；配置表适合当期与前向判断，长历史回测需另设口径。
- 北交所成员（`bj.`）入库但 `daily_kline` 无其日线，聚合时自动脱落（概念板块约 3.6% 的成分属于此类）。

## 日志

```bash
tail -f ~/Projects/whaletrail-lab/projects/whaletrail/logs/paper-live.log
tail -f ~/Projects/whaletrail-lab/projects/whaletrail/logs/paper-live.err
tail -f ~/.openclaw/logs/gateway.err.log
```

## 排障

**whaletrail-live 异常：**

```bash
launchctl list | grep whaletrail-live
launchctl print gui/$(id -u)/ai.whaletrail-live
# 重启（实测 bootstrap 会静默失效——rc=0 但服务不登记；用 load -w 更稳）
launchctl bootout gui/$(id -u)/ai.whaletrail-live
launchctl load -w ~/Library/LaunchAgents/ai.whaletrail-live.plist
# 验证进程真的起来了
launchctl list | grep whaletrail-live
```

**venv 路径异常：**

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/python -c "import sys; print(sys.executable)"
# 如果路径不对，重建：
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**OpenClaw Gateway 起不来（PID=-1）：**

```bash
ssh macmini 'export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 24 > /dev/null 2>&1 && export PATH="$(dirname $(which node)):/opt/homebrew/bin:$PATH" && openclaw doctor --fix'
```

**xAI 认证过期：**

```bash
ssh macmini 'export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 24 && export PATH="$(dirname $(which node)):/opt/homebrew/bin:$PATH" && openclaw models auth login --provider xai'
```

## VPS 反向隧道检查

```bash
ssh aliyun-vps 'ss -tlnp | grep 2222'
# 无输出 = mini 隧道断了，到 mini 上重启 reverse-tunnel
```

## 已知事故：Tailscale NE 卡死导致全系统 TCP 拒连

**症状**：mini 所有 TCP 连接（含 `127.0.0.1` 回环）报 `Can't assign requested address`，UDP/ICMP 正常；Wi-Fi 关联状态异常（`networksetup -getairportnetwork` 报 not associated 但有 IP、链路 active）。

**根因**（2026-08-31 定位）：Tailscale Network Extension（`io.tailscale.ipn.macsys.network-extension`，1.102.2）处于 `activated enabled` 但服务后端已死，内核级拦截全部 TCP。mihomo TUN 曾同时劫持流量（0/1 全路由到 utun1500），但非根因。

**修复**（任一，需 mini 本地或 sudo）：
- 系统设置 → 通用 → 登录项与扩展 → 网络扩展 → 关闭/移除 Tailscale（若 mini 不使用 Tailscale，推荐直接移除）
- 重启 mini（NE 复位；若复发仍需移除）
- `sudo systemextensionsctl reset`（重置所有系统扩展）

**处置决定（2026-08-31）：Tailscale 全量移除，不再使用。** 待 mini 上线后执行：
```bash
# 1) 解除 TCP 拦截（关键一步）
sudo systemextensionsctl uninstall W5364U7YZB io.tailscale.ipn.macsys.network-extension
# 2) 退出并删除应用与残留
osascript -e 'quit app "Tailscale"' 2>/dev/null; pkill -f Tailscale 2>/dev/null
launchctl bootout gui/$(id -u)/io.tailscale.ipn.macsys.login-item-helper 2>/dev/null
sudo rm -rf /Applications/Tailscale.app /Library/LaunchDaemons/io.tailscale.ipn.macsys*
# 3) 验证 TCP 恢复
nc -vz -w3 127.0.0.1 22 && nc -vz -w4 139.224.244.214 22
```

**判别命令**（在 mini 上）：
```bash
nc -vz -w3 127.0.0.1 22          # 回环 TCP 也拒连 = 系统级过滤
nc -vz -u -w3 10.252.20.1 53     # UDP 正常 = TCP 专属过滤器
systemextensionsctl list         # 看激活的 Network Extension
```

## 已知事故：Clash Party helper 恶意软件弹窗

**症状**（2026-08-31）：macOS 反复弹「已阻止恶意软件 / party ape helper」，关不掉。

**根因**：Clash Party（`/Applications/Clash Party.app`，mihomo 内核）TUN 模式需要特权 helper。`party.ape.helper` 被 Gatekeeper 拦截无法加载，应用反复重试 → 弹窗循环。`party.mihomo.helper`（系统代理/DNS）未被拦，正常运行；HTTP 代理 7890 是用户态 sidecar，**不依赖任何 helper**。

**处置**（已执行）：
```bash
# 1) 关 TUN（配置 ~/Library/Application Support/mihomo-party/mihomo.yaml）
#    tun.enable: true -> false（应用重启后 utun1500 消失，顺带消除路由劫持）
# 2) 删被拦的 helper（应用包里无副本，不会重建）
sudo launchctl bootout system/party.ape.helper
sudo rm -f /Library/LaunchDaemons/party.ape.helper.plist /Library/PrivilegedHelperTools/party.ape.helper
# 3) 重启应用：open -a "Clash Party"
```

**保留项**：`party.mihomo.helper`（系统代理/DNS）、7890 代理（Telegram、脚本依赖）。验证：`curl -x http://127.0.0.1:7890 https://www.google.com` 应 200；Telegram 正常收发。

**复发（2026-08-31）**：`mihomo.yaml` 里 `tun.enable` 又变回 `true`，`utun1500` 重新出现。Clash Party GUI 的 TUN 开关会覆盖 yaml。关掉后确认 `ifconfig utun1500` 不存在，且 7890 仍通。
