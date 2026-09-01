# Pause — Mac mini suite review + P0 honesty (2026-09-01)

Paused here. Next session: read this, then SCOPE decisions 15–16, then
`git status` (P0 code is in the working tree, not necessarily committed).

## What this session was

1. Review what actually runs on Mac mini (launchd / OpenClaw cron / Clash /
   tunnel / dashboard), not the docs’ happy path.
2. Implement **P0** (ops honesty). **P1** (replace `gold_sma` or not) was
   explicitly not implemented — product shape only, in SCOPE 16.

## Live picture at pause (mini, via `macmini-remote`)

Working: reverse tunnel, public kanban `:80→8766`, OpenClaw `2026.8.1`,
Ollama `qwen3:4b`, Clash HTTP **7890**, paper-live launchd, ashare cron,
wifi-watchdog.

Dashboard is now launchd `ai.whaletrail-dashboard` (KeepAlive). Clash **TUN
off** (`utun1500` gone). Thunderbolt SSH HostName updated to
`169.254.133.209` (old `169.254.230.133` was dead; IPs drift — prefer
`ssh macmini-remote` when unsure).

## P0 landed (code on MacBook + rsynced to mini)

| Item | Where |
|------|--------|
| Daily report end = today + GLD/SPY B&H | `scripts/daily-report.sh` |
| Sentiment: failed/empty scan does not clobber latest | `scripts/sentiment.py` |
| A-share ghost LONG gets `qty` | `scripts/ashare-paper.py` |
| Streamlit launchd | `scripts/ai.whaletrail-dashboard.plist` |
| Drop incomplete yfinance bars; don’t mark equity at NaN | `whaletrail/data/yfinance_source.py`, `engine/account.py` |
| Docs | SCOPE 15–16, DEPLOY, DASHBOARD, ENVIRONMENT, SENTIMENT_RUNBOOK |

Smoke after the NaN fix (2018-01-01 → 2026-08-31, last bar 08-28):

- GLD `gold_sma` **+96.1%** / 回撤 −16.6% / 41 trades
- GLD 买入持有 **+226.7%**
- SPY 买入持有 **+226.1%**

Sentiment restored 2026-08-31: GSI **0.080** (15 / 11 / 24, 50 tweets).
A-share 潍柴动力 `SZSE:000338`: inferred **1600 股** @ 29.65 from 08-14.

## Left undone (do this first on resume)

1. **Tailscale Network Extension still `activated enabled`** on the mini
   (`io.tailscale.ipn.macsys.network-extension` 1.102.2). App is gone;
   extension is the Errno 49 landmine. Needs sudo **on the mini**:

   ```bash
   sudo systemextensionsctl uninstall W5364U7YZB io.tailscale.ipn.macsys.network-extension
   systemextensionsctl list
   ```

2. Clash Party GUI can turn TUN back on. If `utun1500` reappears, flip TUN
   off in the tray; keep 7890.
3. MacBook working tree is dirty (P0 files + this note). Mini has the same
   files via rsync; source of truth is still MacBook. Commit/push when ready,
   then `git pull` on mini.
4. Daily cron Telegram still depends on OpenClaw delivery; 08-31 morning
   jobs were `error` because `sendMessage` failed during the TCP outage.
   A-share 15:30 that day delivered. Re-check `openclaw cron list` after a
   clean weekday 08:30.

## P1 — not started, already decided (SCOPE 16)

Do **not** build a comprehensive platform.

Two thin books only:

- **Gold:** daily truth (GLD strategy vs B&H vs SPY) + GSI as weather.
  5m panel stays 观察、勿跟单.
- **A-share:** 8 names, 15:30 paper, real qty / P&L. Do not add names until
  this book has a few weeks of honest fills.

Do not: 100 KOLs, HK, restore 5m following, fuse GSI into position size.
`gold_sma` replacement is still SCOPE 14 (grid vs B&H neighbourhood), later.

## Resume commands

```bash
ssh macmini-remote
launchctl list | grep -E 'whaletrail|openclaw|zeph|ollama'
openclaw cron list
curl -s http://127.0.0.1:8766/_stcore/health
systemextensionsctl list   # Tailscale should be gone after sudo
```
