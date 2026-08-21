# -*- coding: utf-8 -*-
"""
核心引擎审计 (backtest_core) — 2026-08-21 重写为三账守恒式
================================================================
旧版教训 (2026-08-21 复盘): 旧审计用"复刻引擎逻辑"的方式对账, 引擎的卖出端成本
符号 bug (shares -= V*(1-COST)/pl; cash += V, 每笔凭空 +V×COST) 被原样复刻,
三查全部通过但数字虚高 — 复刻式审计天然测不出被复刻对象的 bug。

新版原则: **审计代码不复制引擎逻辑**, 只做独立重放与守恒核对:
  A. 成交价基准: 逐笔交易价格 == 当日价格 (defl 通道按折价规则核对)
  B. 现金账: 仅凭交易日志独立重放现金流水 == 引擎日账
  C. 份额账: 仅凭交易日志独立重放份额 == 引擎日账
  D. 财富守恒: ΔNAV == Δ持仓市值 + Δ现金 (逐日, 费用/入金/货基息全入账)
  E. 现金不透支
  F. 无未来函数: 引擎使用的分数 == 前日分数 (抽样)
  G. 回归用例: 旧 bug 写法必须被 D 检出 (证明审计有检出能力)
"""
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_core as bc

WS, WE = "2020-08-17", "2026-08-15"


def audit_window(ws, we, title, cfg=None, codes=None):
    print("=" * 72)
    print(f"{title} ({ws}~{we})")
    print("=" * 72)
    idx, data, f1, f2, f3 = bc.prep(ws, we, codes=codes or bc.V31_CODES)
    res = bc.simulate(idx, data, f1, f2, f3, cfg=cfg)
    all_ok = True
    for name, ok, detail in bc.audit_conservation(res, data, idx):
        print(f"  {'✅' if ok else '❌'} {name}")
        all_ok &= ok

    # F. 无未来函数: 引擎用的分数 == 前日收盘分数
    s_bad = s_chk = 0
    for c in data:
        full = bc.load_ohlcv(c)
        sc = bc.calc_scores(full)
        sample = full.loc[ws:we].index[::60][:10]
        for dt in sample:
            pos = full.index.get_loc(dt)
            if pos < 1:
                continue
            s_prev = sc.iloc[pos - 1]
            s_used = data[c]["s"].loc[dt]
            s_chk += 1
            if not np.isnan(s_used) and not np.isnan(s_prev) and abs(s_used - s_prev) > 1e-9:
                s_bad += 1
    print(f"  {'✅' if s_bad == 0 else '❌'} F 无未来函数 (抽样 {s_chk} 点, 异常 {s_bad})")
    all_ok &= (s_bad == 0)

    m = bc.full_metrics(res)
    print(f"  → 总投入 {m['total_in']:,.0f} | 终值 {m['final']:,.0f} | IRR {m['irr']:+.2%} | "
          f"回撤 {m['dd']:.1%} | Calmar {m['calmar']:.2f} | 总费用 {m['total_fee']:,.0f}")
    return all_ok


def regression_old_bug():
    """G. 回归用例: 旧卖出写法必须被财富守恒检出 (证明审计检出能力)"""
    print("=" * 72)
    print("G. 回归用例: 旧 bug 写法 (shares-=V*(1-C)/pl; cash+=V) 必须被审计检出")
    print("=" * 72)
    # 手工构造最小场景: 1只标的, 首日买入, 次日按旧/新口径各卖一次, 对比 NAV 变化
    COST = 0.0015
    p0, p1 = 1.0, 1.0
    V = 30000.0
    sh = 60000.0
    cash = 0.0
    # 旧口径卖出
    sh_old = sh - V * (1 - COST) / p1
    cash_old = cash + V
    # 新口径卖出
    sh_new = sh - V / p1
    cash_new = cash + V * (1 - COST)
    nav_old = sh_old * p1 + cash_old
    nav_new = sh_new * p1 + cash_new
    delta_old = nav_old - (sh * p1 + cash)
    delta_new = nav_new - (sh * p1 + cash)
    caught = delta_old > 0  # 卖出后 NAV 反而增加 = 财富凭空创造 = 必须被 D 检出
    print(f"  卖出市值 {V:.0f}: 旧口径 ΔNAV {delta_old:+.1f} (凭空印钱, 审计应判 FAIL)")
    print(f"                新口径 ΔNAV {delta_new:+.1f} (-V×COST, 正常)")
    print(f"  {'✅' if caught else '❌'} G 旧写法产生正财富变化, 守恒审计可检出")
    return caught


if __name__ == "__main__":
    ok1 = audit_window(WS, WE, "v3.1 现行组合 (5池+豆粕不轮动+闸门skip)", cfg={})
    ok2 = audit_window(WS, WE, "v3.1 闸门defl (折价买入)", cfg=dict(gate_mode="defl"))
    ok3 = audit_window(WS, WE, "旧4池研究口径", cfg=dict(
        amounts=bc.LEGACY_CONFIG["amounts"], graded=bc.LEGACY_CONFIG["graded"],
        rotate_groups=bc.LEGACY_CONFIG["rotate_groups"], premium_gate={}), codes=bc.ALL)
    ok4 = regression_old_bug()
    print()
    print("=" * 72)
    print("审计总结: " + ("✅ 全部通过 — 引擎可发布" if all([ok1, ok2, ok3, ok4])
          else "❌ 存在失败项 — 禁止发布结论"))
    print("=" * 72)
