# -*- coding: utf-8 -*-
"""
正交分解: 现金池口径(独立/共享) × 轮动(有/无) × 全S3分档
  A 独立池+无轮动  (复现 combine_test 4.26?)
  B 共享池+无轮动  (复现 rotate_alls3 2.18)
  C 独立池+有轮动  (新: 定投入账独立, 轮动资金共享)
  D 共享池+有轮动  (复现 rotate_alls3 2.60)
月预算10000 = 基本3500×2 + 周期1500×2, 正确引擎当日价成交
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from explore_more import prep, BASE, CYC, ALL, COST, CASH_RATE

def sim_pool(idx, data, is_first, pool_mode="shared", rotate=False):
    """pool_mode: shared=共享现金池 | indep=独立现金池(定投入账独立)
    rotate=True: 月度轮动 (分差>=5, 基本30%/周期15%), 轮动资金共享"""
    if pool_mode == "shared":
        cash = 0.0
    else:
        cash = {c: 0.0 for c in ALL}
    shares = {c: 0.0 for c in ALL}
    nav_list = []
    rot_cash = 0.0  # 轮动资金池 (独立模式下卖出转入)
    for i, dt in enumerate(idx):
        p = {c: data[c]["p"].loc[dt] for c in ALL}
        if is_first[i]:
            if pool_mode == "shared":
                cash += 10000
                for c in BASE:
                    amt = min(3500.0, max(cash, 0.0))
                    if amt > 0 and p[c] > 0:
                        shares[c] += amt * (1 - COST) / p[c]; cash -= amt
                for c in CYC:
                    sc = data[c]["s"].loc[dt]
                    mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                    amt = min(1500.0 * mult, max(cash, 0.0))
                    if amt > 0 and p[c] > 0:
                        shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            else:  # indep: 每标的独立入账
                for c in BASE:
                    cash[c] += 3500.0
                    amt = min(3500.0, max(cash[c], 0.0))
                    if amt > 0 and p[c] > 0:
                        shares[c] += amt * (1 - COST) / p[c]; cash[c] -= amt
                for c in CYC:
                    cash[c] += 1500.0
                    sc = data[c]["s"].loc[dt]
                    mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                    amt = min(1500.0 * mult, max(cash[c], 0.0))
                    if amt > 0 and p[c] > 0:
                        shares[c] += amt * (1 - COST) / p[c]; cash[c] -= amt
        if is_first[i] and rotate:
            for grp, pct in [(BASE, 0.30), (CYC, 0.15)]:
                s = {}
                for c in grp:
                    v = data[c]["s"].loc[dt]
                    if not np.isnan(v): s[c] = v
                if len(s) < 2: continue
                c1, c2 = list(s.keys())[:2]
                if abs(s[c1] - s[c2]) >= 5:
                    loser = c1 if s[c1] < s[c2] else c2
                    winner = c2 if loser == c1 else c1
                    pl = p[loser]; pw = p[winner]
                    mv = shares[loser] * pl
                    if mv > 100:
                        sell = mv * pct
                        shares[loser] -= sell * (1 - COST) / pl
                        rot_cash += sell  # 轮动资金进公共池
                        # 买入: 用公共池 + 各自现金
                        if pool_mode == "shared":
                            avail = cash + rot_cash
                        else:
                            avail = cash[winner] + rot_cash
                        buy = min(sell * (1 - COST), max(avail, 0.0))
                        if buy > 0 and pw > 0:
                            shares[winner] += buy * (1 - COST) / pw
                            rot_cash -= buy
                            if rot_cash < 0:
                                if pool_mode == "shared":
                                    cash -= -rot_cash
                                else:
                                    cash[winner] -= -rot_cash
                                rot_cash = 0.0
        # 现金计息
        if pool_mode == "shared":
            cash *= (1 + CASH_RATE / 252)
        else:
            for c in ALL:
                cash[c] *= (1 + CASH_RATE / 252)
        rot_cash *= (1 + CASH_RATE / 252)
        if pool_mode == "shared":
            nav = cash + rot_cash + sum(shares[c] * p[c] for c in ALL)
        else:
            nav = sum(cash[c] for c in ALL) + rot_cash + sum(shares[c] * p[c] for c in ALL)
        nav_list.append(nav)
    nav_s = pd.Series(nav_list, index=pd.DatetimeIndex(idx))
    # IRR
    start = idx[0]
    months = pd.Series(idx).dt.to_period("M")
    f1 = months.ne(months.shift()).values
    cfs = [((dt - start).days / 365.25, -10000.0) for dt, fl in zip(idx, f1) if fl]
    cfs.append(((idx[-1] - start).days / 365.25, nav_s.iloc[-1]))
    def npv(r): return sum(cf / (1 + r) ** t for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0: lo = mid
        else: hi = mid
    irr = (lo + hi) / 2
    dd = (nav_s / nav_s.cummax() - 1).min()
    return nav_s, irr, dd

idx, data, f1, *_ = prep("2020-08-17", "2026-08-15")

print("=" * 78)
print("正交分解: 现金池口径 × 轮动 (全S3分档, 2020-08~2026-08)")
print("=" * 78)
print("%-28s %12s %9s %9s %8s" % ("方案", "终值", "IRR", "回撤", "Calmar"))
for label, pm, rot in [
    ("A 独立池+无轮动", "indep", False),
    ("B 共享池+无轮动", "shared", False),
    ("C 独立池+有轮动", "indep", True),
    ("D 共享池+有轮动", "shared", True),
]:
    nav, irr, dd = sim_pool(idx, data, f1, pool_mode=pm, rotate=rot)
    cal = irr / abs(dd) if dd < 0 else 0
    print("%-28s %12.0f %+7.2f%% %8.1f%% %8.2f" % (label, nav.iloc[-1], irr * 100, dd * 100, cal))

print("")
print("解读:")
print("  A vs B = 池口径差异 (无轮动) | C vs D = 池口径差异 (有轮动)")
print("  A vs C = 轮动贡献 (独立池)   | B vs D = 轮动贡献 (共享池)")
