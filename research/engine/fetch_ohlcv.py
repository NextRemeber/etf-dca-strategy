# -*- coding: utf-8 -*-
"""
OHLCV 数据拉取与增量更新 (腾讯 fqkline, 前复权) — 2026-08-21 重写
================================================================
修复历史 (2026-08-21 复盘):
  1. 旧版 CACHE 硬编码外部路径 (E:\autotest\...\ic_cache), 从不写仓库缓存
     → final_strategy 读的 data/ic_cache 长期陈旧 (日报用旧分数决策)。
  2. 旧版"缓存起始日 ≤2013-06 则整体跳过" → 即使末尾数据几年前的也不刷新。
     现改为: 末条距今 > MAX_AGE_DAYS 天 → 增量补尾部; 重叠段价格若与缓存
     不一致 (前复权因分红整体重算) → 自动全量重拉, 杜绝复权口径断裂。

存储: 仓库 data/ic_cache/ohlcv_{code}.pkl  DataFrame[open,close,high,low,volume]
用法:
  python fetch_ohlcv.py                # 全宇宙增量更新 (陈旧才拉)
  python fetch_ohlcv.py --force        # 全量重拉
  python fetch_ohlcv.py --codes 513100,512890   # 指定标的
  from fetch_ohlcv import update_code  # final_strategy --refresh-data 复用
"""
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import time
import argparse
from datetime import datetime, timedelta

import requests
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

# 仓库缓存 (本脚本唯一写入位置)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "ic_cache")

MAX_AGE_DAYS = 4        # 末条距今 ≤4 天视为新鲜
OVERLAP_TOL = 0.001     # 重叠段单日相对偏差 >0.1% 判定前复权重算 → 全量重拉
FULL_SINCE = 2013       # 全量拉取起始年

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

# 全宇宙 = 周期池19 + final_strategy 扩充候选 + 基本配置5 (2026-08-21 与 ETF_POOL 对齐)
CODES = {
    # 周期池 (19)
    "512480": "sh", "515030": "sh", "159819": "sz", "588000": "sh",
    "512800": "sh", "512980": "sh", "512660": "sh", "512010": "sh",
    "515170": "sh", "159915": "sz", "515790": "sh", "516160": "sh",
    "512200": "sh", "159870": "sz", "512400": "sh", "516110": "sh",
    "510300": "sh", "510500": "sh", "512100": "sh",
    # 基本配置 + 避险 (5)
    "513100": "sh", "512890": "sh", "159985": "sz", "518880": "sh", "515080": "sh",
    # 扩充候选 (final_strategy ETF_POOL, 2026-08-14 起)
    "515880": "sh", "159852": "sz", "159869": "sz", "562500": "sh",
    "515230": "sh", "159781": "sz", "513180": "sh", "513330": "sh",
    "512690": "sh", "159996": "sz", "159825": "sz",
    "515220": "sh", "515210": "sh", "159611": "sz", "159930": "sz",
    "516950": "sh", "512000": "sh", "512070": "sh",
    "512170": "sh", "159992": "sz", "159647": "sz",
    "513500": "sh", "513520": "sh",
}


def fetch_segment(symbol, start, end):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start},{end},640,qfq"
    r = session.get(url, timeout=20)
    block = r.json().get("data", {}).get(symbol, {})
    if not isinstance(block, dict):
        return None
    klines = block.get("qfqday") or block.get("day") or []
    if not klines:
        return None
    rows = [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
             "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])} for k in klines]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def fetch_full(symbol, code):
    """分批拉取 FULL_SINCE 至今 OHLCV"""
    segments = []
    for yr in range(FULL_SINCE, datetime.now().year + 1):
        try:
            seg = fetch_segment(symbol, f"{yr}-01-01", f"{yr}-12-31")
            if seg is not None and len(seg) > 0:
                segments.append(seg)
            time.sleep(0.3)
        except Exception as e:
            print(f"    {code} {yr}: FAIL {type(e).__name__}")
    if not segments:
        return None
    full = pd.concat(segments)
    return full[~full.index.duplicated(keep="last")].sort_index()


def update_code(code, mkt=None, force=False, quiet=True):
    """单标的增量更新。返回状态: fresh/increment/full/no-data/fail"""
    mkt = mkt or ("sh" if code.startswith(("5", "6", "9")) else "sz")
    symbol = f"{mkt}{code}"
    f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
    today = pd.Timestamp.now().normalize()

    if os.path.exists(f) and not force:
        old = pd.read_pickle(f)
        last = old.index[-1]
        if (today - last).days <= MAX_AGE_DAYS:
            return "fresh"
        try:
            seg = fetch_segment(symbol, (last - pd.Timedelta(days=45)).strftime("%Y-%m-%d"),
                                today.strftime("%Y-%m-%d"))
        except Exception:
            return "fail"
        if seg is None or len(seg) == 0:
            return "no-data"
        # 重叠段一致性: 前复权因分红重算 → 全量重拉 (防复权口径断裂)
        overlap_dates = seg.index[seg.index <= last]
        if len(overlap_dates):
            merged = old.join(seg.loc[overlap_dates, ["close"]], rsuffix="_new", how="inner")
            if len(merged):
                bad = (merged["close_new"] / merged["close"] - 1).abs() > OVERLAP_TOL
                if bad.any():
                    if not quiet:
                        print(f"    {code}: 重叠段价格不一致 (前复权重算), 全量重拉")
                    full = fetch_full(symbol, code)
                    if full is not None and len(full) > 300:
                        full.to_pickle(f)
                        return "full"
                    return "fail"
        new_rows = seg.loc[seg.index > last]
        if len(new_rows) == 0:
            return "fresh"
        pd.concat([old, new_rows]).sort_index().to_pickle(f)
        return "increment"

    full = fetch_full(symbol, code)
    if full is not None and len(full) > 300:
        full.to_pickle(f)
        return "full"
    return "no-data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="全量重拉 (忽略增量)")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔指定标的")
    args = parser.parse_args()
    os.makedirs(CACHE, exist_ok=True)

    targets = dict(CODES)
    if args.codes:
        want = [c.strip() for c in args.codes.split(",") if c.strip()]
        targets = {c: CODES.get(c, "sh" if c.startswith(("5", "6", "9")) else "sz") for c in want}

    stats = {}
    for code, mkt in targets.items():
        st = update_code(code, mkt, force=args.force, quiet=False)
        stats[st] = stats.get(st, 0) + 1
        f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
        if st in ("increment", "full"):
            d = pd.read_pickle(f)
            print(f"  ✅ {code}: {st} [{d.index[0].date()} ~ {d.index[-1].date()}] {len(d)}天")
        time.sleep(0.4)
    print(f"\n汇总: {stats}")


if __name__ == "__main__":
    main()
