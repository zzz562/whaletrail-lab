# A股 相似选股（DTW 波形相似 + baostock 全市场数据）

> 日期：2026-09-01 · 状态：已实现并验证（Phase 0/1/2）

从 ValarmClub 实验室移植「找相似走势」能力到 WhaleTrail：给定一只参考股票 + 时间窗口，用 DTW（动态时间规整）对全市场按收盘价波形相似度排序，替代逐只翻看。

## 移植取舍

- **只移植 DTW 算法 + 工作流思路**。ValarmClub 的 Tushare 数据层（token 已过期，不再续费）、PyQt5 桌面 UI、空的 `clustering.py`、近空壳的 `feature_extractor` 均不移植。
- **DTW 用纯 NumPy 重写**（`whaletrail/similarity.py`），不引入 `dtaidistance` 依赖。语义对齐 ValarmClub：尾部对齐到较短序列 → min-max 归一化 → 经典 DTW 距离。若日后要亚秒级全市场扫描，可把 `dtw_distance` 替换为 `dtaidistance`（C 后端），调用方不变。
- **复权选择：不复权（`adjustflag="3"`）**，对齐 ValarmClub 的 Tushare 不复权输入。前复权会随分红回写历史价，破坏增量同步；90 日窗口内分红对波形影响极小。要切换改 `whaletrail/data/baostock_source.py` 的 `_ADJUST_FLAG`。

## 代码位置

| 文件 | 作用 |
|------|------|
| `whaletrail/similarity.py` | DTW 距离 + `rank_similar` 排名（纯 NumPy，无 Qt/Tushare/baostock 依赖） |
| `whaletrail/data/baostock_source.py` | baostock 日线数据源 + `to_baostock_code`/`from_baostock_code` 符号映射 |
| `whaletrail/storage/schema.py` | 新增 `daily_kline`、`ashare_universe` 两张表 |
| `whaletrail/storage/repository.py` | `save_daily_bars`/`save_universe`/`daily_closes`/`daily_last_date`/`universe_names` |
| `scripts/fetch-baostock-universe.py` | baostock → SQLite 增量同步（Mac mini 直连，勿走代理） |
| `scripts/ashare-similar.py` | Phase 0 CLI：对 8 只 watchlist 跑相似度（读 `build_daily_history`） |
| `scripts/dashboard.py` | 新增「🔍 相似选股」页 |

符号映射：`SSE:600690 ↔ sh.600690`、`SZSE:000338 ↔ sz.000338`、`BSE:831175 ↔ bj.831175`、`601899.SS ↔ sh.601899`。

## 数据流

```
Mac mini（nightly job）
  baostock 直连 ──► daily_kline (SQLite, PK code+trade_date)

Mac mini（Streamlit :8766, launchd ai.whaletrail-dashboard）
  「相似选股」页 ──► repo.daily_closes(start≈300天) ──► rank_similar ──► top-N 表 + 归一化叠加图
```

看板仍是只读监控面：页面只读 SQLite + 排名，不触发拉取。

## 部署步骤（Mac mini）

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/pip install -r requirements.txt        # 新增 baostock>=0.8.8

# 首次全市场回填（~5000 只，2015 起；增量按月 cron 只拉最新）
.venv/bin/python scripts/fetch-baostock-universe.py

# 或先只回填 watchlist 8 只（快速验证）
.venv/bin/python scripts/fetch-baostock-universe.py \
  --codes sh.601899,sz.000338,sz.300981,sz.300139,sz.300748,sz.002490,sz.300483,sz.300164 --start 20240101
```

baostock 是免费无 token 的国内源，Mac mini 直连（同 SZSE 交易日历，不走 Clash 代理）。首次全市场回填完成后，加到现有 15:30 A 股 cron 旁做增量。

## 验证记录

- DTW 正确性（MacBook 合成数据）：自相似距离 0、缩放平移不变、无关序列距离大、排名顺序正确。
- Phase 0（Mac mini 真实 8 只）：参考紫金矿业 → 晓程科技最像（6.65），符合「黄金/资源股聚簇」预期。
- Phase 1（Mac mini 真实 baostock）：拉取 600690 成功 21 行、8 只回填 5160 行，落库正确。
- Phase 2（Streamlit AppTest，MacBook 空态 + Mac mini 真数据）：新页渲染无异常，点「运行相似度扫描」出排名表 + 叠加图，无异常。

## 性能 / 已知边界

- 纯 NumPy DTW：5000 只 × 90 日 ≈ 17s（MacBook 基准）。看板用 `st.cache_data(ttl=3600)` + spinner；首查 ~17s，缓存后瞬时。默认 8 只 watchlist 场景瞬时。
- `--codes` 回填不写 `ashare_universe` 名称；看板对 watchlist 股票用 watchlist 名称兜底（`dashboard.py` 内合并），全市场回填（无 `--codes`）会写入中文名。
- baostock 无分钟线、指数分钟线有限；本功能只用日线，不涉及。
- 复权因子、涨跌停、ST 处理不在相似度里做——波形匹配是形态信号，不是交易信号；建议与现有 `whale_flag`（量价异常）叠加使用。

## 未提交提醒

本轮改动在 MacBook 工作区（未 commit），且 Mac mini 工作区有同名未提交改动（`dashboard.py` 等，来自 2026-08-31 决策 15/16）。正式部署走 git：MacBook commit → push → Mac mini pull，不要用 scp 覆盖 mini 本地改动。
