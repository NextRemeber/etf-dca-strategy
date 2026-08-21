# ETF 量化定投领域（etf-quant）

> dev-harness 领域：A股 ETF 定投策略（D:/AI_GEN/etf-quant 生产脚本 + E:/autotest/autotest-script-devops/etf_scorer 研究脚本）

## 导航

| 规则文件 | 何时读 |
|---------|--------|
| `rules/backtest-engine.md` | **写/改回测脚本前必读**（现金口径/IRR/未来函数/已知 bug） |
| `rules/validation-protocol.md` | **下策略结论前必读**（四件套/防锚定/结论格式） |
| `rules/strategy-conclusions.md` | **做新研究前必读**（已定论清单，防重复研究） |
| `rules/data-ops.md` | 涉及行情/缓存/批量请求时 |

## 核心命令

```bash
# 生产日报（每天 11:00 定时任务调用）
C:/Anaconda3/python D:/AI_GEN/etf-quant/final_strategy.py --monthly-budget 3000

# 查踩坑教训
python E:/dev-harness/scripts/learnings-manager.py recall "<关键词>"

# 踩坑导入
python E:/dev-harness/scripts/learnings-manager.py ingest .learnings/current.md
```

## 目录地图

- `D:/AI_GEN/etf-quant/final_strategy.py` — 生产日报脚本（策略唯一落地处）
- `D:/AI_GEN/etf-quant/etf_quant_strategy.py` — 行情/指数库（fetch_realtime 等）
- `E:/autotest/autotest-script-devops/etf_scorer/` — 研究脚本 + `ic_cache/` 数据缓存
- `E:/workspace/etf-dca-strategy/` — GitHub 开源仓库工作副本（NextRemeber/etf-dca-strategy）
- `.hermes/scripts/etf_trading_hours.py` — 盘中提醒脚本（BOLL20 突破，独立于本策略）

## 关键坑位提醒（详见 rules）

1. 回测引擎现金口径错误 → IRR 虚高 5.51pp（免费印钱）
2. "调节无效"结论的泛化边界——只对 BOLL/ATR 成立，S3 分档对基本配置有效（Calmar 2.37→4.26）
3. 新结论必须三窗口 + 前周期 + 随机对照 + 灵敏度四件套
