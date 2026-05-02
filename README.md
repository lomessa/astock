# AStock - 个人短线打板量化框架

面向手动交易的 A 股短线量化系统，基于 **AKShare** 免费数据，聚焦**首板、连板接力、炸板回封、竞价超预期**等短线策略。

## 特性

- **零费用数据**：全部基于 AKShare，无需付费 API Key
- **多策略信号**：首板 / 连板 / 炸板回封 / 竞价超预期
- **情绪周期**：自动计算涨停数、炸板率、连板高度、昨日溢价，给出仓位建议
- **风控体系**：止损止盈自动计算、仓位建议、黑名单、市值过滤
- **手动交易优先**：生成交易计划 → 人工确认 → 手机推送，不自动下单
- **持仓跟踪**：JSON 本地持久化，盘中止损止盈预警
- **多端推送**：支持钉钉 / 飞书 / 企业微信 Webhook

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置推送（可选）

```bash
cp .env.example .env
# 编辑 .env 填入你的钉钉/飞书 Webhook
```

### 1. 查看市场情绪

```bash
python -m astock.cli.main sentiment
```

### 2. 扫描今日信号

```bash
python -m astock.cli.main scan
```

### 3. 生成交易计划并保存

```bash
python -m astock.cli.main plan
```

### 4. 推送交易计划到手机

```bash
python -m astock.cli.main push
```

### 5. 持仓管理与预警

```bash
# 查看持仓
python -m astock.cli.main hold

# 手动录入买入（涨停价买入 1000 股）
python -m astock.cli.main buy 000001 --price 10.5 --volume 1000 --stop-loss 9.5 --stop-profit 12.0

# 手动录入卖出
python -m astock.cli.main sell 000001 --price 11.0
```

### 6. 一键运行（研究/定时任务）

```bash
python main.py
```

## 项目结构

```
astock/
├── config/          # 配置管理（支持 .env 覆盖）
├── data/            # AKShare 数据封装 + 本地缓存
├── strategy/        # 策略基类 + 打板策略集合
├── signal/          # 信号引擎 + 交易计划数据结构
├── sentiment/       # 市场情绪分析
├── risk/            # 风控与仓位管理
├── execution/       # 持仓跟踪 + 交易计划持久化
├── notify/          # 钉钉/飞书/企微推送
├── cli/             # Click 命令行工具
├── utils/           # 交易日工具
└── backtest/        # 简易回测（TODO）

tests/               # 单元测试
notebook/            # Jupyter 研究示例
```

## 核心策略说明

| 策略 | 逻辑 | 适用时段 |
|------|------|----------|
| **首板** | 昨日未涨停，今日首次封板，流通市值小、换手适中、封单大 | 盘中/盘后 |
| **连板接力** | 2-5 连板，封单金额大、开板次数少，不做最高标 | 盘中/盘后 |
| **炸板回封** | 盘中炸板后再次封板，筹码交换充分 | **盘中** |
| **竞价超预期** | 昨日烂板今日高开，量能放大（需 Level-2 精确） | 竞价时段 |

## 注意事项

1. **本框架仅供研究与学习，不构成投资建议。**
2. 手动交易模式下，所有买卖决策需人工确认，框架只负责**筛选、预警、推送**。
3. AKShare 数据有一定延迟，盘中高频交易需接入券商 Level-2。
4. 打板策略风险极高，建议先用小资金验证策略有效性。

## License

MIT
