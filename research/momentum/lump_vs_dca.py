# -*- coding: utf-8 -*-
"""
用户口径对比: 同样总本金
  A 每月5000定投我们策略 (6年 = 36万)
  B 36万期初一次性全量入动量轮动池 (3%止盈+冷却, 全仓动量第一)
窗口: 2020-08 ~ 2026-08 (6年)
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from explore_more import prep, BASE, CYC, ALL, COST, CASH_RATE
from momentum_v2 import wslope, load

def simulate_ours_5000(idx, data, is_first):
    """我们策略: 每月5000 = 基本1750×2 + 周期750×2 + 轮动30/15"""
    cash = 0.0; shares = {c: 0.0 for c in ALL}
    nav_list = []
    for i, dt in enumerate(idx):
        if is_first[i]:
            cash += 5000
            for c in BASE:
                p = data[c]["p"].loc[dt]
                amt = min(1750.0, max(cash, 0.0))
                if amt > 0 and p > 0:
                    shares[c] += amt * (1 - COST) / p; cash -= amt
            for c in CYC:
                p = data[c]["p"].loc[dt]
                sc = data[c]["s"].loc[dt]
                mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                amt = min(750.0 * mult, max(cash, 0.0))
                if amt > 0 and p > 0:
                    shares[c] += amt * (1 - COST) / p; cash -= amt
        if is_first[i]:
            for grp, pct in [(BASE, 0.30), (CYC, 0.15)]:
                s = {}
                for c in grp:
                    v = data[c]["s"].loc[dt]
                    if not np.isnan(v): s[c] = v
                if len(s) < 2: continue
                c1, c2 = list(s.keys())[:2]
                diff = abs(s[c1] - s[c2])
                if diff >= 5:
                    loser = c1 if s[c1] < s[c2] else c2
                    winner = c2 if loser == c1 else c1
                    pl = data[loser]["p"].loc[dt]; pw = data[winner]["p"].loc[dt]
                    mv = shares[loser] * pl
                    if mv > 100:
                        sell = mv * pct
                        shares[loser] -= sell * (1 - COST) / pl
                        cash += sell
                        buy = min(sell * (1 - COST), max(cash, 0.0))
                        if buy > 0 and pw > 0:
                            shares[winner] += buy * (1 - COST) / pw; cash -= buy
        cash *= (1 + CASH_RATE / 252)
        nav = cash + sum(shares[c] * data[c]["p"].loc[dt] for c in ALL)
        nav_list.append(nav)
    return pd.Series(nav_list, index=pd.DatetimeIndex(idx))

def sim_mom_lump(prices, lump, win=25, tp=0.03, cool_days=5):
    """存量版动量轮动: 期初一次性入金 lump, 全仓动量第一, 3%止盈+冷却"""
    cash, hold, hold_sh, peak = float(lump), None, 0.0, 0.0
    cool = {}; nav_list, trades = [], 0
    for dt in prices.index:
        for c in list(cool):
            cool[c] -= 1
            if cool[c] <= 0: del cool[c]
        p = {c: prices.loc[dt, c] for c in prices.columns}
        if hold is not None and tp > 0:
            cur = p[hold]
            if cur > peak: peak = cur
            if cur / peak - 1 <= -tp:
                cash += hold_sh * cur * (1 - COST)
                cool[hold] = cool_days; hold, hold_sh, peak = None, 0.0, 0.0
        slopes = {}
        for c in prices.columns:
            if c in cool: continue
            hist = prices[c].loc[:dt].dropna()
            if len(hist) < win: continue
            slopes[c] = wslope(hist.tail(win))
        if slopes:
            best = max(slopes, key=slopes.get)
            if hold is not None and hold != best:
                cash += hold_sh * p[hold] * (1 - COST)
                cool[hold] = cool_days; hold, hold_sh, peak = None, 0.0, 0.0
            if hold is None and slopes[best] > 0 and cash > 100:
                hold = best; peak = p[best]
                hold_sh = cash * (1 - COST) / p[best]
                cash = 0.0; trades += 1
        nav_list.append(cash + (hold_sh * p[hold] if hold is not None else 0.0))
    return pd.Series(nav_list, index=prices.index), trades

def irr_inc(nav, start, monthly):
    months = nav.index.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cfs = [((dt - start).days / 365.25, -monthly) for dt, fl in zip(nav.index, is_first) if fl]
    cfs.append(((nav.index[-1] - start).days / 365.25, nav.iloc[-1]))
    def npv(r): return sum(cf / (1 + r) ** t for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0: lo = mid
        else: hi = mid
    return (lo + hi) / 2

def summarize(nav, start, total_in, is_lump):
    dd = (nav / nav.cummax() - 1).min()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    if is_lump:
        ann = (nav.iloc[-1] / total_in) ** (1 / yrs) - 1
        irr = ann
    else:
        irr = irr_inc(nav, start, total_in)
        ann = None
    return nav.iloc[-1], (nav.iloc[-1] / total_in - 1) * 100, dd * 100, ann, irr

# ============ 窗口与数据 ============
WINDOW = ("2020-08-17", "2026-08-15")
MONTHLY, YEARS = 5000, 6
LUMP = MONTHLY * 12 * YEARS  # 36万

# A: 我们策略 (每月5000)
idx, data, f1, *_ = prep(*WINDOW)
nav_a = simulate_ours_5000(idx, data, f1)
fin_a, tot_a, dd_a, ann_a, irr_a = summarize(nav_a, idx[0], MONTHLY, is_lump=False)

# B: 动量轮动池 (36万期初全量)
MOM_POOL = ["159915", "513100", "513520", "518880"]
prices = None
for c in MOM_POOL:
    df = load(c)
    s = df["close"]
    prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
prices = prices.ffill().dropna().loc[WINDOW[0]:WINDOW[1]]
nav_b, tr_b = sim_mom_lump(prices, LUMP)
fin_b, tot_b, dd_b, ann_b, irr_b = summarize(nav_b, prices.index[0], LUMP, is_lump=True)

print("=" * 82)
print("同总本金对比: A 每月5000定投(6年共%.0f万) vs B 期初一次性%.0f万全量入动量轮动池" % (LUMP/10000, LUMP/10000))
print("窗口: %s ~ %s | 成本0.15%%单边 | 现金2%%货基" % WINDOW)
print("=" * 82)
print("%-28s %14s %12s %10s %12s" % ("方案", "最终资产", "总收益", "最大回撤", "年化/IRR"))
print("%-28s %14s %11.1f%% %9.1f%% %11.1f%% (IRR)" % (
    "A 每月5000定投(我们策略)", format(fin_a, ",.0f"), tot_a, dd_a, irr_a * 100))
print("%-28s %14s %11.1f%% %9.1f%% %11.1f%% (年化)" % (
    "B 期初全量入动量池", format(fin_b, ",.0f"), tot_b, dd_b, ann_b * 100))
print("")
print("B vs A: 终值 %s, 总收益 %+.1fpp, 回撤 %+.1fpp" % (
    "高" if fin_b > fin_a else "低", tot_b - tot_a, dd_b - dd_a))

# B 的期初时点敏感性 (择时风险)
print("")
print("B 期初时点敏感性 (同样36万, 不同月份入场):")
for start in ["2020-08-17", "2020-11-02", "2021-06-01", "2022-01-04", "2023-01-03"]:
    px = prices.loc[start:]
    nb, trb = sim_mom_lump(px, LUMP)
    yrs_b = (px.index[-1] - px.index[0]).days / 365.25
    ann_b2 = (nb.iloc[-1] / LUMP) ** (1 / yrs_b) - 1
    dd_b2 = (nb / nb.cummax() - 1).min()
    print("  %s 入场: 终值 %s | 年化 %+.1f%% | 回撤 %.1f%%" % (
        start, format(nb.iloc[-1], ",.0f"), ann_b2 * 100, dd_b2 * 100))
