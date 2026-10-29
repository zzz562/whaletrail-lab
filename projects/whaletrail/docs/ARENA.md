# 炒股擂台

短线纸面成绩。用来看谁的帖子值钱，不把喊单送进黄金账或 A 股 paper，也不发 Telegram。

页面：`?page=arena`。规则代码：`whaletrail/arena/`。名册：`config/arena_roster.yaml`。账在 `results/whaletrail.db`（不进 git）。原始帖和图片路径约定在 `results/kol_arena/`（不进 git）。

入赛日 **2026-10-20**。这一天之前的帖子只归档，不交易。每人起始现金 10 万，只在第一次写入时设置，改名册不会把现金重置。

## 谁在场上

名册里 21 个 X 账号，加上 **Grok**。Grok 不是裁判。它用自己收集的、截至上一交易日收盘的信息写一份参赛文，然后和别人走同一套成交、费用和排名。

种子名单在 `config/arena_roster.yaml`。名册外的账号只要交来一份对得上代码的向前喊单（1 到 3 只），`arena-lock.py` 就让他入赛，当场领 10 万。同时在赛最多 **100** 人，满了就拒绝，不排队插队。已出局的人不回来，本金不重置。被「移出」的人（见下）不算出局，可以像新人一样再交一份合格喊单重新入赛：原记录复用，`joined_on` 改成新的入场日，现金重置为 10 万（他们从未下过单），仍受 100 人上限约束。

出局只在空仓时判。净值低于 2 万，记亏完。连续 10 个交易日没有新帖，记停更。还有持仓就先卖完，下一笔结算再判。Grok 在白名单里，不会被移出或出局（亏完、停更、零帖移出都不判）。

移出（removed）与出局不同：自 `joined_on` 起满 **5** 个交易日（`NO_POST_REMOVE_TRADING_DAYS`，不含入场当日）一帖未发、且没有任何订单的人，结算时标为 `status='removed'`，`eliminated_on` 记日期、`eliminated_reason='no_posts'`。不删任何帖子、订单、净值记录。移出的人不占 100 人名额，可重新入赛。有过发帖的人仍按上面的 10 日停更规则判出局。

## 时钟

北京时间。

- 帖子时间在某个交易日 **09:25 及以前**：这张单的进场日就是当天，按当天开盘价买。
- 交易日 09:25 之后的帖，包括午休和下午盘中，都进下一个交易日的开盘。日 K 里没有午盘价，这些话只归档，不当天成交。
- 进场日看 JSON 里的发帖时间 `created_at`（北京时间）：≤ 09:25 进当天开盘，之后进下一交易日。锁单本身可以晚于 09:25，只要在进场日 **15:30** 前跑完 `arena-lock.py` 即可；15:30 之后才锁上的标 `late_capture`，原文留下、订单没有。Grok 与 KOL 同一条宽限。
- 第一次锁上的正文为准。之后的编辑不改订单。删帖用 `arena-mark-deleted.py` 打标，成交保留。

## 怎样才是一笔买单

模型只填 JSON。代码决定接不接。

- `stance` 只能是 `forward` / `recap` / `list` / `exit` / `ambiguous`。
- `forward` 才买。`recap`、`list`、`ambiguous` 只归档。
- 一帖里 `names` 超过 3 个，整帖改判成 `list`，一单不下。不要从长名单里替他挑三只。
- 每项要有 `code`（六位或 `sh.600000`）或和 `ashare_universe.name` 完全一致的全称。对不上、重名、北交所，都拒绝。
- `evidence` 必须是正文或 `ocr_text` 里的连续子串。
- 每个账号每个进场日最多 3 笔新买单。同一代码同一天只留第一笔。
- 已经持有的代码不加仓。

卖出喊单：`stance=exit`，且正好 1 只。发帖时间在进场日 09:25 及以前、且进场日 15:30 前锁定，当天开盘卖掉还能卖的仓。卖不掉（一字跌停、停牌）就作废这次喊卖，之后按止损或收盘处理。

## 成交

结算只在日 K 到齐之后跑。早上锁单不读价格。

- 买价 = 进场日开盘。一手 100 股，目标名义 2 万。一手超过 2 万就放弃。
- 一字涨停（最低 = 最高，并且收在涨停价）不买。停牌不买。没有前收盘或没有这根 K，留到下一次结算，不猜价格。
- 涨跌停：ST 5%，创业板和科创板 20%，其余 10%。用前收盘四舍五入到分。
- 费用和 A 股 paper 相同：佣金万 2.5、单边最低 5 元、卖出印花税 0.05%、单边滑点 0.1%。
- T+1。进场当天不能卖。

两个数分开记：

- **预测**：下一交易日收盘卖出，不止损。超额 = 这笔净收益 − 沪深300 从进场日开盘到那个收盘的收益。
- **账户**：可卖的第一天，开盘已经跌穿进场开盘价的 6% 就按开盘卖；最低价碰到 −6% 就按止损价卖；否则按收盘卖。一字跌停顺延。−6% 写死，模型不许改。

主榜：成交满 10 笔，且买不进率低于 40%。排序分 = 平均超额 × n / (n + 8)。没到门槛的在观察席，净值照记，人不除名。

## Grok 参赛文

每个交易日一份，而且只能有一份。

- `handle` = `grok`
- `post_id` = `grok:YYYY-MM-DD`，日期就是进场日
- `stance` = `forward`
- 正文至少 40 字，写清用了什么信息。信息只能到 **上一交易日收盘**：`daily_kline`、`index_kline`、板块、已经归档的帖子。不许用进场日的开盘或收盘。
- 最多 3 只。证据句必须出现在正文里。

示例：`config/arena_grok.example.json`。KOL 帖的示例：`config/arena_post.example.json`。

## 文件

把 JSON 丢进 `results/kol_arena/inbox/`。`arena-lock.py` 成功后挪到 `processed/`，拒绝的连同原因在 `rejected/`。

图片如果是唯一的代码来源，bot 先 OCR，把认出的字放进 `ocr_text`，原图自己留在 `results/kol_arena/media/`。库里至少要有正文和 OCR 文本。链接只是方便回看。

## 命令

都在 `projects/whaletrail` 下，用 mini 的 venv。

```bash
.venv/bin/python scripts/arena-lock.py
.venv/bin/python scripts/arena-settle.py
.venv/bin/python scripts/arena-status.py
.venv/bin/python scripts/arena-status.py --json
.venv/bin/python scripts/arena-mark-deleted.py <post_id>
```

`arena-settle.py` 默认结算到沪深300最新一根日 K。个股缺 bar 就保持锁定，下次再试。不要用东财 15:10 的残缺快照充数。20:00 那班抓完、`ashare-fetch-status.py --json` 健康之后再结算。结算时会把亏完或停更、且已经空仓的人标成出局。

## 交给 Grok bot 的任务

### 任务 1 · 每个交易日 09:10 前 · Grok 自己的参赛单

工作目录：`~/Projects/whaletrail-lab/projects/whaletrail`。

读到上一个交易日为止的 `daily_kline`、`index_kline`、`board_members`。可以读 `arena_posts` 里已经归档的文字。不要读进场日的行情。

写一份最多 3 只的喊单到 `results/kol_arena/inbox/grok-YYYYMMDD.json`，字段按 `config/arena_grok.example.json`。`created_at` 用发帖/交稿时间（≤ 09:25 才算当天）。写完立刻跑 `arena-lock.py`。进场日 15:30 之后才锁定的当天单会被标成晚抓，不成交；Grok 与 KOL 同宽限。没有把握就少写，可以 1 只，不要凑满 3 只。

### 任务 2 · 每个交易日 09:10 前 · 把名册里的新帖收成 JSON

名册里 `kind: kol` 的账号，用 X 拉取上次游标之后的帖。一帖一个 JSON，放进同一个 inbox，然后同一轮 `arena-lock.py`。

你来填 `stance` 和 `names`。收评、龙虎榜、融资榜、午评，标 `recap`。一帖能对上的名字超过 3 个，标 `list`。暗语、外号、对不上全称的，不要猜代码。图里有代码就 OCR 进 `ocr_text`，`evidence` 用 OCR 文本里的原句。09:22 前结束。

### 任务 3 · 每个交易日 15:40 · 收盘后的第二趟

再拉一次新帖，写入 inbox，跑 `arena-lock.py`。这些帖的进场日是下一个交易日。已经在库里的 `post_id` 会当成重复，不会改第一次的正文。

### 任务 4 · 日 K 健康之后 · 结算

先跑 `scripts/ashare-fetch-status.py --json`。不健康就停，不要结算。健康之后跑 `arena-settle.py`，再跑 `arena-status.py --json` 看榜。

抽查近 10 个交易日的帖子 id。X 上已经 404 的，跑 `arena-mark-deleted.py`，不要把订单删掉。

### 任务 5 · 看到该参赛的新人 · 直接写进 inbox

不要只记在 candidates 里。新人若有 1 到 3 只对得上代码的向前喊单，就写成和老账号一样的 JSON 放进 inbox，交给 `arena-lock.py`。在赛不满 100 就入赛领 10 万。锁单打印「在赛已满 100 人」或「已出局」时，不要改名册硬插，这份 JSON 留在 rejected 里即可。

## 模型不在脚本里

`arena-lock.py` 和 `arena-settle.py` 不调用大模型。也不要走 OpenClaw。解读是 Grok bot 自己的事：bot 读帖、写 JSON，再跑上面的 Python。

定时跑三次，每次把下面整段 prompt 交给 Grok bot。bot 看北京时间，只做对得上的那一班。

- 工作日 09:10：早班
- 工作日 15:40：收盘归档，这些单进下一个开盘
- 工作日 21:10：日 K 健康之后结算

午休和盘中不再单独做一班。那些帖留到 15:40 一起归档。

```text
你是 WhaleTrail 炒股擂台的运维。工作目录是 Mac mini 上的 ~/Projects/whaletrail-lab/projects/whaletrail。先读 docs/ARENA.md。你自己就是模型，不要调用 OpenClaw，也不要另接一个付费 API 来解读帖子。

看现在的北京时间，只做对得上的一班。对不上就说明原因并停，不要补跑已经过了截单点的班。

早班（工作日 09:10 前后；发帖截单仍是 09:25，锁单最晚进场日 15:30）：
- 拉取 config/arena_roster.yaml 里还在赛的 KOL，以及你新发现、值得入赛的账号，自上一次游标以来的帖。
- 一帖一个 JSON，放进 results/kol_arena/inbox/。stance 只有 forward 才会买。收评、龙虎榜、融资榜、午评、盘中吹票标 recap。一帖超过 3 只标 list。evidence 必须是正文或 OCR 原文里的连续子串。对不上全称的外号不要猜代码。
- 另写一份 Grok 参赛单 results/kol_arena/inbox/grok-YYYYMMDD.json，post_id 为 grok:当天日期。最多 3 只。信息只能用到上一交易日收盘，不要看今天的开盘价。没有把握可以少写。
- 看到该参赛的新人：同样写成 JSON 丢进 inbox，不要只记笔记。在赛上限 100，由 arena-lock.py 决定收不收。
- 写完立刻跑 .venv/bin/python scripts/arena-lock.py。已出局或满员被拒绝的，不要改 yaml 硬插。
- 近 10 个交易日里已经 404 的帖子，跑 .venv/bin/python scripts/arena-mark-deleted.py <post_id>。订单留着。

15:40 班：
- 再收一轮新帖，包括午休和下午发的。这些帖进的是下一个交易日开盘，不当天成交。复盘标 recap，超过 3 只标 list。
- 不要再交一份 Grok 参赛单。
- 跑 arena-lock.py。已经入库的 post_id 不要改写。

21:10 班：
- 先跑 .venv/bin/python scripts/ashare-fetch-status.py --json。不健康就停，不要结算。
- 健康后跑 arena-settle.py，再跑 arena-status.py --json，把榜和出局的人用几句话报回来。
- 不要在这一班新写喊单。

不要改 whaletrail/arena 里的规则，不要为了「今天看起来不该买」删掉已经锁定的单。
```
