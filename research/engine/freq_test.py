# -*- coding: utf-8 -*-
"""
定投扣款频率对比: 每周 vs 双周 vs 每月 (同预算口径)
组合: 周期 AI(159819)+黄金(518880) 分档S3 | 基本 纳指(513100)+红利低波(512890) 1x
预算: 每标每月1000 (周=250/次, 双周=500/次, 月=1000/次), 70/30按标的数等权简化
规则: 前一日收盘打分 → 当日执行; 不透支; 闲置现金2%货基
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

CACHE = r"E:\autotest\autotest-script-devops\etf_scorer\ic_cache"
COST = 0.0015; CASH_RATE = 0.02
CYCLE = {"159819": "AI", "518880": "黄金"}
SLOW = {"513100": "纳指", "512890": "红利低波"}
START, END = "2020-08-17", "2026-08-15"
PREWARM = "2019-01-01"

def load(code):
    f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
    return pd.read_pickle(f) if os.path.exists(f) else None

def calc_scores(c):
    ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean(); std20 = c.rolling(20).std()
    mom60 = c.pct_change(60)*100; dev60 = (c/ma60-1)*100
    s_ma = np.where(dev60<-15,50,np.where(dev60<-5,40,np.where(dev60<5,25,np.where(dev60<15,10,0))))
    s_mom = np.where(mom60<-20,30,np.where(mom60<-5,20,np.where(mom60<5,12,np.where(mom60<20,5,0))))
    boll = ((c<ma20-2*std20).astype(float)*20 + ((c>=ma20-2*std20)&(c<ma20)).astype(float)*14 +
            ((c>=ma20)&(c<ma20+2*std20)).astype(float)*8)
    return pd.Series(s_ma+s_mom+boll, index=c.index)

def invest_dates(idx, freq):
    """freq: week=每周一, biweek=隔周周一, month=每月首个交易日"""
    s = pd.Series(idx)
    if freq == "month":
        p = idx.to_period("M")
        return set(idx[pd.Series(p).ne(pd.Series(p).shift()).values])
    if freq == "week":
        p = idx.to_period("W")
        return set(idx[pd.Series(p).ne(pd.Series(p).shift()).values])
    if freq == "biweek":
        p = idx.to_period("W")
        wk = pd.Series(p).ne(pd.Series(p).shift()).values
        weeks = list(dict.fromkeys(p[wk]))
        keep = set(weeks[::2])
        return set(idx[[w and pp in keep for w, pp in zip(wk, p)]])

def simulate(c, scores, freq, monthly=1000.0):
    per = {"week": monthly/4.33, "biweek": monthly/2.165, "month": monthly}[freq]
    dates = invest_dates(c.index, freq)
    cash, shares, nav_list, total_inv = 0.0, 0.0, [], 0.0
    start = c.index[0]
    cfs = []
    for dt in c.index:
        p = c.loc[dt]
        if dt in dates:
            sc = scores.get(dt, np.nan)  # 已外部shift(前日分)
            if np.isnan(sc): mult = 1.0
            elif sc >= 90: mult = 3.0
            elif sc >= 70: mult = 2.0
            elif sc >= 50: mult = 1.0
            else: mult = 0.25
            amt = min(per*mult, max(cash + per, 0.0))  # 本期预算到账后可投
            cash += per - amt
            if amt > 0 and p > 0:
                shares += amt*(1-COST)/p
                total_inv += amt
                cfs.append(((dt-start).days/365.25, -amt))
        cash *= (1 + CASH_RATE/252)
        nav_list.append(shares*p + cash)
    nav = pd.Series(nav_list, index=c.index)
    # IRR 二分
    cfs.append(((c.index[-1]-start).days/365.25, nav.iloc[-1]))
    def npv(r): return sum(cf/(1+r)**t for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo+hi)/2
        if npv(mid) > 0: lo = mid
        else: hi = mid
    irr = (lo+hi)/2
    dd = (nav/nav.cummax()-1).min()
    yrs = (c.index[-1]-start).days/365.25
    calmar = irr/abs(dd) if dd < 0 else np.nan
    return nav.iloc[-1], total_inv, irr, dd, calmar

data = {}
for code in list(CYCLE)+list(SLOW):
    df = load(code)
    full = df.loc[PREWARM:END].dropna()
    sim = df.loc[START:END].dropna()
    data[code] = (full, sim)
    print(f"{code} 模拟段 {sim.index[0].date()} ~ {sim.index[-1].date()} {len(sim)}天")

print("\n" + "="*80)
print(f"定投频率对比 ({START}~{END}) | 每标月预算1000元 | 周期标S3分档, 基本标1x")
print("="*80)
print("%-6s %10s %10s %10s %8s %10s %8s" % ("频率","总投入","总终值","总收益","IRR","最大回撤","Calmar"))
for freq, label in [("week","每周"),("biweek","双周"),("month","每月")]:
    t_inv = t_fin = 0.0
    irrs, dds = [], []
    for code, name in {**CYCLE, **SLOW}.items():
        full, sim = data[code]
        c = sim["close"]
        scores = calc_scores(full["close"]).loc[START:].shift(1) if code in CYCLE else pd.Series(50.0, index=c.index)
        fin, inv, irr, dd, cal = simulate(c, scores, freq)
        t_inv += inv; t_fin += fin; irrs.append(irr); dds.append(dd)
    # 组合层面: 用平均IRR和最深回撤近似
    print("%-6s %10.0f %10.0f %+10.0f %+7.2f%% %9.1f%% %8.2f" % (
        label, t_inv, t_fin, t_fin-t_inv, np.mean(irrs)*100, min(dds)*100,
        np.mean(irrs)/abs(min(dds))))
