# 中国期货量化研究系统

这是一套面向中国境内期货市场的、研究优先且失败关闭的量化系统。它不是把股票系统加上杠杆，而是把实际月份合约、换月、保证金、逐日结算、涨跌停和交割边界作为系统核心。

当前版本提供一个不依赖网络的、可验证的最小完整闭环：

- 版本化合约规格与实际合约行情；
- 数据质量闸门和可交易合约选择；
- 日频趋势信号、波动率风险预算与移仓计划；
- 次日开盘成交、部分成交、涨跌停拒单和滑点；
- 多空持仓、手续费、保证金、逐日盯市与强平缓冲；
- 预测、策略、执行三本独立账本；
- 候选策略注册与人工晋级门槛；
- 合成数据端到端演示和单元测试。

## 快速开始

```powershell
$env:PYTHONPATH = "src"
python -m futures_quant.cli demo
python -m pytest -q
```

演示只使用明确标注的合成数据，不代表任何真实收益。输出写入 `artifacts/demo/`。

接入外部逐合约数据：

```powershell
python -m futures_quant.cli backtest `
  --bars data/bars.csv `
  --specs data/contract_specs.csv `
  --daily-parameters data/daily_parameters.csv
```

真实数据接入从显式日历与有门禁的采集开始：

```powershell
python -m futures_quant.cli import-calendar `
  --input path/to/calendar.json `
  --source-label "明确的数据来源与版本"

python -m futures_quant.cli ingest-shfe `
  --start 2024-01-02 `
  --end 2024-12-31
```

只有采集报告状态为 `published`、逐日结算参数完整且数据质量门禁通过，输出才允许进入正式回测。网络或覆盖率失败会生成审计报告并阻断，不会回退为合成数据。

CSV字段契约见 [数据契约](docs/DATA_CONTRACT.md)。

详细边界见 [系统总规范](docs/MASTER_SPEC.md)、[架构决策](docs/DECISIONS.md)和[用户反馈模型](docs/USER_FEEDBACK_MODEL.md)。
