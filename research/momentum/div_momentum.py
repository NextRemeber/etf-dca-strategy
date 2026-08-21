# -*- coding: utf-8 -*-
"""
分散版动量轮动: 池子分散持有 + 按动量排名调权重 (正确引擎, 当日价成交)
方案:
  K1  动量前1 (单持仓, 对照原版)
  K2  动量前2 等权
  K4  全部4只 等权 (纯分散持有对照)
  W4  全部4只 动量加权 (排名 4:3:2:1 归一化)
  K2T 动量前2 等权 + 3%止盈
频率: 月度调仓 (每月首个交易日按25日斜率排名调权重)
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

CACHE = r"E:\autotest\autotest-script-devops\etf_scorer\ic_cache"
COST = 0.0015
CASH_RATE = 0.02

def load(code):
    f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
    return pd.read_pickle(f) if os.path.exists(f) else None

def wslope(px):
    y = px.values; x = np.arange(len(y))
    w = np.exp(3.0 * x / len(y))
    xw = x - np.average(x, weights=w); yw = y - np.average(y, weights=w)
    return np.sum(w * xw * yw) / np.sum(w * xw * xw)

def sim_div_mom(prices, lump, K=2, weighted=False, tp=0.0, cool_days=5):
    """分散动量: 月度调仓到目标权重 (等权或动量加权), 可选止盈"""
    idx = prices.index
    months = idx.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cash = float(lump)
    shares = {c: 0.0 for c in prices.columns}
    nav_list, trades = [], 0
    hold_peak = {c: 0.0 for c in prices.columns}  # 止盈用
    for i, dt in enumerate(idx):
        p = {c: prices.loc[dt, c] for c in prices.columns}
        # 止盈 (分散版: 每只独立止盈卖出)
        if tp > 0:
            for c in prices.columns:
                if shares[c] > 0 and not np.isnan(p[c]):
                    if p[c] > hold_peak[c]: hold_peak[c] = p[c]
                    if hold_peak[c] > 0 and p[c] / hold_peak[c] - 1 <= -tp:
                        cash += shares[c] * p[c] * (1 - COST)
                        shares[c] = 0.0
                        hold_peak[c] = 0.0
                        trades += 1
        # 月度调仓: 按斜率排名设置目标权重
        if is_first[i]:
            slopes = {}
            for c in prices.columns:
                hist = prices[c].loc[:dt].dropna()
                if len(hist) < 25: continue
                slopes[c] = wslope(hist.tail(25))
            if not slopes:
                nav_list.append(cash + sum(shares[c] * p[c] for c in prices.columns))
                continue
            ranked = sorted(slopes, key=slopes.get, reverse=True)  # 强→弱
            # 目标权重
            if K >= len(ranked):
                keep = ranked
            else:
                keep = ranked[:K]
            if weighted:
                # 动量加权: 排名1→4分, 2→3分, 3→2分, 4→1分 (仅被选中者)
                w = {}
                for rank, c in enumerate(keep):
                    w[c] = len(keep) - rank
                tot_w = sum(w.values())
                target = {c: w[c] / tot_w for c in w}
            else:
                target = {c: 1.0 / len(keep) for c in keep}
            # 调仓: 先卖不持有的/超配的, 再买低配的
            nav_now = cash + sum(shares[c] * p[c] for c in prices.columns)
            # 卖出不在目标中的
            for c in prices.columns:
                if shares[c] > 0 and c not in target:
                    cash += shares[c] * p[c] * (1 - COST)
                    shares[c] = 0.0
                    trades += 1
            # 调整到目标权重
            for c in target:
                target_val = nav_now * target[c]
                cur_val = shares[c] * p[c]
                diff = target_val - cur_val
                if diff > 1e-6 * nav_now and cash > 0:
                    buy = min(diff, cash)
                    shares[c] += buy * (1 - COST) / p[c]
                    cash -= buy
                    trades += 1
                elif diff < -1e-6 * nav_now:
                    sell = min(-diff, cur_val)
                    cash += sell * (1 - COST)
                    shares[c] -= sell * (1 - COST) / p[c]
                    trades += 1
        nav_list.append(cash + sum(shares[c] * p[c] for c in prices.columns))
    return pd.Series(nav_list, index=idx), trades

def run(pool, start, end, **kw):
    prices = None
    for c in pool:
        df = load(c)
        if df is None: return None
        s = df["close"]
        prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
    prices = prices.ffill().dropna().loc[start:end]
    nav, tr = sim_div_mom(prices, 1.0, **kw)  # 净值口径
    total = nav.iloc[-1] / nav.iloc[0] - 1
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    dd = (nav / nav.cummax() - 1).min()
    return nav, total, ann, dd, ann / abs(dd) if dd < 0 else np.nan, tr

CUR4 = ["159915", "513100", "513520", "518880"]  # 创业板/纳指/日经/黄金
PRE4 = ["159915", "513100", "518880", "513030"]  # 前周期: 创业板/纳指/黄金/德国

CASES = [
    ("K1 动量前1(原版)", dict(K=1)),
    ("K2 前2等权", dict(K=2)),
    ("K4 全部等权(纯分散)", dict(K=4)),
    ("W4 动量加权4:3:2:1", dict(K=4, weighted=True)),
    ("K2T 前2等权+3%止盈", dict(K=2, tp=0.03)),
]

for label, pool, ws, we, win in [
    ("当前 2020-01起", CUR4, "2020-01-02", "2026-08-15", "20-26"),
    ("当前 2020-08起(定投同起点)", CUR4, "2020-08-17", "2026-08-15", "20-26"),
    ("前周期 2014-2019", PRE4, "2014-09-05", "2019-12-31", "14-19"),
]:
    print("=" * 78)
    print("%s (%s~%s)" % (label, ws, we))
    print("=" * 78)
    print("%-24s %10s %8s %8s %8s %7s" % ("方案", "总收益", "年化", "回撤", "Calmar", "换手"))
    for cname, kw in CASES:
        r = run(pool, ws, we, **kw)
        nav, total, ann, dd, cal, tr = r
        print("%-24s %+8.1f%% %+6.1f%% %7.1f%% %8.2f %7d" % (
            cname, total * 100, ann * 100, dd * 100, cal, tr))
