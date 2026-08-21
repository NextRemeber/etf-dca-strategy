# -*- coding: utf-8 -*-
"""策略层提案实验 (2026-08-21, 只产数字不改策略):
  实验1 溢价闸门资金再利用: gated 纳指预算改道买入红利低波 (redirect) vs skip vs 无闸门
  实验2 豆粕权重扫描: 0% ~ 23% 的 IRR/回撤/Calmar 代价-收益曲线
  实验3 子窗口复核 (2019-06 起)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_core as bc

WS, WE = "2020-09-23", "2026-08-15"


def v31_amounts(soy):
    """豆粕月预算 = soy, 其余 7000-soy 由纳指/红利低波均分, 周期 3000 不变"""
    rest = 7000.0 - soy
    return {"513100": rest / 2, "512890": rest / 2, "159985": soy,
            "159819": 1500.0, "518880": 1500.0}


def run(idx, data, f1, f2, f3, cfg):
    r = bc.simulate(idx, data, f1, f2, f3, cfg=cfg)
    m = bc.full_metrics(r)
    ok = all(x for _, x, _ in bc.audit_conservation(r, data, idx))
    return m, ok


for ws, we, tag in [(WS, WE, "完整窗口"), ("2019-06-25", WE, "子窗口")]:
    idx, data, f1, f2, f3 = bc.prep(ws, we)
    print("=" * 76)
    print(f"{tag} ({ws}~{we})")
    print("=" * 76)

    print("实验1 闸门资金再利用 (豆粕 2333 固定):")
    for label, cfg in [
        ("无闸门            ", dict(premium_gate={})),
        ("skip  (现金趴货基) ", {}),
        ("defl  (折价买入)   ", dict(gate_mode="defl")),
        ("redirect(改道红利)", dict(gate_mode="redirect", gate_redirect_to="512890")),
    ]:
        m, ok = run(idx, data, f1, f2, f3, cfg)
        print("  %s  IRR %+6.2f%%  回撤 %5.1f%%  Calmar %.2f  审计:%s" % (
            label, m["irr"] * 100, m["dd"] * 100, m["calmar"], "PASS" if ok else "FAIL"))

    print("实验2 豆粕权重扫描 (无闸门, 隔离豆粕本身):")
    for soy, pct in [(0.0, "0% (四池)"), (500.0, "~7%"), (1000.0, "~14%"), (1500.0, "~21%"), (2333.33, "23% (现行)")]:
        cfg = dict(amounts=v31_amounts(soy),
                   graded={"159819", "518880"}, premium_gate={},
                   rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)])
        if soy == 0.0:
            cfg["amounts"].pop("159985")
        m, ok = run(idx, data, f1, f2, f3, cfg)
        print("  豆粕%-10s IRR %+6.2f%%  回撤 %5.1f%%  Calmar %.2f  审计:%s" % (
            pct, m["irr"] * 100, m["dd"] * 100, m["calmar"], "PASS" if ok else "FAIL"))
    print()
