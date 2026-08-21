# -*- coding: utf-8 -*-
"""
纪律型策略探索 v2 (预加载优化):
  A 跨区块轮动(全局10%) | B 目标权重再平衡70/30 | C1 双周 | C2 季度 | D 动态权重
  基准: 区块内轮动30%/15%月度
"""
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from basic_rotate import combo_metrics, load_ohlcv, calc_scores

BASE = ["513100", "512890"]
CYC = ["159819", "518880"]
ALL = BASE + CYC
COST, CASH_RATE = 0.0015, 0.02


def prep(window_start, window_end):
    """预加载: close/scores 按统一索引"""
    idx = None
    for c in ALL:
        cc = load_ohlcv(c).loc[window_start:window_end].dropna()
        idx = cc.index if idx is None else idx.union(cc.index)
    idx = sorted(idx)
    data = {}
    for c in ALL:
        cc = load_ohlcv(c).loc[window_start:window_end].dropna()
        data[c] = {
            "p": cc.reindex(idx).ffill(),
            "s": calc_scores(cc).shift(1).reindex(idx).ffill(),
        }
    months = pd.Series(idx).dt.to_period("M")
    is_first = months.ne(months.shift()).values
    is_quarter = pd.Series(idx).dt.to_period("Q").ne(pd.Series(idx).dt.to_period("Q").shift()).values
    is_biweek = np.zeros(len(idx), dtype=bool)
    for m, grp in pd.Series(idx).groupby(months):
        days = list(grp.index)
        if len(days) >= 2:
            is_biweek[days[0]] = True
            is_biweek[days[len(days) // 2]] = True
    return idx, data, is_first, is_biweek, is_quarter


def simulate(idx, data, is_first, is_biweek, is_quarter, freq="month", cross=False,
             target_w=False, dyn_w=False):
    cash = 0.0
    shares = {c: 0.0 for c in ALL}
    nav_list = []
    m_base, m_cyc = 3500.0, 1500.0
    for i, dt in enumerate(idx):
        act = False
        if freq == "month" and is_first[i]:
            act = True
        elif freq == "biweek" and is_biweek[i]:
            act = True
        elif freq == "quarter" and is_quarter[i]:
            act = True
        if act:
            cash += 10000
            for c in BASE:
                p = data[c]["p"].loc[dt]
                w = 0.5
                if dyn_w and freq == "month":
                    s1, s2 = data[BASE[0]]["s"].loc[dt], data[BASE[1]]["s"].loc[dt]
                    if not np.isnan(s1) and not np.isnan(s2) and s1 + s2 > 0:
                        w = min(0.6, max(0.4, s1 / (s1 + s2)))
                amt = min(m_base * (w / 0.5 if dyn_w else 1.0), max(cash, 0.0))
                if amt > 0 and p > 0:
                    shares[c] += amt * (1 - COST) / p
                    cash -= amt
            for c in CYC:
                p = data[c]["p"].loc[dt]
                sc = data[c]["s"].loc[dt]
                mult = 1.0 if np.isnan(sc) else (3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25)
                amt = min(m_cyc * mult, max(cash, 0.0))
                if amt > 0 and p > 0:
                    shares[c] += amt * (1 - COST) / p
                    cash -= amt
        if act:
            if target_w:
                nav = cash + sum(shares[c] * data[c]["p"].loc[dt] for c in ALL)
                base_now = sum(shares[c] * data[c]["p"].loc[dt] for c in BASE)
                diff_b = nav * 0.70 - base_now
                if abs(diff_b) > nav * 0.01:
                    src = CYC[0] if diff_b > 0 else BASE[0]
                    dst = BASE[0] if diff_b > 0 else CYC[0]
                    ps = data[src]["p"].loc[dt]
                    pd_ = data[dst]["p"].loc[dt]
                    sell = min(abs(diff_b), shares[src] * ps)
                    if sell > 100:
                        shares[src] -= sell * (1 - COST) / ps
                        cash += sell
                        buy = min(sell * (1 - COST), max(cash, 0.0))
                        if buy > 0 and pd_ > 0:
                            shares[dst] += buy * (1 - COST) / pd_
                            cash -= buy
            else:
                groups = [BASE, CYC] if not cross else [ALL]
                for grp in groups:
                    s = {}
                    for c in grp:
                        v = data[c]["s"].loc[dt]
                        if not np.isnan(v):
                            s[c] = v
                    if len(s) < 2:
                        continue
                    c1, c2 = list(s.keys())[:2]
                    diff = abs(s[c1] - s[c2])
                    if diff >= 5:
                        loser = c1 if s[c1] < s[c2] else c2
                        winner = c2 if loser == c1 else c1
                        pl = data[loser]["p"].loc[dt]
                        pw = data[winner]["p"].loc[dt]
                        mv = shares[loser] * pl
                        pct = 0.30 if grp is BASE else (0.15 if grp is CYC else 0.10)
                        if freq == "biweek":
                            pct /= 2
                        elif freq == "quarter":
                            pct *= 2
                        if mv > 100:
                            sell = mv * pct
                            shares[loser] -= sell * (1 - COST) / pl
                            cash += sell
                            buy = min(sell * (1 - COST), max(cash, 0.0))
                            if buy > 0 and pw > 0:
                                shares[winner] += buy * (1 - COST) / pw
                                cash -= buy
        cash *= (1 + CASH_RATE / 252)
        nav = cash + sum(shares[c] * data[c]["p"].loc[dt] for c in ALL)
        nav_list.append(nav)
    return pd.Series(nav_list, index=pd.DatetimeIndex(idx))


def run(ws, we, label):
    print(f"\n{label} ({ws}~{we}):")
    idx, data, f1, f2, f3 = prep(ws, we)
    cases = [
        ("基准: 区块内轮动30/15%月度", dict(freq="month")),
        ("A 跨区块轮动(全局10%)月度", dict(freq="month", cross=True)),
        ("B 目标权重再平衡70/30", dict(freq="month", target_w=True)),
        ("C1 双周轮动(幅度减半)", dict(freq="biweek")),
        ("C2 季度轮动(幅度加倍)", dict(freq="quarter")),
        ("D 动态权重40/60~60/40", dict(freq="month", dyn_w=True)),
    ]
    for label_c, kw in cases:
        nav = simulate(idx, data, f1, f2, f3, **kw)
        irr, dd = combo_metrics(nav, 10000)
        print("  %-30s IRR %+7.2f%%  回撤 %6.1f%%  Calmar %.2f" % (label_c, irr * 100, dd * 100, irr / abs(dd)))


run("2020-09-23", "2026-08-11", "完整窗口")
run("2019-06-25", "2026-08-11", "子窗口")
