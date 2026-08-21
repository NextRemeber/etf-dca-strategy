# -*- coding: utf-8 -*-
"""
纪律型策略探索 v3 (2026-08-21 重写):
  引擎统一收口到 backtest_core (本文件仅为实验驱动层, 不再持有 simulate 副本)。
  修正历史: 2026-08-21 修复卖出端成本符号 bug (旧引擎每笔卖出凭空 +V×COST,
  IRR 虚高 ~0.4pp / Calmar 虚高 ~0.07)。本文件所有数字均为修正后口径。

实验 (旧4池: 纳指/红利低波 + AI/黄金):
  基准: 区块内轮动30%/15%月度 | A 跨区块轮动(全局10%) | B 目标权重再平衡70/30
  C1 双周 | C2 季度 | D 动态权重
另附: v3.1 现行组合 (含豆粕+溢价闸门) 官方数字。

兼容导出: prep/BASE/CYC/ALL/COST/CASH_RATE/load_ohlcv/calc_scores
  (mixed_optimize / pool_orthogonal / momentum 系列脚本依赖)
"""
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_core import (  # noqa: F401
    prep as _prep, load_ohlcv, calc_scores,
    BASE, CYC, ALL, COST, CASH_RATE, LEGACY_CONFIG, V31_CODES,
    simulate, full_metrics, audit_conservation,
)


def prep(ws, we, codes=None, prewarm_days=300):
    """兼容签名: 默认旧4池 (预热线程 300 日, 2026-08-21 起规则要求)"""
    return _prep(ws, we, codes=ALL if codes is None else codes, prewarm_days=prewarm_days)


def _line(label, res):
    m = full_metrics(res)
    print("  %-30s IRR %+7.2f%%  回撤 %6.1f%%  Calmar %.2f" % (
        label, m["irr"] * 100, m["dd"] * 100, m["calmar"]))


LEGACY_POOL = dict(amounts=LEGACY_CONFIG["amounts"], graded=LEGACY_CONFIG["graded"],
                   rotate_groups=LEGACY_CONFIG["rotate_groups"], premium_gate={})


def run(ws, we, label):
    print(f"\n{label} ({ws}~{we}):")
    idx, data, f1, f2, f3 = prep(ws, we)
    cases = [
        ("基准: 区块内轮动30/15%月度", dict(freq="month")),
        ("A 跨区块轮动(全局10%)月度", dict(freq="month", cross=True)),
        ("B 目标权重再平衡70/30", dict(freq="month", target_w=True)),
        ("C1 双周轮动(幅度减半)", dict(freq="biweek")),
        ("C2 季度轮动(幅度加倍)", dict(freq="quarter")),
        ("D 动态权重40/60~60/40", dict(freq="month", dyn_w=True)),
        ("对照: 不轮动", dict(freq="month", rotate_groups=None)),
    ]
    for label_c, kw in cases:
        _line(label_c, simulate(idx, data, f1, f2, f3, cfg={**LEGACY_POOL, **kw}))


def run_v31(ws, we, label):
    """v3.1 现行组合: 5池+豆粕(不轮动)+溢价闸门 双界"""
    print(f"\n{label} ({ws}~{we}) [v3.1 现行组合]:")
    idx, data, f1, f2, f3 = _prep(ws, we, codes=V31_CODES)
    for lab, kw in [("无闸门(对照)", dict(premium_gate={})),
                    ("闸门·skip(保守界)", {}),
                    ("闸门·defl(乐观界)", dict(gate_mode="defl"))]:
        _line(lab, simulate(idx, data, f1, f2, f3, cfg=kw))


if __name__ == "__main__":
    run("2020-09-23", "2026-08-15", "完整窗口")   # 159819 上市日起
    run("2019-06-25", "2026-08-15", "子窗口(前周期, AI缺数据自动跳过)")
    run_v31("2020-09-23", "2026-08-15", "完整窗口")
    run_v31("2019-06-25", "2026-08-15", "子窗口(前周期)")
