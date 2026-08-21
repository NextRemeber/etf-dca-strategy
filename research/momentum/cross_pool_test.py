# -*- coding: utf-8 -*-
"""
交叉验证: 投法 × 标的池 正交分解
  A1 我们投法 × 我们池 (纳指/红利低波 + AI/黄金)   = 现状
  B1 动量投法 × 次方池 (创业板/纳指/日经/黄金)      = 上次对比B
  B2 动量投法 × 我们池 (纳指/红利低波/AI/黄金)     = 新增: 同池比投法
  A2 我们投法 × 次方池 (基本纳指+日经, 周期创业板+黄金) = 新增: 同池比投法
口径: 同总本金36万, 2020-08~2026-08, 成本0.15%, 现金2%
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from explore_more import prep, COST, CASH_RATE
from momentum_v2 import wslope, load

def sim_mom_lump(prices, lump, win=25, tp=0.03, cool_days=5):
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

def prep_prices(pool, ws, we):
    prices = None
    for c in pool:
        df = load(c)
        if df is None: return None
        s = df["close"]
        prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
    return prices.ffill().dropna().loc[ws:we]

def build_dca(pool_base, pool_cyc, ws, we, m_base=1750.0, m_cyc=750.0, rot=True):
    """我们投法: 基本1x + 周期S3 + 轮动30/15, 通用池"""
    idx = None
    px = prep_prices(pool_base + pool_cyc, ws, we)
    if px is None: return None
    idx = px.index
    scores = {}
    for c in px.columns:
        hist = px[c].dropna()
        sc = calc_scores_series(hist)
        scores[c] = sc.shift(1).reindex(idx)
    months = idx.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cash = 0.0; shares = {c: 0.0 for c in px.columns}
    nav_list = []
    for i, dt in enumerate(idx):
        p = {c: px.loc[dt, c] for c in px.columns}
        if is_first[i]:
            cash += 5000
            for c in pool_base:
                amt = min(m_base, max(cash, 0.0))
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            for c in pool_cyc:
                sc = scores[c].loc[dt]
                mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                amt = min(m_cyc * mult, max(cash, 0.0))
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
        if is_first[i] and rot:
            for grp, pct in [(pool_base, 0.30), (pool_cyc, 0.15)]:
                s = {}
                for c in grp:
                    v = scores[c].loc[dt]
                    if not np.isnan(v): s[c] = v
                if len(s) < 2: continue
                c1, c2 = list(s.keys())[:2]
                diff = abs(s[c1] - s[c2])
                if diff >= 5:
                    loser = c1 if s[c1] < s[c2] else c2
                    winner = c2 if loser == c1 else c1
                    mv = shares[loser] * p[loser]
                    if mv > 100:
                        sell = mv * pct
                        shares[loser] -= sell * (1 - COST) / p[loser]
                        cash += sell
                        buy = min(sell * (1 - COST), max(cash, 0.0))
                        if buy > 0 and p[winner] > 0:
                            shares[winner] += buy * (1 - COST) / p[winner]; cash -= buy
        cash *= (1 + CASH_RATE / 252)
        nav_list.append(cash + sum(shares[c] * p[c] for c in px.columns))
    return pd.Series(nav_list, index=idx)

def calc_scores_series(c):
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean(); std20 = c.rolling(20).std()
    mom60 = c.pct_change(60) * 100; dev60 = (c / ma60 - 1) * 100
    s_ma = np.where(dev60 < -15, 50, np.where(dev60 < -5, 40, np.where(dev60 < 5, 25, np.where(dev60 < 15, 10, 0))))
    s_mom = np.where(mom60 < -20, 30, np.where(mom60 < -5, 20, np.where(mom60 < 5, 12, np.where(mom60 < 20, 5, 0))))
    boll = ((c < ma20 - 2 * std20).astype(float) * 20 +
            ((c >= ma20 - 2 * std20) & (c < ma20)).astype(float) * 14 +
            ((c >= ma20) & (c < ma20 + 2 * std20)).astype(float) * 8)
    return pd.Series(s_ma + s_mom + boll.values, index=c.index)

def summarize(nav, lump, is_lump):
    dd = (nav / nav.cummax() - 1).min() * 100
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    if is_lump:
        ann = ((nav.iloc[-1] / lump) ** (1 / yrs) - 1) * 100
        return nav.iloc[-1], (nav.iloc[-1] / lump - 1) * 100, dd, ann
    else:
        months = nav.index.to_period("M")
        is_first = pd.Series(months).ne(pd.Series(months).shift()).values
        cfs = [((dt - nav.index[0]).days / 365.25, -5000.0) for dt, fl in zip(nav.index, is_first) if fl]
        cfs.append(((nav.index[-1] - nav.index[0]).days / 365.25, nav.iloc[-1]))
        def npv(r): return sum(cf / (1 + r) ** t for t, cf in cfs)
        lo, hi = -0.99, 10.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if npv(mid) > 0: lo = mid
            else: hi = mid
        return nav.iloc[-1], (nav.iloc[-1] / lump - 1) * 100, dd, (lo + hi) / 2 * 100

WS, WE, LUMP = "2020-08-17", "2026-08-15", 360000
OURS_POOL = ["513100", "512890", "159819", "518880"]   # 纳指/红利低波/AI/黄金
CF_POOL = ["159915", "513100", "513520", "518880"]     # 创业板/纳指/日经/黄金
NAMES = {"513100": "纳指", "512890": "红利低波", "159819": "AI", "518880": "黄金",
         "159915": "创业板", "513520": "日经"}

print("=" * 84)
print("投法 × 标的池 正交对比 (总本金36万, 2020-08~2026-08)")
print("=" * 84)
print("%-34s %12s %10s %10s %10s" % ("方案", "最终资产", "总收益", "回撤", "年化/IRR"))

# A1: 我们投法 × 我们池 (现状)
nav = build_dca(["513100", "512890"], ["159819", "518880"], WS, WE)
fin, tot, dd, irr = summarize(nav, LUMP, False)
print("%-34s %12s %+9.1f%% %9.1f%% %9.1f%% (IRR)" % ("A1 定投×我们池(现状)", format(fin, ",.0f"), tot, dd, irr))

# B1: 动量投法 × 次方池
px = prep_prices(CF_POOL, WS, WE)
nav, tr = sim_mom_lump(px, LUMP)
fin, tot, dd, ann = summarize(nav, LUMP, True)
print("%-34s %12s %+9.1f%% %9.1f%% %9.1f%% (年化, 换手%d)" % ("B1 动量×次方池", format(fin, ",.0f"), tot, dd, ann, tr))

# B2: 动量投法 × 我们池
px = prep_prices(OURS_POOL, WS, WE)
nav, tr = sim_mom_lump(px, LUMP)
fin, tot, dd, ann = summarize(nav, LUMP, True)
print("%-34s %12s %+9.1f%% %9.1f%% %9.1f%% (年化, 换手%d)" % ("B2 动量×我们池", format(fin, ",.0f"), tot, dd, ann, tr))

# A2: 我们投法 × 次方池 (基本=纳指+日经 1x, 周期=创业板+黄金 S3)
nav = build_dca(["513100", "513520"], ["159915", "518880"], WS, WE)
fin, tot, dd, irr = summarize(nav, LUMP, False)
print("%-34s %12s %+9.1f%% %9.1f%% %9.1f%% (IRR)" % ("A2 定投×次方池", format(fin, ",.0f"), tot, dd, irr))

print("")
print("结论矩阵:")
print("  同池比投法: A1 vs B2 (我们池) | A2 vs B1 (次方池)")
