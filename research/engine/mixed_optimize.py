# -*- coding: utf-8 -*-
"""
混合场景寻优 (存量20万 + 每月5000 + 月度轮动):
  实验1 区块比例: 基本/周期 50/50 ~ 90/10
  实验2 轮动策略: 分差阈值 × 调仓比例 × 是否轮动
  实验3 选标策略: 周期池/基本池 候选组合
指标: 混合IRR / 最大回撤 / Calmar(IRR/|DD|)
正确引擎: 当日价成交, 共享现金池, 0.15%双边, 2%货基
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 仓库引擎目录
from explore_more import prep, COST, CASH_RATE

def load_pool(codes, ws, we):
    """构建统一 prices + 前日分数 (复用 explore_more 风格)"""
    import explore_more
    idx = None
    for c in codes:
        cc = explore_more.load_ohlcv(c).loc[ws:we].dropna()
        if isinstance(cc, pd.DataFrame): cc = cc["close"]
        idx = cc.index if idx is None else idx.union(cc.index)
    idx = sorted(idx)
    data = {}
    for c in codes:
        cc = explore_more.load_ohlcv(c).loc[ws:we].dropna()
        if isinstance(cc, pd.DataFrame): cc = cc["close"]
        data[c] = {
            "p": cc.reindex(idx).ffill(),
            "s": explore_more.calc_scores(cc).shift(1).reindex(idx).ffill(),
        }
    months = pd.Series(idx).dt.to_period("M")
    is_first = months.ne(months.shift()).values
    return idx, data, is_first

def sim_mixed_param(idx, data, is_first, pool_base, pool_cyc,
                    base_pct=0.70, lump=200000, monthly=5000,
                    rot_th=5, rot_pct_base=0.30, rot_pct_cyc=0.15):
    """混合: 存量建仓(按区块比例) + 月度定投(按区块比例) + 月度轮动"""
    allc = pool_base + pool_cyc
    cash = float(lump)
    shares = {c: 0.0 for c in allc}
    nav_list = []
    built = False
    for i, dt in enumerate(idx):
        p = {c: data[c]["p"].loc[dt] for c in allc}
        if not built and cash > 0:
            for c in pool_base:
                amt = lump * base_pct / len(pool_base)
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            for c in pool_cyc:
                amt = lump * (1 - base_pct) / len(pool_cyc)
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            built = True
        if is_first[i]:
            cash += monthly
            for c in pool_base:
                amt = min(monthly * base_pct / len(pool_base), max(cash, 0.0))
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            for c in pool_cyc:
                sc = data[c]["s"].loc[dt]
                mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                amt = min(monthly * (1 - base_pct) / len(pool_cyc) * mult, max(cash, 0.0))
                if amt > 0 and p[c] > 0:
                    shares[c] += amt * (1 - COST) / p[c]; cash -= amt
            if rot_th is not None:
                for grp, pct in [(pool_base, rot_pct_base), (pool_cyc, rot_pct_cyc)]:
                    s = {}
                    for c in grp:
                        v = data[c]["s"].loc[dt]
                        if not np.isnan(v): s[c] = v
                    if len(s) < 2: continue
                    c1, c2 = list(s.keys())[:2]
                    if abs(s[c1] - s[c2]) >= rot_th:
                        loser = c1 if s[c1] < s[c2] else c2
                        winner = c2 if loser == c1 else c1
                        pl = p[loser]; pw = p[winner]
                        mv = shares[loser] * pl
                        if mv > 100:
                            sell = mv * pct
                            # 2026-08-21 修复: 份额减 sell/pl, 现金入 sell*(1-COST) (旧写法凭空印钱)
                            shares[loser] -= sell / pl
                            cash += sell * (1 - COST)
                            buy = min(sell * (1 - COST), max(cash, 0.0))
                            if buy > 0 and pw > 0:
                                shares[winner] += buy * (1 - COST) / pw; cash -= buy
        cash *= (1 + CASH_RATE / 252)
        nav_list.append(cash + sum(shares[c] * p[c] for c in allc))
    nav = pd.Series(nav_list, index=pd.DatetimeIndex(idx))
    # IRR
    start = idx[0]
    cfs = [((dt - start).days / 365.25, -lump) for dt in [idx[0]]]
    cfs += [((dt - start).days / 365.25, -monthly) for dt, fl in zip(idx[1:], is_first[1:]) if fl]
    cfs.append(((idx[-1] - start).days / 365.25, nav.iloc[-1]))
    def npv(r): return sum(cf / (1 + r) ** t for t, cf in cfs)
    lo, hi = -0.99, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0: lo = mid
        else: hi = mid
    irr = (lo + hi) / 2
    dd = (nav / nav.cummax() - 1).min()
    return irr, dd

def show(tag, rows, ws, we):
    print("=" * 76)
    print("%s (%s~%s, 存量20万+每月5000)" % (tag, ws, we))
    print("=" * 76)
    print("%-34s %9s %9s %8s" % ("方案", "IRR", "回撤", "Calmar"))
    for label, irr, dd in rows:
        cal = irr / abs(dd) if dd < 0 else np.nan
        print("%-34s %+7.2f%% %7.1f%% %8.2f" % (label, irr * 100, dd * 100, cal))

WS, WE = "2020-08-17", "2026-08-15"
BASE_CUR = ["513100", "512890"]   # 纳指/红利低波
CYC_CUR = ["159819", "518880"]    # AI/黄金

# ============ 实验1: 区块比例 ============
print("实验1 区块比例 (基本/周期, 轮动分差5, 基本30%/周期15%)")
rows = []
for bp in [0.50, 0.60, 0.70, 0.80, 0.90]:
    idx, data, f1 = load_pool(BASE_CUR + CYC_CUR, WS, WE)
    irr, dd = sim_mixed_param(idx, data, f1, BASE_CUR, CYC_CUR, base_pct=bp)
    rows.append(("基本%d%%/周期%d%%" % (bp * 100, (1 - bp) * 100), irr, dd))
show("实验1 区块比例", rows, WS, WE)

# ============ 实验2: 轮动策略 ============
print("")
print("实验2 轮动策略 (70/30, 当前池)")
rows = []
idx, data, f1 = load_pool(BASE_CUR + CYC_CUR, WS, WE)
for label, kw in [
    ("不轮动", dict(rot_th=None)),
    ("分差≥3, 30/15", dict(rot_th=3)),
    ("分差≥5, 30/15 (现状)", dict(rot_th=5)),
    ("分差≥10, 30/15", dict(rot_th=10)),
    ("无条件, 30/15", dict(rot_th=0)),
    ("分差≥5, 基本15%/周期7.5%", dict(rot_th=5, rot_pct_base=0.15, rot_pct_cyc=0.075)),
    ("分差≥5, 基本50%/周期25%", dict(rot_th=5, rot_pct_base=0.50, rot_pct_cyc=0.25)),
]:
    irr, dd = sim_mixed_param(idx, data, f1, BASE_CUR, CYC_CUR, **kw)
    rows.append((label, irr, dd))
show("实验2 轮动策略", rows, WS, WE)

# ============ 实验3: 选标策略 ============
print("")
print("实验3 选标策略 (70/30, 分差5, 30/15)")
rows = []
combos = [
    ("现状: 周期AI+黄金 / 基本纳指+红利低波", ["159819", "518880"], ["513100", "512890"]),
    ("周期换创业板 / 基本不变", ["159915", "518880"], ["513100", "512890"]),
    ("周期换有色 / 基本不变", ["512400", "518880"], ["513100", "512890"]),
    ("周期AI+黄金 / 基本换标普500+红利低波", ["159819", "518880"], ["513500", "512890"]),
    ("周期AI+黄金 / 基本换纳指+日经", ["159819", "518880"], ["513100", "513520"]),
    ("周期AI+创业板 / 基本纳指+红利低波", ["159819", "159915"], ["513100", "512890"]),
    ("周期黄金+有色 / 基本纳指+红利低波", ["518880", "512400"], ["513100", "512890"]),
]
for label, cyc, base in combos:
    try:
        idx, data, f1 = load_pool(base + cyc, WS, WE)
        irr, dd = sim_mixed_param(idx, data, f1, base, cyc)
        rows.append((label, irr, dd))
    except Exception as e:
        rows.append((label + " [数据不足]", 0, -1))
show("实验3 选标策略", rows, WS, WE)
