# Data Layer

| 模块 | 说明 |
|------|------|
| `base.py` | `DataSource` 抽象基类 |
| `yfinance_source.py` | Yahoo Finance 日线 + Parquet 缓存 |
| `baostock_source.py` | A股全市场日 K（OHLCV + turn/ST/PE/PB）+ 证券基本资料 + 申万一级 + 指数成分 |
| `tvscreener_source.py` | TradingView Scanner HTTP 端点（用于 watchlist 快照） |
| `watchlist.py` | YAML/JSON 关注列表加载 |
| `cache.py` | Parquet 本地缓存（append/upsert） |
| `symbols.py` | 标的解析与校验（禁止 A 股/港股） |

所有数据源都实现 `DataSource.get_daily(symbol, start, end) -> DataFrame`。
