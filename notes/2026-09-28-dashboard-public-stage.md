# 看板默认相似选股 + 公网预发（2026-09-28）

已落地。决策 26。生产停在 `~/Projects/whaletrail-prod` 这个 detached worktree；预发跑 `~/Projects/whaletrail-lab`。

## 地址

| 地址 | 现在是什么 |
|------|------------|
| `http://139.224.244.214/` | 生产。发布之后是相似选股 |
| `http://139.224.244.214/?page=gold` | 黄金 paper |
| `http://139.224.244.214/?page=ashare` | A股 paper |
| `http://139.224.244.214/?page=kol` | KOL 评测 |
| `http://139.224.244.214/?page=genzhuang` | 跟庄复盘 |
| `http://139.224.244.214/stage/` | 预发，同一套页面，跑 lab 里正在改的代码 |
| `http://139.224.244.214/stage/?page=gold` | 预发上的黄金 paper，其余参数同生产 |

没有顶栏。不认识的 `page` 落回相似选股。

阿里云安全组没有放开 8088（从外网连这个端口会超时，机器上没有阿里云 API 密钥，打不开）。预发因此挂在现有的 80 端口 `/stage/`，Streamlit `baseUrlPath=stage`。

## 进程

| | 生产 | 预发 |
|--|------|------|
| launchd | `ai.whaletrail-dashboard` | `ai.whaletrail-dashboard-stage` |
| 端口 | `8766`，`fileWatcherType=none` | `8768`，文件监视开着 |
| 代码 | `~/Projects/whaletrail-prod` | `~/Projects/whaletrail-lab` |
| 数据 | `WT_DATA_ROOT` 指向 lab 的 `projects/whaletrail` | 同一份 |

worktree 上还有 `results/`、`data_cache/`、`logs/` 三条符号链接，给还不认识 `WT_DATA_ROOT` 的旧 SHA 用。预发扫描写 `logs/similar-scan.stage.log`。

发布（mini 上，只重启生产）：

```bash
SHA=$(git -C ~/Projects/whaletrail-lab rev-parse HEAD)
git -C ~/Projects/whaletrail-prod checkout "$SHA"
git -C ~/Projects/whaletrail-lab branch -f prod "$SHA"
launchctl kickstart -k gui/$(id -u)/ai.whaletrail-dashboard
```

`prod` 只是书签。Git 历史仍然只有 `main`。反向隧道多了一条 `-R 127.0.0.1:8768:localhost:8768`，plist 在 `~/Library/LaunchAgents/com.zeph.reverse-tunnel.plist`，不入库。nginx 在 `/etc/nginx/conf.d/wt-dashboard.conf`。

## 核对过

- 旧生产进程默认是黄金 paper，并且还有分段按钮；新脚本默认相似选股，没有分段按钮。`?page=gold|ashare|kol|genzhuang` 和非法参数都试过。
- 浏览器打开 `/stage/` 能看到全市场相似选股；`/stage/?page=ashare`、`?page=gold`、`?page=genzhuang` 能打开对应页。
- 在 lab 脚本里临时加了一句 `STAGEMARK`：`/stage/` 看得到，`/` 看不到。然后把这句删掉了。
