# -*- coding: utf-8 -*-
"""513100 溢价序列重建/增量刷新 (2026-08-21 B7 bug 修复产物, rules/backtest-engine.md §七.5)

口径: premium = raw(不复权)收盘价 / 东财官方单位净值 - 1  (同日期对齐)
禁止用 qfq 价格: 513100 于 2025-05-12 份额折算 (qfq=raw×0.5614), 混用会失真。

用法:
  python build_premium.py            # 全量重建 (2018-01 起)
  python build_premium.py --incremental   # 增量补尾部 (仅拉净值缺失段)

数据源:
  净值: https://api.fund.eastmoney.com/f10/lsjz (分页, 每页30)
  价格: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get (fq 参数留空=不复权, 每段640条)
输出:
  data/ic_cache/premium_513100.pkl (DataFrame, 列名0) + 同步生产缓存
"""
import argparse
import os
import pickle
import shutil
import time

import pandas as pd
import requests

CODE = "513100"
SYM = "sh513100"
NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
PX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "ic_cache")
PROD_CACHE = r"E:/autotest/autotest-script-devops/etf_scorer/ic_cache"


def fetch_nav(start="2013-07-01", end="2026-12-31"):
    """东财官方单位净值 (分页拉全)"""
    nav = {}
    for page in range(1, 200):
        params = {"fundCode": CODE, "pageIndex": page, "pageSize": 30,
                  "startDate": start, "endDate": end}
        rows = (requests.get(NAV_URL, params=params, headers=HEADERS, timeout=15)
                .json().get("Data") or {}).get("LSJZList") or []
        if not rows:
            break
        nav.update({pd.Timestamp(r["FSRQ"]): float(r["DWJZ"]) for r in rows})
        time.sleep(0.15)
    return pd.Series(nav).sort_index()


def fetch_raw(start, end):
    """腾讯不复权日线 (接口每段返回<=640条, 分段需保证覆盖且重叠去重)"""
    raw = {}
    segs = [(start, "2020-06-30"), ("2020-07-01", "2022-06-30"),
            ("2022-07-01", "2024-06-30"), ("2024-07-01", end)]
    for s, e in segs:
        r = requests.get(f"{PX_URL}?param={SYM},day,{s},{e},640,",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        k = (r.json()["data"][SYM].get("day") or
             r.json()["data"][SYM].get("qfqday"))
        raw.update({pd.Timestamp(row[0]): float(row[2]) for row in k})
        time.sleep(0.3)
    return pd.Series(raw).sort_index()


def build(start="2018-01-01", incremental=False):
    nav = fetch_nav()
    raw = fetch_raw(start, "2026-12-31")
    aligned = pd.concat([raw.rename("raw"), nav.rename("nav")], axis=1).dropna()
    prem = (aligned["raw"] / aligned["nav"] - 1) * 100
    prem.name = None
    out = os.path.join(CACHE_DIR, f"premium_{CODE}.pkl")
    prem.to_frame().to_pickle(out)
    shutil.copy(out, os.path.join(PROD_CACHE, f"premium_{CODE}.pkl"))
    print(f"重建完成: {len(prem)} 条 | {prem.index.min().date()} ~ {prem.index.max().date()} "
          f"| 均值 {prem.mean():.2f}% | >8% {(prem > 8).sum()} 天")
    return prem


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true")
    build(incremental=ap.parse_args().incremental)
