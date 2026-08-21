# -*- coding: utf-8 -*-
"""
OHLCV 全量数据拉取 (腾讯 fqkline, 前复权)
覆盖: POOL 19只 + 慢牛3只 (黄金/纳指/红利低波)
存储: ic_cache/ohlcv_{code}.pkl  DataFrame[date, open, close, high, low, volume]
"""
import warnings
warnings.filterwarnings("ignore")
import os
import time
import requests
import pandas as pd

CACHE = r"E:\autotest\autotest-script-devops\etf_scorer\ic_cache"
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

CODES = {
    # 周期池 (POOL 19只)
    "512480": "sh", "515030": "sh", "159819": "sz", "588000": "sh",
    "512800": "sh", "512980": "sh", "512660": "sh", "512010": "sh",
    "515170": "sh", "159915": "sz", "515790": "sh", "516160": "sh",
    "512200": "sh", "159870": "sz", "512400": "sh", "516110": "sh",
    "510300": "sh", "510500": "sh", "512100": "sh",
    # 慢牛3只
    "518880": "sh", "513100": "sh", "512890": "sh",
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
    rows = []
    for k in klines:
        # k = [date, open, close, high, low, volume]
        rows.append({"date": k[0], "open": float(k[1]), "close": float(k[2]),
                     "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def fetch_full(symbol, code):
    """分批拉取2013至今 OHLCV (覆盖两个完整牛熊周期)"""
    segments = []
    for yr in range(2013, 2027):
        sd = f"{yr}-01-01"
        ed = f"{yr}-12-31"
        try:
            seg = fetch_segment(symbol, sd, ed)
            if seg is not None and len(seg) > 0:
                segments.append(seg)
            time.sleep(0.3)
        except Exception as e:
            print(f"    {code} {sd}~{ed}: FAIL {type(e).__name__}")
    if not segments:
        return None
    full = pd.concat(segments)
    full = full[~full.index.duplicated(keep="last")].sort_index()
    return full


def main():
    os.makedirs(CACHE, exist_ok=True)
    for code, mkt in CODES.items():
        f = os.path.join(CACHE, f"ohlcv_{code}.pkl")
        if os.path.exists(f):
            old = pd.read_pickle(f)
            if old.index[0] <= pd.Timestamp("2013-06-01"):
                print(f"{code}: 已有完整缓存 {len(old)}天 [{old.index[0].date()}~{old.index[-1].date()}]")
                continue
            # 缓存不足, 重拉
            print(f"{code}: 缓存仅{old.index[0].date()}起, 重拉...")
        else:
            print(f"拉取 {code}...")
        full = fetch_full(f"{mkt}{code}", code)
        if full is not None and len(full) > 300:
            full.to_pickle(f)
            print(f"  ✅ {code}: {len(full)}天 [{full.index[0].date()}~{full.index[-1].date()}]")
        else:
            print(f"  ❌ {code}: 数据不足")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
