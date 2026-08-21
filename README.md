# ETF 定投量化策略 — 统一主仓库

![License](https://img.shields.io/badge/License-MIT-blue) ![Python](https://img.shields.io/badge/Python-3.8+-green) ![Status](https://img.shields.io/badge/Status-v3.1定稿-brightgreen)

> A股 ETF 月度定投 + 分数分档 + 再平衡轮动的完整量化体系。
> 生产脚本 / 回测引擎 / 研究记录 / 数据缓存 / 领域规则 统一管理。
> 经 25+ 轮系统性回测验证（双周期样本外 + 随机对照 + 参数平台检验 + 引擎审计）。

## 策略一句话

**5 只 ETF、70/30 区块隔离、每月初三个动作（分档定投 / 分数轮动 / 溢价闸门），无止盈点。**

## 组合与规则

| 区块 | 标的 | 月预算(月入1万) | 机制 |
|------|------|--------|------|
| 基本配置 70% | 纳指ETF (513100) + 红利低波ETF (512890) + 豆粕ETF (159985) | 各 2333 元 | 等额定投 + 分数轮动 30%（豆粕不参与轮动，商品保险） |
| 周期卫星 30% | AI ETF (159819) + 黄金ETF (518880) | 各 1500 元 | S3 分档 + 分数轮动 15% |

### 每月初执行三件事

```
① 定投  新钱按分数分档投入（周期卫星）
        ≥90分→3x │ ≥70分→2x │ ≥50分→1x │ <50分→0.25x
        分数 = MA60偏离(50) + 60日动量(30) + BOLL位置(20)（逆向指标，高分=超跌）

② 轮动  分差≥5 时，从低分者调仓到高分者（再平衡纪律）
        基本配置调 30% 持仓（仅纳指 vs 红利低波），周期卫星调 15% 持仓
        机制：同类资产的相对强弱切换收割（再平衡溢价）

③ 闸门  纳指溢价 >8% 时改场外申购（按净值成交，非停买）
```

## 仓库结构

```
etf-quant/
├── strategy/            # 生产脚本（唯一运行入口）
│   ├── final_strategy.py        # 每日日报（定时任务 11:00 调用）
│   └── etf_quant_strategy.py    # 行情/指数库
├── research/            # 回测引擎与研究脚本
│   ├── engine/          # 核心引擎（已过引擎三查审计）
│   │   ├── explore_more.py      # 核心定投引擎（共享现金池+分档+轮动）
│   │   ├── mixed_optimize.py    # 混合场景引擎（存量+增量参数化）
│   │   ├── engine_audit.py      # 引擎审计三查
│   │   ├── pool_orthogonal.py   # 池口径正交分解
│   │   ├── reserve_engine.py    # 现金储备制引擎
│   │   ├── freq_test.py         # 定投频率对比
│   │   └── fetch_ohlcv.py       # 数据拉取（腾讯 fqkline）
│   └── momentum/        # 动量轮动研究系列（外部策略复刻/证伪）
├── data/
│   └── ic_cache/        # 前复权K线缓存（腾讯，fetch_ohlcv 可重建）
├── rules/               # dev-harness 领域规则副本（权威源在 E:/dev-harness/domains/etf-quant/）
├── docs/
│   ├── 01-策略体系.md
│   ├── 02-验证报告.md
│   ├── 03-执行手册.md
│   └── 04-最终方案-v3.1.md
└── README.md
```

## 快速开始

```bash
# 每日日报（定时任务 11:00 自动调用）
C:/Anaconda3/python strategy/final_strategy.py --monthly-budget 3000

# 数据更新（重新拉取全部历史K线）
C:/Anaconda3/python research/engine/fetch_ohlcv.py

# 引擎审计（新引擎发布前必跑）
C:/Anaconda3/python research/engine/engine_audit.py
```

## 核心结论速查（详见 docs/04-最终方案-v3.1.md）

- 合理年化 8-12%（回测含 2025 特殊行情），定投口径回撤 -8.3%
- 已证伪方向：动量轮动/止盈/机械换标/自动卖出/顶部预测/触发加投 等 16 项（防重复研究）
- 引擎三查：当日价成交 / 分数 shift(1) / 现金恒等式（组合级 IRR ÷ 组合级回撤）

## 免责声明

本仓库仅供量化研究学习，不构成投资建议。历史回测不代表未来收益。
