# -*- coding: utf-8 -*-
"""
现金储备制引擎 - 同预算资产口径最终裁定
修复: 旧引擎 mult>1 时"免费印钱"(不扣现金) + 纯定投 dummy=100 误触 3x

设计: 现金池从0开始, 每月入账1000(总预算恒定), 投资按 mult 从池子扣(可为负=动用未来预算),
      池子计货基息2%, NAV=持仓市值+池子余额, 期末同预算直接可比
"""
import warnings
warnings.filterwarnings("ignore")
import sys
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\autotest\autotest-script-devops\etf_scorer")
from weight_sensitivity import load, compute_factors, POOL

COST = 0.0015
CASH_RATE = 0.02


def simulate_reserve(s, scores=None, pos_fn=None, monthly=1000, thr=(90, 70, 50), force_mult=None):
    """现金储备制回测.
    force_mult: 固定倍率(纯定投=1.0); scores+thr: 分档; pos_fn: 在分档基础上调倍率
    """
    t90, t70, t50 = thr
    start = s.index[0]
    months = s.index.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    invest_dates = set(s.index[is_first])
    cash, shares = 0.0, 0.0
    nav_list, hist = [], []
    total_invest = 0.0
    for i, dt in enumerate(s.index):
        p = s.loc[dt]
        # 月初入账预算
        if is_first[i]:
            cash += monthly
        # 计算倍率
        if force_mult is not None:
            mult = force_mult
        else:
            sc = scores.shift(1).loc[dt] if dt in scores.index else np.nan
            mult = 1.0
            if not np.isnan(sc):
                if sc >= t90:
                    mult = 3.0
                elif sc >= t70:
                    mult = 2.0
                elif sc >= t50:
                    mult = 1.0
                else:
                    mult = 0.0
        # 投资日执行
        if dt in invest_dates and p > 0:
            if pos_fn is not None:
                mult = pos_fn(dt, p, hist, mult)
            amt = monthly * mult
            if amt > 0:
                # 硬约束: 不透支, 投资额不超过当前现金余额 (低位3x需前期积累)
                amt = min(amt, max(cash, 0.0))
                if amt > 0:
                    shares += amt * (1 - COST) / p
                    cash -= amt
                    total_invest += amt
        # 现金池计息 (负数也计, 资金成本=货基利率)
        cash *= (1 + CASH_RATE / 252)
        nav_list.append(shares * p + cash)
        hist.append((dt, p))
    nav = pd.Series(nav_list, index=s.index)
    final_mv = shares * s.iloc[-1] + cash
    return nav, final_mv, total_invest


def calc_metrics(nav, monthly=1000):
    """IRR(每月预算现金流) + 最大回撤 + 年化Sharpe"""
    rets = nav.pct_change().dropna()
    dd = (nav / nav.cummax() - 1).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    start = nav.index[0]
    months = nav.index.to_period("M")
    is_first = pd.Series(months).ne(pd.Series(months).shift()).values
    cashflows = []
    for k, dt in enumerate(list(nav.index[is_first])):
        cashflows.append(((dt - start).days / 365.25, -monthly))
    cashflows.append(((nav.index[-1] - start).days / 365.25, nav.iloc[-1]))

    def npv(r):
        return sum(cf / (1 + r) ** t for t, cf in cashflows)

    rate = 0.05
    for _ in range(100):
        f = npv(rate)
        fp = (npv(rate + 1e-4) - npv(rate)) / 1e-4
        if abs(fp) < 1e-10:
            break
        rn = rate - f / fp
        if abs(rn - rate) < 1e-8:
            break
        rate = rn
    return rate, dd, sharpe


def make_kelly_pos(win_rate, gains, losses, pos_max=0.75):
    loss_rate = 1 - win_rate
    kelly = win_rate - loss_rate / (gains / losses) if losses > 0 else 0
    return min(max(kelly, 0), pos_max)


def kelly_pos_builder():
    def _fn(dt, p, hist, mult):
        if len(hist) < 100:
            return mult
        prices = np.array([x[1] for x in hist[-250:]])
        if len(prices) < 60:
            return mult
        rets = np.diff(prices) / prices[:-1]
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        if len(wins) == 0 or len(losses) == 0:
            return mult
        wr = len(wins) / len(rets)
        k = make_kelly_pos(wr, wins.mean(), abs(losses.mean()))
        return mult * k if k > 0.02 else 0.0
    return _fn


def atr_pos_builder(atr_base=0.10, std_thr=0.5):
    def _fn(dt, p, hist, mult):
        if len(hist) < 30:
            return mult
        prices = np.array([x[1] for x in hist[-21:]])
        if len(prices) < 21:
            return mult
        tr = np.abs(np.diff(prices))
        atr_ratio = tr.mean() / p
        wv = max(atr_ratio / std_thr, 1e-6)
        pos = min(atr_base / wv, 0.75)
        return mult * pos
    return _fn


# ============ 数据 ============
data = {}
for code, name in POOL.items():
    s = load(code)
    if s is None:
        continue
    s = s.loc["2020-06-01":].dropna()
    if len(s) < 400:
        continue
    f1, f2, f3 = compute_factors(s)
    data[code] = (name, s, f1, f2, f3)
print("回测标的: %d只, 区间 2020-06 ~ 2026-08" % len(data))

WEIGHTS = [
    ("旧50/30/20", (1.0, 1.0, 1.0)),
    ("新0/60/40", (0.0, 2.0, 2.0)),
    ("均权33/33/33", (0.667, 1.111, 1.667)),
    ("动量主导25/50/25", (0.5, 1.667, 1.25)),
    ("BOLL主导30/20/50", (0.6, 0.667, 2.5)),
]


def run_combo(w=None, pos_mode=None, thr=(90, 70, 50), seg=None):
    """返回 (最终资产均值, IRR均值, 回撤均值, Sharpe均值, 投入占比)"""
    assets, irrs, dds, shps, ratios = [], [], [], [], []
    for code, (name, s, f1, f2, f3) in data.items():
        if seg is not None:
            sd, ed = seg
            s = s.loc[sd:ed]
            if len(s) < 300:
                continue
            f1, f2, f3 = f1.loc[sd:ed], f2.loc[sd:ed], f3.loc[sd:ed]
        if pos_mode == "kelly":
            pf = kelly_pos_builder()
        elif pos_mode == "atr":
            pf = atr_pos_builder()
        else:
            pf = None
        if w is None:
            scores, force = None, 1.0
        else:
            scores = w[0] * f1 + w[1] * f2 + w[2] * f3
            force = None
        nav, final_mv, total_invest = simulate_reserve(s, scores=scores, pos_fn=pf, thr=thr, force_mult=force)
        irr, dd, shp = calc_metrics(nav)
        n_months = int(pd.Series(nav.index.to_period("M")).nunique())
        assets.append(final_mv)
        irrs.append(irr)
        dds.append(dd)
        shps.append(shp)
        ratios.append(total_invest / (1000 * n_months))
    return (np.mean(assets), np.mean(irrs), np.mean(dds), np.mean(shps), np.mean(ratios))


# ============ 1) 仓位模式对比 (现金储备制) ============
print("\n===== 1) 仓位模式对比 (现金储备制, 同预算1000/月) =====")
print("%-20s %10s %8s %9s %7s %8s" % ("模式", "最终资产", "IRR", "最大回撤", "Sharpe", "投入占比"))
dca = run_combo(w=None)
print("%-20s %10.0f %+7.2f%% %8.2f%% %+7.2f %7.1f%%" % ("纯定投(基准)", dca[0], dca[1]*100, dca[2]*100, dca[3], dca[4]*100))
for wlabel, w in WEIGHTS:
    for pos_label, pos_mode in [("分档", None), ("+Kelly", "kelly"), ("+ATR", "atr")]:
        r = run_combo(w=w, pos_mode=pos_mode)
        print("%-20s %10.0f %+7.2f%% %8.2f%% %+7.2f %7.1f%%" % (wlabel + pos_label, r[0], r[1]*100, r[2]*100, r[3], r[4]*100))

# ============ 2) 权重敏感性 (资产口径) ============
print("\n===== 2) 权重敏感性 (资产口径, 纯分档) =====")
print("%-20s %10s %8s %9s %8s" % ("权重", "最终资产", "IRR", "最大回撤", "投入占比"))
base_asset = None
for wlabel, w in WEIGHTS:
    r = run_combo(w=w, pos_mode=None)
    if base_asset is None:
        base_asset = r[0]
    diff = (r[0] / base_asset - 1) * 100 if base_asset else 0
    print("%-20s %10.0f %+7.2f%% %8.2f%% %7.1f%%   vs旧%+.1f%%" % (wlabel, r[0], r[1]*100, r[2]*100, r[4]*100, diff))

# ============ 3) 分段稳健性 ============
print("\n===== 3) 分段稳健性 (资产口径) =====")
SEGS = [("前段 2020-06~2023-01", "2020-06-01", "2023-01-31"),
        ("后段 2023-02~2026-08", "2023-02-01", "2026-08-31")]
for seg_label, sd, ed in SEGS:
    print("\n--- %s ---" % seg_label)
    dca_r = run_combo(w=None, seg=(sd, ed))
    print("  纯定投: 资产%.0f IRR%+.2f%%" % (dca_r[0], dca_r[1]*100))
    for wlabel, w in WEIGHTS[:3]:
        r = run_combo(w=w, pos_mode=None, seg=(sd, ed))
        print("  %-14s 资产%8.0f IRR%+6.2f%% 回撤%6.2f%% 投入%4.1f%%"
              % (wlabel, r[0], r[1]*100, r[2]*100, r[4]*100))

# ============ 4) GridSearch 阈值 (资产口径复核) ============
print("\n===== 4) 阈值扫描 (资产口径, 新权重) =====")
for thr in [(90, 70, 50), (85, 65, 50), (95, 75, 55), (90, 75, 60), (80, 65, 50)]:
    r = run_combo(w=WEIGHTS[1][1], pos_mode=None, thr=thr)
    print("  阈值%s -> 资产%8.0f IRR%+6.2f%% 回撤%6.2f%% 投入%4.1f%%"
          % (str(thr), r[0], r[1]*100, r[2]*100, r[4]*100))
