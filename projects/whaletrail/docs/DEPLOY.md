# Deploy — Mac mini 运行与运维

> Mac mini 是运行/部署机。本文档只覆盖 whaletrail 相关服务；gwht 不依赖 gvalar 手册。

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

```bash
cd ~/Projects/whaletrail-lab
git pull origin main
```

若 Mac mini 无法访问 GitHub，改用 rsync 从 MacBook 同步：

```bash
rsync -avz ~/github_code/whaletrail-lab/ macmini:~/Projects/whaletrail-lab/ \
  --exclude .venv --exclude data_cache --exclude results --exclude logs
```

## 端口转发（MacBook 访问 Mac mini 服务）

```bash
ssh -L 8766:localhost:8766 -L 18789:localhost:18789 -L 11434:localhost:11434 macmini
```

## launchd 服务

| Label | 用途 |
|-------|------|
| `ai.whaletrail-live` | paper trading 实时扫描（仅美股交易时段，周末/节假日自动跳过） |
| `ai.whaletrail-dashboard` | Streamlit 看板 `:8766`（KeepAlive，重启自愈） |
| `ai.openclaw.gateway` | OpenClaw AI Agent 网关 |
| `homebrew.mxcl.ollama` | 本地 LLM（qwen3:4b） |
| `com.zeph.reverse-tunnel` | SSH 反向隧道 → VPS |
| `com.zeph.wifi-watchdog` | Wi-Fi 自检 + 隧道自愈（每 3 分钟） |

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
openclaw cron list
openclaw cron run whaletrail-daily       # 手动触发日报
openclaw cron run whaletrail-sentiment   # 手动触发情绪扫描
```

| 任务 | 调度 | 说明 |
|------|------|------|
| `whaletrail-daily` | 工作日 08:30 CST | `daily-report.sh gold_sma GLD` → Telegram |
| `whaletrail-sentiment` | 每日 09:00 CST | X KOL 情绪扫描 → Telegram |
| `whaletrail-ashare` | 工作日 15:30 CST | A股低频率 paper（`ashare-paper.py`，脚本内自检交易日历+时段）→ Telegram |

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
