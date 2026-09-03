# 外部数据契约

## 逐合约日线 `bars.csv`

每行是一张实际月份合约的一个交易日，必需字段：

| 字段 | 说明 |
|---|---|
| `trading_day` | 交易所交易日，`YYYY-MM-DD` |
| `event_time` | 该日行情经济上完成的时间 |
| `available_at` | 系统最早稳定获得该记录的时间，不得早于 `event_time` |
| `symbol`,`product` | 实际合约代码与品种代码 |
| `open`,`high`,`low`,`close`,`settlement` | 未复权实际合约价格 |
| `volume`,`open_interest` | 成交量与持仓量 |
| `upper_limit`,`lower_limit` | 当日涨跌停价，可选但强烈建议提供 |
| `source_version` | 数据来源/批次版本，不得为空 |

同一 `trading_day + symbol` 不得重复。主力连续或复权拼接序列不能作为本文件输入。

## 合约规格 `contract_specs.csv`

必需字段：`symbol,product,exchange,multiplier,tick_size,margin_rate,maintenance_margin_rate,price_limit_rate,last_trade_date,effective_from`。

可选字段：`effective_to,open_fee_rate,close_fee_rate,close_today_fee_rate,settlement_type,version`。

同一合约可以有多行规格版本，但任一交易日必须恰好解析到一个生效版本。保证金和费用应采用实际研究目的对应的期货公司标准，而不是为了提高回测收益而使用最低值。

## 交易日与夜盘日历

日内时间戳必须同时依赖两份日历：交易所交易日日历，以及明确记录“哪些自然日实际开夜盘”的夜盘日历。周五夜盘可以归入下周一交易日；法定节假日前取消夜盘不能仅凭工作日推断，必须来自交易所公告。夜盘日历缺失时，系统对日内数据应当 fail closed。

## 逐日结算参数 `daily_parameters.csv`

正式回测必须提供：`trading_day,symbol,settlement_price,spec_long_margin_rate,spec_short_margin_rate,source_version`。

费用同时支持比例和按手两种口径：`trade_fee_rate,trade_fee_per_lot,close_today_fee_rate,close_today_fee_per_lot`。未提供逐日参数时CLI只会运行`static_fallback`研究模式，不得作为正式历史证据。
