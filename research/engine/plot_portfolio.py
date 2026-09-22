# -*- coding: utf-8 -*-
"""v3.4 组合结构对比收益率曲线图 (2026-08-31)

画不同组合结构 (标的/比例不同, 规则统一 S3+轮动+无闸门) 的收益率曲线对比。
窗口: 2020-09-01 ~ 2026-08-20 全窗; 红利低波用 512890 (全历史可比),
      v3.4 实盘为 563020 (上市 2023-12, 费率 0.60%→0.20%, 上市后口径 IRR 更高, 见验证报告阶段十六)。

用法: C:/Anaconda3/python research/engine/plot_portfolio.py
输出: docs/组合结构对比_收益率曲线.png
"""
import sys
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_core import prep, simulate, full_metrics  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

# 不同组合结构 (统一规则: S3(周期) + 轮动分差≥5月度 + 无闸门)
COMBOS = [
    ("v3.4 现状 60/40\r\n(纳指+红利低波+豆粕 / AI+黄金)",
     dict(amounts={"513100": 2000, "512890": 2000, "159985": 2000, "159819": 2000, "518880": 2000},
          graded={"159819", "518880"},
          rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)])),
    ("旧 70/30\r\n(基本三只各2333 / 周期各1500)",
     dict(amounts={"513100": 2333, "512890": 2333, "159985": 2334, "159819": 1500, "518880": 1500},
          graded={"159819", "518880"},
          rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)])),
    ("4池无豆粕 70/30\r\n(纳指+红利低波 / AI+黄金)",
     dict(amounts={"513100": 2333, "512890": 2333, "159819": 1500, "518880": 1500},
          graded={"159819", "518880"},
          rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)])),
    ("60/40 无豆粕\r\n(纳指+红利低波各3000 / 周期各2000)",
     dict(amounts={"513100": 3000, "512890": 3000, "159819": 2000, "518880": 2000},
          graded={"159819", "518880"},
          rotate_groups=[(["513100", "512890"], 0.30), (["159819", "518880"], 0.15)])),
    ("去红利低波 60/40\r\n(纳指+豆粕 / AI+黄金)",
     dict(amounts={"513100": 3000, "159985": 3000, "159819": 2000, "518880": 2000},
          graded={"159819", "518880"},
          rotate_groups=[(["159819", "518880"], 0.15)])),
]


def run(cfg, ws, we):
    codes = list(cfg["amounts"].keys())
    idx, data, is_first, is_biweek, is_quarter = prep(ws, we, codes=codes)
    res = simulate(idx, data, is_first, is_biweek, is_quarter, cfg=cfg)
    nav = res["nav"]
    inject = float(sum(cfg["amounts"].values()))
    cum_in = pd.Series(np.cumsum(is_first.astype(float) * inject), index=nav.index)
    yield_ = (nav / cum_in - 1)
    dd = (nav / nav.cummax() - 1)
    m = full_metrics(res)
    return nav, yield_, dd, m


def plot(ws, we, title, fname):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=True,
                                   gridspec_kw={"height_ratios": [2.6, 1]})
    colors = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e"]
    li = 0
    for (name, cfg), c in zip(COMBOS, colors):
        nav, y, dd, m = run(cfg, ws, we)
        lw = 2.6 if li == 0 else 1.4
        ax1.plot(y.index, y * 100, label=f"{name}\n     IRR {m['irr']*100:+.1f}%  回撤 {m['dd']*100:-.1f}%  Calmar {m['calmar']:.2f}",
                 color=c, linewidth=lw, alpha=1.0 if li == 0 else 0.85)
        if li == 0:
            ax2.fill_between(dd.index, dd * 100, 0, color=c, alpha=0.25)
            ax2.plot(dd.index, dd * 100, color=c, lw=1.2)
        li += 1
    ax1.axhline(0, color="black", lw=0.8, ls="--")
    ax1.set_title(title + "\n收益率 = (组合总资产 − 累计投入) / 累计投入", fontsize=13)
    ax1.set_ylabel("资金加权收益率 (%)")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("回撤 (%)  ← 仅标 v3.4 现状")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(fname, dpi=150)
    print(f"已保存: {fname}")


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    plot("2020-09-01", "2026-08-20",
         "v3.4 组合结构对比 (全窗 2020-09 ~ 2026-08, 规则统一: S3+轮动+无闸门, 红利低波=512890)",
         os.path.join(out_dir, "组合结构对比_收益率曲线.png"))
