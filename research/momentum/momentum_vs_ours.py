# -*- coding: utf-8 -*-
"""
公平对比: 每月10000预算
  A 我们的策略: 基本70%(纳指+红利低波 1x) + 周期30%(AI+黄金 S3) + 月度轮动30/15
  B 动量轮动池(增量版): 每月入账10000, 3%止盈+冷却, 全仓持有动量第一
口径统一: 共享现金池 + 2%货基 + 0.15%成本
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from explore_more import prep, BASE, CYC, ALL, COST, CASH_RATE
from momentum_v2 import wslope, load

def simulate_ours(idx, data, is_first):
    cash = 0.0; shares = {c: 0.0 for c in ALL}
    nav_list = []
    for i, dt in enumerate(idx):
        if is_first[i]:
            cash += 10000
            for c in BASE:
                p = data[c]["p"].loc[dt]
                amt = min(3500.0, max(cash, 0.0))
                if amt > 0 and p > 0:
                    shares[c] += amt * (1 - COST) / p; cash -= amt
            for c in CYC:
                p = data[c]["p"].loc[dt]
                sc = data[c]["s"].loc[dt]
                mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                amt = min(1500.0 * mult, max(cash, 0.0))
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

def sim_mom(prices, win=25, tp=0.03, cool_days=5, monthly=10000):
    months = prices.index.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cash, hold, hold_sh, peak = 0.0, None, 0.0, 0.0
    cool = {}; nav_list, trades = [], 0
    for i, dt in enumerate(prices.index):
        if is_first[i]: cash += monthly
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

def metrics(nav, start):
    total = nav.iloc[-1] / nav.iloc[0] - 1
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    dd = (nav / nav.cummax() - 1).min()
    months = nav.index.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cfs = [((dt - start).days / 365.25, -10000.0) for dt, fl in zip(nav.index, is_first) if fl]
    cfs.append(((nav.index[-1] - start).days / 365.25, nav.iloc[-1]))
    def npv(r): return sum(cf / (1 + r) ** t for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0: lo = mid
        else: hi = mid
    irr = (lo + hi) / 2
    return total, ann, dd, ann / abs(dd) if dd < 0 else np.nan, irr

idx, data, f1, *_ = prep("2020-08-17", "2026-08-15")
nav_ours = simulate_ours(idx, data, f1)
tot, ann, dd, cal, irr = metrics(nav_ours, idx[0])
print("=" * 78)
print("公平对比 (2020-08~2026-08, 每月10000, 共享现金池+2%货基+0.15%成本)")
print("=" * 78)
print("A 我们现状(基本1x+周期S3+轮动30/15):")
print("   终值 %s | IRR %+.1f%% | 年化(资产) %.1f%% | 回撤 %.1f%% | Calmar %.2f" % (
    format(nav_ours.iloc[-1], ",.0f"), irr*100, ann*100, dd*100, cal))

MOM_POOL = ["159915", "513100", "513520", "518880"]
prices = None
for c in MOM_POOL:
    df = load(c)
    s = df["close"]
    prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
prices = prices.ffill().dropna().loc["2020-08-17":"2026-08-15"]
nav_mom, tr = sim_mom(prices)
tot, ann, dd, cal, irr = metrics(nav_mom, prices.index[0])
print("")
print("B 动量轮动池(增量版, 3%止盈+冷却, 全仓动量第一, 4标的):")
print("   终值 %s | IRR %+.1f%% | 年化(资产) %.1f%% | 回撤 %.1f%% | Calmar %.2f | 换手%d次" % (
    format(nav_mom.iloc[-1], ",.0f"), irr*100, ann*100, dd*100, cal, tr))

print("")
print("分年度对比:")
print("%-6s %12s %12s" % ("年份", "我们策略", "动量轮动"))
for yr in ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]:
    s1 = nav_ours.loc[yr]; s2 = nav_mom.loc[yr]
    r1 = s1.iloc[-1] / s1.iloc[0] - 1
    r2 = s2.iloc[-1] / s2.iloc[0] - 1
    print("%-6s %+11.1f%% %+11.1f%%" % (yr, r1*100, r2*100))
