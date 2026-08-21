# -*- coding: utf-8 -*-
"""
动量轮动正确版重跑: 当日价格成交(无未来函数)
对比 v2(昨日净值成交, 有bug) 与 正确实现 的差异
"""
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

def sim_correct(prices, win=25, tp=0.03, cool_days=5):
    """正确实现: 止盈/切换按当日价格成交 (份额×当日价)"""
    cash, hold, hold_sh, peak = 1.0, None, 0.0, 0.0
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
            if hold is None and slopes[best] > 0 and cash > 1e-6:
                hold = best; peak = p[best]
                hold_sh = cash * (1 - COST) / p[best]
                cash = 0.0; trades += 1
        nav_list.append(cash + (hold_sh * p[hold] if hold is not None else 0.0))
    return pd.Series(nav_list, index=prices.index), trades

def run(pool, start, end, win=25, tp=0.03):
    prices = None
    for c in pool:
        df = load(c)
        if df is None: return None
        s = df["close"]
        prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
    prices = prices.ffill().dropna().loc[start:end]
    nav, tr = sim_correct(prices, win=win, tp=tp)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    dd = (nav / nav.cummax() - 1).min()
    return nav, total, ann, dd, ann / abs(dd) if dd < 0 else np.nan, tr

CUR4 = ["159915", "513100", "513520", "518880"]  # 创业板/纳指/日经/黄金
PRE4 = ["159915", "513100", "518880", "513030"]  # 前周期: 创业板/纳指/黄金/德国

print("=" * 76)
print("动量轮动【正确引擎】当日价成交 (旧v2昨日净值成交有未来函数, 数字作废)")
print("=" * 76)

print("\n① 当前窗口 2020-01起 (4标的: 创业板/纳指/日经/黄金):")
for tp, label in [(0.03, "3%止盈(旧v2虚报2688%)"), (0.05, "5%止盈(旧v2虚报1514%)"), (0.0, "无止盈")]:
    r = run(CUR4, "2020-01-02", "2026-08-15", tp=tp)
    nav, total, ann, dd, cal, tr = r
    print("  %-24s 总收益%+8.1f%% 年化%5.1f%% 回撤%5.1f%% Calmar %5.2f 换手%d" % (
        label, total*100, ann*100, dd*100, cal, tr))

print("\n② 同定投起点 2020-08-17 (公平对比口径):")
r = run(CUR4, "2020-08-17", "2026-08-15", tp=0.03)
nav, total, ann, dd, cal, tr = r
print("  3%%止盈: 总收益%+8.1f%% 年化%5.1f%% 回撤%5.1f%% Calmar %5.2f" % (total*100, ann*100, dd*100, cal))
print("  (旧v2同口径虚报: 总收益+1514% 年化52%)")

print("\n③ 前周期 2014-2019 (4标的: 创业板/纳指/黄金/德国):")
for tp, label in [(0.03, "3%止盈(旧v2虚报年化22.5%)"), (0.05, "5%止盈"), (0.0, "无止盈")]:
    r = run(PRE4, "2014-09-05", "2019-12-31", tp=tp)
    nav, total, ann, dd, cal, tr = r
    print("  %-24s 总收益%+8.1f%% 年化%5.1f%% 回撤%5.1f%% Calmar %5.2f 换手%d" % (
        label, total*100, ann*100, dd*100, cal, tr))

print("\n④ 分年度 (3%止盈, 2020-01起):")
nav, *_ = run(CUR4, "2020-01-02", "2026-08-15", tp=0.03)
for yr in ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]:
    seg = nav.loc[yr]
    r_yr = seg.iloc[-1] / seg.iloc[0] - 1
    dd_yr = (seg / seg.cummax() - 1).min()
    print("  %s: %+7.1f%%  (年内回撤 %5.1f%%)" % (yr, r_yr*100, dd_yr*100))
