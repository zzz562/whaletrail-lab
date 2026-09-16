# 相似选股：K 线 + 换手 + 筹码三通道

> 日期：2026-09-16 · 状态：代码已落地；Mac mini 实测数字补在文末

决策 21。相似选股从只比收盘波形，改成价、量、筹一起拟合。仍是读线观察，不扩可交易名单。

## 算法

| 通道 | 输入 | 距离 | 默认权重 |
|------|------|------|----------|
| K 线 | 收盘，min-max 后 DTW | 与决策 17 相同 | 0.50 |
| 量 | 换手 %（停牌当 0），min-max 后同一天 L1 | `pair_l1`（无时间规整：无约束 DTW 会把不同日的放量尖峰拉成一样） | 0.30 |
| 筹码 | 窗口内本地 CYQ（OHLC+turn，三角衰减，60 档相对价轴） | 1 维 Wasserstein | 0.20 |

融合：每个通道的距离在**本轮候选**里转成分位（0=最像），再加权。禁止把原始 DTW 和 EMD 加在一起。权重滑条在看板；和为 0 时退回纯 K 线。参考票没有可用换手则筹码权重置 0。

CYQ：停牌 / `turn` 空不加筹不衰减。直方图 L1 归一化后比较形状。不复权除权会在旧价留幽灵峰，caption 标明，不拉前复权第二套日 K。获利比例、集中度只展示，不进融合分。

## 代码

| 文件 | 作用 |
|------|------|
| `whaletrail/chips.py` | `chip_histogram` / `wasserstein_1d` / `chip_stats` |
| `whaletrail/similarity.py` | `rank_similar` 保留；新增 `rank_multi` |
| `whaletrail/storage/repository.py` | `daily_bars`（同一组 bar 对齐三通道） |
| `scripts/dashboard.py` | 量柱、筹码图、权重、分列表、三张叠加 |
| `scripts/ashare-similar.py` | 全市场 CLI，读 `daily_kline` |
| `tests/test_chips.py` `tests/test_similarity.py` | 合成数据不变量 |

不新增依赖（pytest 仅测试）。不落筹码表。不接东财。

## 运行（Mac mini）

```bash
cd ~/Projects/whaletrail-lab/projects/whaletrail
.venv/bin/python -m pytest tests/test_chips.py tests/test_similarity.py -q
.venv/bin/python scripts/ashare-similar.py --symbol sh.601899 --window 90 --top 20
```

看板 launchd 吃到新代码后可能要重启 `ai.whaletrail-dashboard`。

## 召回–重排（决策 22）

三通道全市场加权会把「K 线一般、筹码碰巧像」的票抬进来。改成搜索里的两段：

1. **召回** 全市场收盘 DTW，保留最近 `recall_n`（默认 80）。K 线不像的直接出局。
2. **重排** 只在这 80 只里算换手 L1 和筹码 EMD，分位加权（默认量 0.60 / 筹 0.40）。分位的分母是召回池，不是 5000 只。

`Δ = 召回名次 − 重排名次`。正数 = 量和筹把它往前抬。

`rank_multi` 仍在，测试和对照用。看板 / CLI 走 `retrieve_rank`。

## Mini 实测

参考 `sh.601899` 紫金矿业，90 日，5190 只满窗口（2026-09-16）：

| 模式 | 耗时 | Top |
|------|------|-----|
| 仅 K（`--weights 1,0,0 --include-st`） | **3.77s** | 中金黄金 3.21；其后中车 / 山金 / 若干 ST |
| 默认三通道（排除 ST） | **7.28s** | 中金黄金仍第 1；山金国际第 2；赤峰黄金 / 西部矿业 / 湖南黄金进前 20。ST 被滤掉。 |

7.28s < 15s，不开 P40 预筛。量 L1 几乎不占时间；多出来的 ~3.5s 主要是筹码直方图。

融合会把「K 线不算最近、但换手/筹码更像」的票抬上来（本轮里有茅台、中石油、美的）。不一定是坏事，权重滑条可以压筹码。黄金同业（中金 / 山金 / 赤峰 / 湖南）比纯 K 线更成簇。
