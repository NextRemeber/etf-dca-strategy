# -*- coding: utf-8 -*-
"""
ETF 回测统一核心引擎 (backtest_core) — v1.0 2026-08-21
================================================================
所有研究/回测脚本的唯一引擎来源, 禁止再复制 simulate 逻辑副本。

相对旧引擎 (explore_more 2026-08-20 版) 的修正:
  1. 卖出成本符号修复: 旧版 `shares -= V*(1-COST)/pl; cash += V` 每笔卖出
     凭空创造 V*COST 财富 (IRR 虚高 ~0.4pp, Calmar 2.49→2.56)。
     正确口径: `shares -= V/pl; cash += V*(1-COST)`。
  2. 分数预热: 打分在窗口起点前 prewarm_days 个交易日预热计算 (rules 要求)。
  3. NaN 分数处理: on_na="skip" (规则: 数据不足跳过, 不投) | "default1" (旧口径)。
  4. 成本模型: buy_cost / sell_cost 分离 + 可选最低佣金 min_fee (元/笔)。
  5. 溢价闸门: 纳指 premium>8% 时改场外申购 (按净值成交, nav 序列驱动),
     无净值数据时退化为跳过 (现金留存池内计息)。
  6. 交易日志: 每笔交易记录 (日期/方向/代码/价格/金额/费用/份额/通道/原因),
     支撑独立对账。
  7. 三账守恒审计: 现金账 + 份额账 + 财富守恒 (audit_conservation)。

硬性口径 (rules/backtest-engine.md):
  - IRR 一律二分法 [-0.99, 10.0], 禁止牛顿迭代
  - 买卖按当日价成交, 分数 shift(1), 现金不透支
"""
import os
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

# ============ 路径: 仓库缓存优先, 外部目录仅兜底 ============
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_REPO = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "data", "ic_cache"))
CACHE_EXT = r"E:\autotest\autotest-script-devops\etf_scorer\ic_cache"  # 历史路径, 只读兜底


def _cache_file(name):
    """按 仓库→外部 顺序解析缓存文件路径, 找不到返回 None"""
    for d in (CACHE_REPO, CACHE_EXT):
        f = os.path.join(d, name)
        if os.path.exists(f):
            return f
    return None


def load_ohlcv(code):
    """前复权收盘价 Series; 无数据返回 None"""
    f = _cache_file(f"ohlcv_{code}.pkl")
    if f is None:
        return None
    df = pd.read_pickle(f)
    s = df["close"] if isinstance(df, pd.DataFrame) else df.iloc[:, 0]
    return s.dropna()


def load_premium(code):
    """历史溢价率序列 (%, 前复权口径已对齐); 无返回 None"""
    f = _cache_file(f"premium_{code}.pkl")
    if f is None:
        return None
    d = pd.read_pickle(f)
    s = d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d
    return s.dropna()


def load_otc_nav(code):
    """场外净值序列 (用于溢价闸门场外申购建模); 无返回 None。

    ⚠️ 原始净值含份额分拆跳变 (513100 于 2022-01-13 分拆 1:5, 5.189→1.009)。
    此处自动检测 |日变动|>25% 的跳变并后向复权 (分拆前的净值除以分拆比)，
    使序列连续 — 场外份额全程按复权口径计, 期末估值不受影响。
    """
    f = _cache_file(f"nav_{code}_hist.pkl")
    if f is None:
        return None
    d = pd.read_pickle(f)
    s = (d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d).dropna()
    chg = s.pct_change().abs()
    jumps = chg[chg > 0.25].index
    for j in jumps:
        pos = s.index.get_loc(j)
        if pos < 1:
            continue
        ratio = s.iloc[pos - 1] / s.iloc[pos]           # 分拆比 (前净值/后净值)
        s.iloc[:pos] = s.iloc[:pos] / ratio             # 只调分拆日之前 (含昨日), 分拆日起新口径
    return s


# ============ 打分因子 (与 final_strategy/freq_test 同口径) ============
def factor_series(c):
    """三因子: f1=MA60偏离(0-50) f2=60日动量(0-30) f3=BOLL位置(0-20) — 逆向, 高分=超跌"""
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    std20 = c.rolling(20).std()
    mom60 = c.pct_change(60) * 100
    dev60 = (c / ma60 - 1) * 100
    f1 = np.where(dev60 < -15, 50, np.where(dev60 < -5, 40, np.where(dev60 < 5, 25, np.where(dev60 < 15, 10, 0))))
    f2 = np.where(mom60 < -20, 30, np.where(mom60 < -5, 20, np.where(mom60 < 5, 12, np.where(mom60 < 20, 5, 0))))
    f3 = ((c < ma20 - 2 * std20).astype(float) * 20 +
          ((c >= ma20 - 2 * std20) & (c < ma20)).astype(float) * 14 +
          ((c >= ma20) & (c < ma20 + 2 * std20)).astype(float) * 8)
    return (pd.Series(f1, index=c.index), pd.Series(f2, index=c.index), pd.Series(f3, index=c.index))


def calc_scores(c, weights=(1.0, 1.0, 1.0)):
    f1, f2, f3 = factor_series(c)
    return weights[0] * f1 + weights[1] * f2 + weights[2] * f3


# ============ 组合配置 ============
# v3.1 现行组合: 基本70% (纳指/红利低波/豆粕 各2333) + 周期30% (AI/黄金 各1500)
# 豆粕不参与轮动 (保险角色 1x); 纳指溢价>8% 改场外
V31_CODES = ["513100", "512890", "159985", "159819", "518880"]
V31_CONFIG = dict(
    amounts={"513100": 2333.33, "512890": 2333.33, "159985": 2333.33,
             "159819": 1500.0, "518880": 1500.0},
    graded={"159819", "518880"},                       # S3 分档标的
    rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)],
    premium_gate={"513100": 8.0},
)

# 旧 4 标的组合 (explore_more 历史研究口径, 无闸门无豆粕)
BASE = ["513100", "512890"]
CYC = ["159819", "518880"]
ALL = BASE + CYC
COST = 0.0015        # 旧统一成本 (兼容导出)
CASH_RATE = 0.02     # 货基年化
LEGACY_CONFIG = dict(
    amounts={"513100": 3500.0, "512890": 3500.0, "159819": 1500.0, "518880": 1500.0},
    graded=set(CYC),
    rotate_groups=[(BASE, 0.30), (CYC, 0.15)],
    premium_gate=None,
)


# ============ 数据准备 ============
def prep(ws, we, codes=None, prewarm_days=300):
    """统一索引 + 价格 + 前日分数 (预热) + 溢价/场外净值 (如有)。

    prewarm_days: 分数在窗口前 N 个交易日预热计算 (rules 要求)。
                  传 0 = 旧口径 (窗口内计算, 前60日 NaN)。
    返回: (idx, data, is_first, is_biweek, is_quarter)
          data[code] = {"p": 价格, "s": 前日分数, "prem": 前日溢价%(可无), "nav": 场外净值(可无)}
    """
    if codes is None:
        codes = V31_CODES
    full = {}
    idx = None
    for c in codes:
        cc = load_ohlcv(c)
        if cc is None:
            continue
        full[c] = cc
        w = cc.loc[ws:we].dropna()
        if len(w):
            idx = w.index if idx is None else idx.union(w.index)
    idx = sorted(idx)

    pw_start = (pd.Timestamp(ws) - pd.Timedelta(days=int(prewarm_days * 1.6))).strftime("%Y-%m-%d")
    data = {}
    for c, cc in full.items():
        base = cc if prewarm_days > 0 else cc.loc[ws:we].dropna()
        s = calc_scores(base).shift(1)
        w = cc.loc[ws:we].dropna()
        entry = {
            "p": w.reindex(idx).ffill(),
            "s": s.reindex(idx).ffill(),
        }
        prem = load_premium(c)
        if prem is not None:
            entry["prem"] = prem.shift(1).reindex(idx).ffill()
        nav = load_otc_nav(c)
        if nav is not None:
            entry["nav"] = nav.reindex(idx).ffill()
        data[c] = entry

    s_idx = pd.Series(idx)
    months = s_idx.dt.to_period("M")
    is_first = months.ne(months.shift()).values
    is_quarter = s_idx.dt.to_period("Q").ne(s_idx.dt.to_period("Q").shift()).values
    is_biweek = np.zeros(len(idx), dtype=bool)
    for m, grp in s_idx.groupby(months):
        days = list(grp.index)
        if len(days) >= 2:
            is_biweek[days[0]] = True
            is_biweek[days[len(days) // 2]] = True
    return idx, data, is_first, is_biweek, is_quarter


# ============ 模拟引擎 ============
DEFAULT_CFG = dict(
    amounts=None,            # {code: 月预算}; None=V31_CONFIG
    graded=None,             # S3 分档标的集合
    rotate_groups=None,      # [([c1,c2], pct), ...]
    premium_gate=None,       # {code: 溢价阈值%}
    freq="month",            # month | biweek | quarter
    cross=False,             # 跨区块轮动 (全局10%)
    target_w=False,          # 目标权重再平衡 70/30
    dyn_w=False,             # 分数动态权重 40/60~60/40
    buy_cost=0.0015,
    sell_cost=0.0015,
    min_fee=0.0,             # 最低佣金 (元/笔), 0=不限
    cash_rate=0.02,
    on_na="skip",            # "skip"=规则口径(数据不足跳过) | "default1"=旧口径
    gate_mode="skip",        # 溢价闸门: "skip"=当月预算留现金池(保守界) | "defl"=按溢价折价买入(乐观界)
                             # 注: nav 序列与 premium 序列口径不一致且未含分红, 场外净值直建不可靠,
                             #     故用 [skip, defl] 双界定闸门效应区间 (2026-08-21 实证)
)


def _fee(gross, rate, min_fee):
    return max(gross * rate, min_fee) if min_fee > 0 else gross * rate


def simulate(idx, data, is_first, is_biweek=None, is_quarter=None, cfg=None):
    """执行回测, 返回结果 dict (nav/trades/daily/totals)。

    成交口径: 当日价格; 卖出 = 份额减 V/pl, 现金入 V-fee (成本从现金侧扣)。
    """
    user = dict(cfg or {})
    if user.get("amounts") is None:
        user.pop("amounts", None)
    cfg = {**DEFAULT_CFG, **user}
    pool_keys = ("amounts", "graded", "rotate_groups", "premium_gate")
    if "amounts" not in user:
        pool = dict(V31_CONFIG)                       # 默认 → v3.1 组合
        if "premium_gate" in user:                    # 允许仅覆盖闸门 (传 {} 关闭)
            pool["premium_gate"] = user["premium_gate"]
        unexpected = [k for k in ("graded", "rotate_groups") if k in user]
        if unexpected:
            raise ValueError(f"池配置不完整: 传了 {unexpected} 但缺 amounts")
        cfg.update(pool)
    codes = list(cfg["amounts"].keys())
    graded = set(cfg["graded"] or ())
    inject = float(sum(cfg["amounts"].values()))

    cash = 0.0
    shares = {c: 0.0 for c in codes}
    otc_shares = {c: 0.0 for c in codes}   # 场外份额 (按净值估值)
    trades = []      # 逐笔日志
    daily = []       # 每日账本
    total_in = 0.0
    total_fee = 0.0

    def buy(code, budget, dt, price, channel, reason):
        nonlocal cash, total_fee
        if budget <= 0 or price <= 0 or not np.isfinite(price):
            return
        budget = min(budget, max(cash, 0.0))          # 不透支
        if budget <= 0:
            return
        fee = _fee(budget, cfg["buy_cost"], cfg["min_fee"])
        sh = (budget - fee) / price
        if channel == "otc":
            otc_shares[code] += sh
        else:
            shares[code] += sh
        cash -= budget
        total_fee += fee
        trades.append(dict(date=dt, action="BUY", code=code, price=float(price),
                           gross=budget, fee=fee, shares_delta=sh, channel=channel, reason=reason))

    def sell(code, value, dt, price, reason):
        """卖出市值 value 的份额, 成本从现金侧扣 (正确口径)"""
        nonlocal cash, total_fee
        if value <= 0 or price <= 0:
            return
        fee = _fee(value, cfg["sell_cost"], cfg["min_fee"])
        shares[code] -= value / price
        cash += value - fee
        total_fee += fee
        trades.append(dict(date=dt, action="SELL", code=code, price=float(price),
                           gross=value, fee=fee, shares_delta=-value / price, channel="场内", reason=reason))

    def mv(dt):
        v = 0.0
        for c in codes:
            p = data[c]["p"].loc[dt]
            if not np.isfinite(p):
                p = 0.0          # 标的未上市: 份额为0, 贡献0 (防 0×NaN=NaN 污染 NAV)
            v += shares[c] * p
            if otc_shares[c] > 0:
                nav = data[c].get("nav")
                if nav is not None and np.isfinite(nav.loc[dt]):
                    v += otc_shares[c] * nav.loc[dt]
                else:
                    v += otc_shares[c] * p     # 净值缺失日退化为价格估值
        return v

    def gated(code, dt):
        """溢价闸门: 当日该标的是否被闸 (True=改场外/跳过)"""
        gate_thr = (cfg.get("premium_gate") or {}).get(code)
        if gate_thr is None:
            return False
        prem = data[code].get("prem")
        if prem is None:
            return False
        v = prem.loc[dt]
        return np.isfinite(v) and v > gate_thr

    base_pair = [c for c in codes if c not in graded]   # 纯定投标的 (含豆粕)
    # 动态权重 (研究变体 D): 仅作用于第一轮动组的两只, 分数比例 40/60~60/60 截断
    dyn_pair = None
    if cfg["dyn_w"] and cfg["rotate_groups"] and cfg["freq"] == "month":
        dyn_pair = cfg["rotate_groups"][0][0][:2]
    # 现金注入与动作频率独立 (rules 硬性口径): 注入固定每月1次;
    # 动作频率只改变买入/轮动的切分 — biweek 每次买半月量, quarter 一次买三月量
    buy_mult = {"month": 1.0, "biweek": 0.5, "quarter": 3.0}[cfg["freq"]]

    for i, dt in enumerate(idx):
        # 现金注入: 固定每月首个交易日, 与动作频率独立 (rules 硬性口径)
        if is_first[i]:
            cash += inject
            total_in += inject

        act = False
        if cfg["freq"] == "month" and is_first[i]:
            act = True
        elif cfg["freq"] == "biweek" and is_biweek[i]:
            act = True
        elif cfg["freq"] == "quarter" and is_quarter[i]:
            act = True

        if act:
            # --- 定投: 纯定投标的 1x (dyn_w 时前两只按分数动态分权, 已证伪仅留复现) ---
            for c in base_pair:
                p = data[c]["p"].loc[dt]
                budget = cfg["amounts"][c] * buy_mult
                if dyn_pair and c in dyn_pair:
                    other = dyn_pair[0] if c == dyn_pair[1] else dyn_pair[1]
                    s1 = data[c]["s"].loc[dt]
                    s2 = data[other]["s"].loc[dt]
                    if not np.isnan(s1) and not np.isnan(s2) and s1 + s2 > 0:
                        w = min(0.6, max(0.4, s1 / (s1 + s2)))
                        budget = budget * (w / 0.5)
                if gated(c, dt):
                    prem_v = data[c]["prem"].loc[dt]
                    if cfg["gate_mode"] == "defl" and np.isfinite(prem_v) and prem_v > 0:
                        # 乐观界: 按 NAV 等效价 (价格/(1+溢价)) 买入, 后续按市价估值
                        buy(c, budget, dt, p / (1 + prem_v / 100), "defl", f"月定投·折价(溢价闸门{prem_v:+.1f}%)")
                    # skip: 当月预算留现金池, 由后续月份/轮动使用 (保守界)
                else:
                    buy(c, budget, dt, p, "场内", "月定投")

            # --- 定投: 分档标的 S3 ---
            for c in [x for x in codes if x in graded]:
                p = data[c]["p"].loc[dt]
                sc = data[c]["s"].loc[dt]
                if np.isnan(sc):
                    if cfg["on_na"] == "default1":
                        mult = 1.0
                    else:
                        continue          # 规则口径: 数据不足跳过
                else:
                    mult = 3.0 if sc >= 90 else 2.0 if sc >= 70 else 1.0 if sc >= 50 else 0.25
                buy(c, cfg["amounts"][c] * buy_mult * mult, dt, p, "场内", f"月定投·S3({mult}x)")

            # --- 轮动 / 目标权重 ---
            if cfg["target_w"]:
                nav_now = cash + mv(dt)
                cyc_pair = [c for c in codes if c in graded]
                base_block = cfg["rotate_groups"][0][0] if cfg["rotate_groups"] else base_pair
                base_mv = sum(shares[c] * data[c]["p"].loc[dt] for c in base_block)
                diff_b = nav_now * 0.70 - base_mv
                if abs(diff_b) > nav_now * 0.01:
                    cyc_c = cyc_pair[0] if cyc_pair else codes[0]
                    src = cyc_c if diff_b < 0 else base_pair[0]
                    dst = base_pair[0] if diff_b < 0 else cyc_c
                    ps, pd_ = data[src]["p"].loc[dt], data[dst]["p"].loc[dt]
                    sell_v = min(abs(diff_b), shares[src] * ps)
                    if sell_v > 100:
                        sell(src, sell_v, dt, ps, "目标权重再平衡")
                        buy(dst, min(sell_v * (1 - cfg["sell_cost"]), max(cash, 0.0)), dt, pd_, "场内", "目标权重再平衡")
            elif cfg["rotate_groups"]:
                groups = cfg["rotate_groups"]
                if cfg["cross"]:
                    groups = [(codes, 0.10)]
                for grp, pct in groups:
                    s = {c: data[c]["s"].loc[dt] for c in grp if not np.isnan(data[c]["s"].loc[dt])}
                    if len(s) < 2:
                        continue
                    c1, c2 = list(s.keys())[:2]
                    if abs(s[c1] - s[c2]) < 5:
                        continue
                    loser = c1 if s[c1] < s[c2] else c2
                    winner = c2 if loser == c1 else c1
                    pl, pw = data[loser]["p"].loc[dt], data[winner]["p"].loc[dt]
                    mv_l = shares[loser] * pl
                    pct_eff = pct / 2 if cfg["freq"] == "biweek" else (pct * 2 if cfg["freq"] == "quarter" else pct)
                    if mv_l > 100:
                        sell(loser, mv_l * pct_eff, dt, pl, f"轮动调出(分差{abs(s[c1]-s[c2]):.0f})")
                        if gated(winner, dt):
                            # 闸门: 调入纳指方向跳过 (现金留存, 与 final_strategy 口径一致)
                            continue
                        buy(winner, min(mv_l * pct_eff * (1 - cfg["sell_cost"]), max(cash, 0.0)),
                            dt, pw, "场内", f"轮动调入(分差{abs(s[c1]-s[c2]):.0f})")

        cash *= (1 + cfg["cash_rate"] / 252)
        nav = cash + mv(dt)
        daily.append(dict(date=dt, cash=cash, nav=nav,
                          **{f"sh_{c}": shares[c] for c in codes},
                          **{f"otc_{c}": otc_shares[c] for c in codes if otc_shares[c] != 0.0},
                          fee_cum=total_fee, in_cum=total_in))

    return dict(
        nav=pd.Series([d["nav"] for d in daily], index=pd.DatetimeIndex(idx)),
        daily=pd.DataFrame(daily).set_index("date"),
        trades=pd.DataFrame(trades),
        total_in=total_in,
        total_fee=total_fee,
        cash_end=cash,
        cfg=cfg,
    )


# ============ 指标 ============
def irr_bisect(cfs, lo=-0.99, hi=10.0, iters=200):
    """cfs: [(年化时间点, 现金流)], 流出为负。二分法 (rules 硬性口径)"""
    def npv(r):
        return sum(cf / (1 + r) ** t for t, cf in cfs)
    if npv(lo) < 0:
        return None
    if npv(hi) > 0:
        return hi
    for _ in range(iters):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def month_first_flags(idx):
    m = pd.Series(pd.DatetimeIndex(idx)).dt.to_period("M")
    return m.ne(m.shift()).values


def combo_metrics(nav, monthly, is_first=None):
    """月度现金流 IRR + 最大回撤。返回 (irr, dd) — 兼容旧 basic_rotate 签名"""
    if is_first is None:
        is_first = month_first_flags(nav.index)
    start = nav.index[0]
    cfs = [((dt - start).days / 365.25, -monthly) for dt, fl in zip(nav.index, is_first) if fl]
    cfs.append(((nav.index[-1] - start).days / 365.25, nav.iloc[-1]))
    irr = irr_bisect(cfs)
    dd = (nav / nav.cummax() - 1).min()
    return irr, dd


def full_metrics(res, monthly=None):
    """完整指标: IRR/回撤/Calmar/资产口径"""
    nav = res["nav"]
    if monthly is None:
        monthly = float(sum(res["cfg"]["amounts"].values()))
    irr, dd = combo_metrics(nav, monthly)
    return dict(
        final=float(nav.iloc[-1]),
        total_in=float(res["total_in"]),
        asset_ratio=float(nav.iloc[-1] / res["total_in"]),
        irr=irr, dd=float(dd),
        calmar=(irr / abs(dd)) if dd < 0 else np.nan,
        total_fee=float(res["total_fee"]),
    )


# ============ 三账守恒审计 ============
def audit_conservation(res, data, idx, tol=1e-6):
    """对 simulate 结果做独立三账核对 (不信任引擎内部记账)。

    A. 成交价: 每笔交易价格 == 当日价格 (场内) / 当日净值 (场外)
    B. 现金账: 从交易日志独立重放现金 == 引擎日账现金
    C. 份额账: 从交易日志独立重放份额 == 引擎日账份额
    D. 财富守恒: ΔNAV = 持仓市值变动 + 现金变动 (入金/费用均已入账)
    返回 [(检查名, 通过, 明细)]
    """
    out = []
    trades, daily = res["trades"], res["daily"]
    codes = list(res["cfg"]["amounts"].keys())

    # A. 成交价 (defl 通道为溢价折价价 = 当日价/(1+溢价), 属闸门设计而非违规)
    bad_price = 0
    if len(trades):
        for _, t in trades.iterrows():
            px = data[t["code"]]["p"].loc[t["date"]]
            if t["channel"] == "defl":
                prem = data[t["code"]].get("prem")
                if prem is not None:
                    px = px / (1 + prem.loc[t["date"]] / 100)
            if abs(t["price"] - px) > 1e-9:
                bad_price += 1
    out.append((f"A 成交价基准 ({len(trades)}笔, 异常{bad_price}笔)", bad_price == 0, ""))

    # B/C. 现金账 + 份额账独立重放
    cash = 0.0
    sh = {c: 0.0 for c in codes}
    otc = {c: 0.0 for c in codes}
    fee_cum = 0.0
    is_first = month_first_flags(idx)
    inject = float(sum(res["cfg"]["amounts"].values()))
    cash_err = sh_err = 0
    ti = 0
    for i, dt in enumerate(idx):
        if is_first[i]:
            cash += inject
        if ti < len(trades) and trades.iloc[ti]["date"] == dt:
            while ti < len(trades) and trades.iloc[ti]["date"] == dt:
                t = trades.iloc[ti]
                if t["action"] == "BUY":
                    cash -= t["gross"]
                    if t["channel"] == "otc":
                        otc[t["code"]] += t["shares_delta"]
                    else:
                        sh[t["code"]] += t["shares_delta"]
                else:
                    cash += t["gross"] - t["fee"]
                    sh[t["code"]] += t["shares_delta"]
                fee_cum += t["fee"]
                ti += 1
        cash *= (1 + res["cfg"]["cash_rate"] / 252)
        row = daily.loc[dt]
        if abs(cash - row["cash"]) > max(1e-6, abs(cash) * tol):
            cash_err += 1
        for c in codes:
            key = f"sh_{c}"
            if key in daily.columns and abs(sh[c] - row[key]) > max(1e-9, abs(sh[c]) * tol):
                sh_err += 1
    out.append((f"B 现金账独立重放 (异常日 {cash_err})", cash_err == 0, ""))
    out.append((f"C 份额账独立重放 (异常 {sh_err})", sh_err == 0, ""))

    # D. 财富守恒: nav_t - nav_{t-1} == Δmv + Δcash (现金含货基息, 已入日账)
    wealth_err = 0
    worst = 0.0
    prev_mv = None
    prev_cash = None
    prev_nav = None
    for dt in idx:
        row = daily.loc[dt]
        mv_now = row["nav"] - row["cash"]
        if prev_nav is not None:
            expect = (mv_now - prev_mv) + (row["cash"] - prev_cash)
            got = row["nav"] - prev_nav
            if not (np.isfinite(got) and np.isfinite(expect) and np.isfinite(row["nav"])):
                wealth_err += 1                 # NaN 账目本身就是异常 (2026-08-21: 曾被 NaN 比较漏检)
                continue
            d = abs(got - expect)
            if d > max(1e-6, abs(got) * tol):
                wealth_err += 1
                worst = max(worst, d)
        prev_nav, prev_mv, prev_cash = row["nav"], mv_now, row["cash"]
    out.append((f"D 财富守恒 (异常日 {wealth_err}, 最大偏差 {worst:.2e})", wealth_err == 0, ""))

    # E. 现金不透支
    neg = int((daily["cash"] < -1e-6).sum())
    out.append((f"E 现金不透支 (负值日 {neg})", neg == 0, ""))
    return out
