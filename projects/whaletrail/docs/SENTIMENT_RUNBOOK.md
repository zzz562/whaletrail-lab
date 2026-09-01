# WhaleTrail Sentiment — 情绪子项 Runbook

> 跟庄情绪层：X API 拉黄金 KOL 推文 → Ollama 打分 → GSI 情绪指数  
> 创建：2026-08-11 | 状态：Phase 1 ✅（独立运行，待融合）

---

## 定位

| 维度 | 说明 |
|------|------|
| 在工程里 | `whaletrail-lab/projects/whaletrail/` 的子模块 |
| 代码 | `scripts/sentiment.py` |
| 数据 | `results/sentiment_*.json` |
| 看板 | `dashboard.py` → 🐋 情绪监控 分页（样式见 `docs/DASHBOARD.md`） |
| 与 paper-live | 当前**独立运行**；Phase 2 融合（待做） |
| 与回测引擎 | 不直接耦合 |

---

## 数据流

```
cron 每日 09:00
    │
    ▼
sentiment.py
    ├── X API v2 (Bearer Token)
    │   ├── GET /2/users/by/username/:user → user_id (缓存)
    │   └── GET /2/users/:id/tweets → 最新 5 条推文
    │
    ├── Ollama qwen3:4b (subprocess CLI)
    │   └── 每条推文 → bullish/bearish/neutral + 1-5 置信度
    │
    └── 输出
        ├── results/sentiment_YYYYMMDD.json   (每日快照；失败也会写，带 fetch_failed)
        ├── results/sentiment_latest.json      (最新 → 看板读；X API 全失败或 0 条新评分时不覆盖)
        └── results/sentiment_state.json       (去重: seen_tweets + user_cache)
```

## 黄金 KOL 名单（18 个）

`PeterLBrandt` `LukeGromen` `SantiagoAuFund` `KitcoNewsNOW` `GoldPredictors` `KobeissiLetter` `DonDurrett` `TheDailyGold` `badcharts1` `KimbleCharting` `GoldSilver_com` `TheGoldAdvisor` `Oliver_MSA` `GoldCore` `spotgoldprice` `goldminingnews` `SWGoldReport` `Huanusa`

来源：`WHALE_WATCH.md` 第 1 组。

---

## 成本

| 项目 | 单价 | 日均 |
|------|------|------|
| 读一条推文 | $0.005 | ~$0.45（18 KOL × 5 条） |
| 查一个用户 | $0.01 | 首次 $0.18，之后缓存 |
| **月均** | | **≈ $13-15** |

24h 内重复请求同一条推文**不重复计费**。

---

## 操作手册

### 手动跑一次

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail

# 单 KOL 测试
.venv/bin/python3 scripts/sentiment.py --account PeterLBrandt

# 全量扫描
.venv/bin/python3 scripts/sentiment.py
```

### 查看结果

```bash
# 最新情绪指数
cat results/sentiment_latest.json | python3 -m json.tool | head -20

# 提取 GSI
python3 -c "import json; d=json.load(open('results/sentiment_latest.json')); print(d['gold_sentiment_index'])"
```

### Cron 管理

```bash
# 查看
openclaw cron list | grep sentiment

# 手动触发一次
openclaw cron run whaletrail-sentiment

# 删除重建
openclaw cron rm <id>
openclaw cron add --name whaletrail-sentiment --cron "0 9 * * *" --tz Asia/Shanghai \
  --command "cd ~/Projects/whaletrail-lab/projects/whaletrail && .venv/bin/python3 scripts/sentiment.py" \
  --channel telegram --to 5102138680 --announce
```

### 重置状态（清空缓存）

```bash
rm results/sentiment_state.json
```

---

## 待融合（Phase 2 plan）

| 步骤 | 内容 |
|------|------|
| 1 | `paper-live.py` 读 `sentiment_latest.json` |
| 2 | GSI > 0.15 时标注「情绪共振」；GSI < -0.15 时标注「逆势」 |
| 3 | Telegram 推送标注情绪标签 |
| 4 | 可选：GSI 极值时调整目标仓位 |

当前 paper-live 还没接情绪数据，融合改动范围小（±30 行）。

---

## 文件清单

```
projects/whaletrail/
├── scripts/sentiment.py           ← 主脚本
├── scripts/dashboard.py           ← 看板（情绪分页）
├── results/
│   ├── sentiment_YYYYMMDD.json    ← 每日快照
│   ├── sentiment_latest.json      ← 最新（看板读）
│   └── sentiment_state.json       ← 去重状态
└── WHALE_WATCH.md                 ← KOL 名单源
```

---

## 历史回溯

X API full-archive search 需额外付费（按条 $0.005 无去重）。  
当前策略：**从今天开始积累，一周后自然有历史**。  
看板已有 3 天 seed 数据用于图表展示。
