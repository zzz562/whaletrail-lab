# Pause — X 情绪扫描（whaletrail-sentiment）

> 2026-09-01 · 已停止，待重设计

## 停了什么

OpenClaw cron `whaletrail-sentiment`（每日 09:00 Asia/Shanghai 跑 `scripts/sentiment.py` 拉 18 个黄金 KOL 的 X 推文 → Ollama 打分 → GSI）已在 Mac mini 禁用。

- 停用原因：省 X API 额度（月均 ~$13–15）；监控本身要重设计。
- 未删除，仅 `disable`：`enabled: false`。
- 恢复命令：`~/.openclaw/tmp/agent-cli/openclaw cron enable 9321339b-60ea-4577-96aa-0cfabeb4ba42`

## 影响面

- 看板「🐋 情绪监控」页会停在最后一次 `sentiment_latest.json`，不再更新（空状态可渲染，不会崩）。
- `daily-report.sh` 不调用 sentiment，回测日报不受影响。
- 其余 OpenClaw cron 未动：`whaletrail-daily`（8:30 日报）、`whaletrail-ashare`（15:30 A 股 paper）。

## 重设计时的方向（未定）

- 拉取频率 / KOL 名单缩减 / 是否复用历史缓存去重，均待定。
- 相关：`docs/SENTIMENT_RUNBOOK.md`、`scripts/sentiment.py`。
