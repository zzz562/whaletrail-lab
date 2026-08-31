#!/bin/bash
# mini Wi-Fi + reverse-tunnel watchdog (launchd: com.zeph.wifi-watchdog)
# 职责：保持 BZL-IoT Wi-Fi 在线；检测到公网可达后确保反向隧道存活。
# 不处理 Tailscale NE 卡死（需 root），只记录 TCP_BLOCKED 标记供排障。
#
# 部署：见 docs/DEPLOY.md「Wi-Fi 看门狗」。
# 密码文件（可选）：~/.config/wifi-bzl-iot.pw，chmod 600，不入库。

SSID="BZL-IoT"
IFACE="en1"
PW_FILE="$HOME/.config/wifi-bzl-iot.pw"
VPS="139.224.244.214"
LOG="$HOME/Projects/whaletrail-lab/projects/whaletrail/logs/wifi-watchdog.log"
TUNNEL_PLIST="$HOME/Library/LaunchAgents/com.zeph.reverse-tunnel.plist"
TUNNEL_LABEL="com.zeph.reverse-tunnel"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# --- 1. Wi-Fi：关联 BZL-IoT（或链路功能上在线：有 IP 且默认路由存在） ---
cur=$(networksetup -getairportnetwork "$IFACE" 2>/dev/null)
ip=$(ipconfig getifaddr "$IFACE" 2>/dev/null)
if echo "$cur" | grep -qi "$SSID"; then
  :
elif [ -n "$ip" ] && route -n get default >/dev/null 2>&1; then
  :
else
  log "wifi: not on $SSID (assoc='$cur' ip='$ip'); joining"
  if [ -f "$PW_FILE" ]; then
    networksetup -setairportnetwork "$IFACE" "$SSID" "$(cat "$PW_FILE")" >>"$LOG" 2>&1
  else
    networksetup -setairportnetwork "$IFACE" "$SSID" >>"$LOG" 2>&1  # 尝试开放网络
  fi
  sleep 6
  cur=$(networksetup -getairportnetwork "$IFACE" 2>/dev/null)
  ip=$(ipconfig getifaddr "$IFACE" 2>/dev/null)
  echo "$cur" | grep -qi "$SSID" || [ -n "$ip" ] \
    && log "wifi: join ok (assoc='$cur' ip='$ip')" \
    || log "wifi: join FAILED (assoc='$cur' ip='$ip')"
fi

# --- 2. 公网可达性（隧道的前提） ---
if ! ping -c1 -W2 "$VPS" >/dev/null 2>&1; then
  log "net: VPS unreachable; skip tunnel check"
  exit 0
fi

# --- 3. TCP 阻塞标记（Tailscale NE 卡死特征：ping 通但 TCP 拒连） ---
if ! nc -vz -w3 "$VPS" 22 >/dev/null 2>&1; then
  log "TCP_BLOCKED: ping ok but TCP $VPS:22 refused (check Tailscale NE / systemextensions)"
fi

# --- 4. 反向隧道：不在跑就按 DEPLOY.md 标准流程重启 ---
if ! pgrep -f "2222:localhost:22" >/dev/null 2>&1; then
  log "tunnel: not running; restarting"
  launchctl bootout "gui/$(id -u)/$TUNNEL_LABEL" 2>/dev/null
  sleep 1
  launchctl load -w "$TUNNEL_PLIST" >>"$LOG" 2>&1
  sleep 6
  if pgrep -f "2222:localhost:22" >/dev/null 2>&1; then
    log "tunnel: restarted ok"
  else
    log "tunnel: restart FAILED (tail of /tmp/reverse-tunnel.err.log):"
    tail -2 /tmp/reverse-tunnel.err.log 2>/dev/null >> "$LOG"
  fi
fi
