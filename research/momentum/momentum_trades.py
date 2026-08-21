# -*- coding: utf-8 -*-
"""拆解3%止盈的每笔交易: 买入/卖出价、持有时长、段收益、贡献分解"""
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

def simulate_trades(px, win=25, tp=0.03, cool_days=5):
    """返回净值 + 完整交易记录"""
    nav, hold, peak, entry = 1.0, None, 0.0, 0.0
    cool = {}; nav_list, trades, cur_trade = [], [], None
    for dt in px.index:
        for c in list(cool):
            cool[c] -= 1
            if cool[c] <= 0: del cool[c]
        p = {c: px.loc[dt, c] for c in px.columns}
        if hold is not None and tp > 0:
            cur = p[hold]
            if cur > peak: peak = cur
            if cur / peak - 1 <= -tp:
                nav *= (1 - COST)
                cur_trade["sell_dt"] = dt; cur_trade["sell_px"] = cur
                cur_trade["peak_px"] = peak; cur_trade["dd_from_peak"] = cur/peak - 1
                cur_trade["seg_ret"] = nav / cur_trade["nav_at_entry"] - 1
                trades.append(cur_trade); cur_trade = None
                cool[hold] = cool_days; hold, peak, entry = None, 0.0, 0.0
        slopes = {}
        for c in px.columns:
            if c in cool: continue
            hist = px[c].loc[:dt].dropna()
            if len(hist) < win: continue
            slopes[c] = wslope(hist.tail(win))
        if slopes:
            best = max(slopes, key=slopes.get)
            if hold is not None and hold != best:
                nav *= (1 - COST)
                cur_trade["sell_dt"] = dt; cur_trade["sell_px"] = p[hold]
                cur_trade["peak_px"] = peak; cur_trade["dd_from_peak"] = p[hold]/peak - 1
                cur_trade["seg_ret"] = nav / cur_trade["nav_at_entry"] - 1
                trades.append(cur_trade); cur_trade = None
                cool[hold] = cool_days; hold, peak, entry = None, 0.0, 0.0
            if hold is None and slopes[best] > 0:
                hold = best; peak = entry = p[best]
                nav *= (1 - COST)
                cur_trade = {"code": best, "buy_dt": dt, "buy_px": p[best],
                             "nav_at_entry": nav}
        if hold is not None:
            nav = nav * p[hold] / entry; entry = p[hold]
        nav_list.append(nav)
    if cur_trade is not None:
        cur_trade["sell_dt"] = px.index[-1]; cur_trade["sell_px"] = p[hold]
        cur_trade["peak_px"] = peak; cur_trade["dd_from_peak"] = p[hold]/peak - 1
        cur_trade["seg_ret"] = nav / cur_trade["nav_at_entry"] - 1
        cur_trade["open"] = True
        trades.append(cur_trade)
    return pd.Series(nav_list, index=px.index), trades

CUR4 = ["159915", "513100", "513520", "518880"]
NAMES = {"159915": "创业板", "513100": "纳指", "513520": "日经", "518880": "黄金"}
prices = None
for c in CUR4:
    df = load(c)
    s = df["close"]
    prices = s.to_frame(c) if prices is None else prices.join(s.rename(c), how="outer")
prices = prices.ffill().dropna().loc["2020-01-01":"2026-08-15"]

nav, trades = simulate_trades(prices, tp=0.03)
print(f"总交易 {len(trades)} 笔 (含未平仓), 净值终值 {nav.iloc[-1]:.1f}")

# 段收益统计
rets = [t["seg_ret"] for t in trades if not t.get("open")]
hold_days = [(t["sell_dt"] - t["buy_dt"]).days for t in trades if not t.get("open")]
print(f"\n已平仓 {len(rets)} 笔: 段收益 均值{np.mean(rets)*100:+.1f}% 中位{np.median(rets)*100:+.1f}% "
      f"范围[{np.min(rets)*100:+.0f}%~{np.max(rets)*100:+.0f}%]")
print(f"持有天数: 均值{np.mean(hold_days):.0f}天 中位{np.median(hold_days):.0f}天")
print(f"盈利笔数: {sum(1 for r in rets if r>0)}/{len(rets)} ({sum(1 for r in rets if r>0)/len(rets)*100:.0f}%)")
print(f"平均每笔贡献(复利): {(np.prod([1+r for r in rets])**(1/len(rets))-1)*100:+.1f}%")

print("\n--- 典型交易样本 (每6笔取1笔) ---")
print("%-4s %-10s %-10s %-10s %-10s %-9s %-9s %-8s" % ("标的", "买入日", "卖出日", "买入价", "卖出价", "峰值", "回撤", "段收益"))
for i, t in enumerate(trades):
    if i % 6 != 0: continue
    if t.get("open"): continue
    print("%-4s %-10s %-10s %-10.3f %-10.3f %-9.3f %-8.1f%% %+7.1f%%" % (
        NAMES[t["code"]], str(t["buy_dt"].date()), str(t["sell_dt"].date()),
        t["buy_px"], t["sell_px"], t["peak_px"], t["dd_from_peak"]*100, t["seg_ret"]*100))
