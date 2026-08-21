# -*- coding: utf-8 -*-
"""动量轮动: 随机对照(选标的随机化) + 成本敏感性"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

# 缓存解析: 仓库 data/ic_cache 优先, 旧外部目录兜底 (2026-08-21 去外部硬依赖)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "ic_cache")
_EXT_CACHE = r"E:\autotest\autotest-script-devops\etf_scorer\ic_cache"
COST = 0.0015

def load(code):
    f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
    if not os.path.exists(f):
        f = os.path.join(_EXT_CACHE, f"ohlcv_{code}.pkl")
    return pd.read_pickle(f) if os.path.exists(f) else None

def wslope(px):
    y = px.values; x = np.arange(len(y))
    w = np.exp(3.0 * x / len(y))
    xw = x - np.average(x, weights=w); yw = y - np.average(y, weights=w)
    return np.sum(w * xw * yw) / np.sum(w * xw * xw)

def simulate(px, win=25, tp=0.03, cool_days=5, random_pick=False, seed=0):
    """random_pick=True 时随机选标的(保留止盈+冷却), 对照用"""
    rng = np.random.default_rng(seed)
    nav, hold, peak, entry = 1.0, None, 0.0, 0.0
    cool = {}; nav_list, trades = [], 0
    for dt in px.index:
        for c in list(cool):
            cool[c] -= 1
            if cool[c] <= 0: del cool[c]
        p = {c: px.loc[dt, c] for c in px.columns}
        if hold is not None and tp > 0:
            cur = p[hold]
            if cur > peak: peak = cur
            if cur / peak - 1 <= -tp:
                nav *= (1 - COST); cool[hold] = cool_days
                hold, peak, entry = None, 0.0, 0.0
        avail = [c for c in px.columns if c not in cool and not np.isnan(p[c])]
        if random_pick:
            if hold is not None and hold not in avail:
                nav *= (1 - COST); hold, peak, entry = None, 0.0, 0.0
            if hold is None and avail:
                hold = avail[int(rng.integers(len(avail)))]
                peak = entry = p[hold]
                nav *= (1 - COST); trades += 1
        else:
            slopes = {}
            for c in px.columns:
                if c in cool: continue
                hist = px[c].loc[:dt].dropna()
                if len(hist) < win: continue
                slopes[c] = wslope(hist.tail(win))
            if slopes:
                best = max(slopes, key=slopes.get)
                if hold is not None and hold != best:
                    nav *= (1 - COST); cool[hold] = cool_days
                    hold, peak, entry = None, 0.0, 0.0
                if hold is None and slopes[best] > 0:
                    hold = best; peak = entry = p[best]
                    nav *= (1 - COST); trades += 1
        if hold is not None:
            nav = nav * p[hold] / entry; entry = p[hold]
        nav_list.append(nav)
    return pd.Series(nav_list, index=px.index), trades

def run(pool, start, end, win=25, tp=0.03, cost=COST, random_pick=False, seed=0):
    global COST
    prices = None
    for c in pool:
        df = load(c)
        if df is None: return None
        s = df["close"]
        prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
    prices = prices.ffill().dropna().loc[start:end]
    old_cost = COST; COST = cost
    nav, tr = simulate(prices, win=win, tp=tp, random_pick=random_pick, seed=seed)
    COST = old_cost
    total = nav.iloc[-1] / nav.iloc[0] - 1
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    dd = (nav / nav.cummax() - 1).min()
    return nav, total, ann, dd, ann / abs(dd) if dd < 0 else np.nan, tr

CUR4 = ["159915", "513100", "513520", "518880"]

print("=" * 70)
print("③ 随机对照: 动量选标的 vs 随机选标的 (同为3%止盈+冷却, 2020-2026)")
print("=" * 70)
r = run(CUR4, "2020-01-01", "2026-08-15", tp=0.03)
print(f"动量选标的:  总收益{r[1]*100:+8.1f}% 年化{r[2]*100:5.1f}% Calmar {r[4]:5.2f} 换手{r[5]}次")
rand_results = []
for seed in range(30):
    rr = run(CUR4, "2020-01-01", "2026-08-15", tp=0.03, random_pick=True, seed=seed)
    rand_results.append((rr[1], rr[4], rr[5]))
rand_ann = np.array([x[1] for x in rand_results])
print(f"随机选标的×30:  年化 均值{rand_ann.mean()*100:.1f}% 中位{np.median(rand_ann)*100:.1f}% 最好{rand_ann.max()*100:.1f}% 最差{rand_ann.min()*100:.1f}%")
print(f"  动量年化 {r[2]*100:.1f}% vs 随机最好 {rand_ann.max()*100:.1f}% → {'动量胜出' if r[2] > rand_ann.max() else '动量未超随机最好'}")

print("\n" + "=" * 70)
print("④ 成本敏感性 (3%止盈, 2020-2026):")
print("=" * 70)
for cost, label in [(0.0005, "0.05% (低佣)"), (0.0015, "0.15% (默认)"), (0.003, "0.30% (高)")]:
    rr = run(CUR4, "2020-01-01", "2026-08-15", tp=0.03, cost=cost)
    print(f"  {label}: 总收益{rr[1]*100:+8.1f}% 年化{rr[2]*100:5.1f}% 回撤{rr[3]*100:5.1f}% Calmar {rr[4]:5.2f} 换手{rr[5]}次")
