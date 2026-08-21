# -*- coding: utf-8 -*-
"""
ETF 定投策略日报 (经完整回测验证, 无未来函数)
=============================================

投资组合 (2026-08-14 终版, 全部经双周期验证):
  基本配置 70%: 纳指(513100) / 红利低波(512890) → 等额定投 + 分数轮动(每月30%, 分差≥5)
  周期卫星 30%: AI(159819) / 黄金(518880) → 分档投入 + 分数轮动(每月15%, 分差≥5)

核心机制 (每月初执行三件事):
  1. 定投: 新钱按分数分档 (≥90→3x | ≥70→2x | ≥50→1x | <50→0.25x)
  2. 轮动: 分差≥5时从低分者调仓到高分者 (再平衡纪律, 随机对照100%超随机)
  3. (v3.3) 已移除溢价闸门: 2026-08-21 真实数据重验——溢价影响~0.4pp仅保险价值, 去闸门 IRR +0.37pp/Calmar 2.87→2.91
  无止盈点: 轮动即渐进式高位减持 (止盈与轮动冲突, 验证2.55→2.26)

核心验证结论 (经引擎审计 + 完整回测确认):
  1. 慢牛不"慢牛": 黄金最长套牢2.8年, 回测含2025特殊行情, 合理预期8-12%年化
  2. 调节无效: BOLL/ATR/加权调节对任何资产块均无超额收益
     - 慢牛资产: BOLL中轨调节 vs 纯定投, 资产差异 < 1%
     - 周期资产: 任何调节方式 vs 固定3倍/2倍/1倍/0.5倍分档, 差异 < 1%
  3. 权重无差异: 50/30/20 vs 0/60/40 vs 均权, 资产差异 ±1%
  4. 换标无效: 固定池 vs 5种动态选标规则, 全部跑输; 100次随机换标胜率仅43%
  5. 打分有用但有限: 同投入资金额效率+8.1%(低位多投), 但资金闲置拖累总资产
  6. 最优模式 S3: 周期分档 90/70/50/<50 → 3x/2x/1x/0.25x (不空仓, 低档0.25x经全参数寻优确认)

溢价闸门已移除 (v3.3): 2026-08-21 真实溢价数据重验——闸门成本~0.4pp仅换崩塌保护,
  且 premium 缓存曾污染 (2025年30%溢价为假数据); 溢价保留在日报中作信息展示, 不触发动作

用法: python final_strategy.py [--monthly-budget 3000]
"""
import warnings
warnings.filterwarnings("ignore")

import sys
import os
import argparse
import numpy as np
import pandas as pd
import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据缓存目录 (前复权K线, 仓库内 data/ic_cache, 由 research/engine/fetch_ohlcv.py 生成)
CACHE = os.path.join(r"E:\autotest\autotest-script-devops\etf_scorer", "ic_cache")
if not os.path.exists(CACHE):
    CACHE = os.path.join(BASE_DIR, "ic_cache")

# 复用 etf_quant_strategy 的指数行情与格式函数 (丰富日报头部)
sys.path.insert(0, BASE_DIR)
try:
    from etf_quant_strategy import (
        fetch_index_data, format_index_header, fetch_realtime,
        get_premium,
    )
    HAS_ENHANCED = True
except Exception:
    HAS_ENHANCED = False

# ============ ETF 池 ============
ETF_POOL = {
    "512480": ("半导体", "热"), "515030": ("新能源", "热"), "159819": ("AI", "热"), "588000": ("科创50", "热"),
    "512980": ("传媒", "冷"), "512660": ("军工", "冷"), "512010": ("医药", "冷"), "515170": ("食品饮料", "冷"),
    "159915": ("创业板", "宽基"), "515790": ("光伏", "冷"), "516160": ("电池", "冷"), "512200": ("地产", "冷"),
    "159870": ("化工", "冷"), "512400": ("有色", "冷"), "510300": ("沪深300", "宽基"), "510500": ("中证500", "宽基"),
    # ---- 扩充候选 (2026-08-14 新增, 数据来自腾讯fqkline) ----
    "515880": ("通信", "热"), "159852": ("软件", "热"), "159869": ("游戏", "热"), "562500": ("机器人", "热"),
    "515230": ("软件TMT", "热"), "159781": ("双创50", "热"), "513180": ("恒生科技", "热"), "513330": ("恒生互联网", "热"),
    "512690": ("酒", "冷"), "159996": ("家电", "冷"), "159825": ("农业", "冷"),
    "515220": ("煤炭", "冷"), "515210": ("钢铁", "冷"), "159611": ("电力", "冷"), "159930": ("能源", "冷"),
    "516950": ("基建", "冷"), "512000": ("证券", "冷"), "512070": ("非银金融", "冷"),
    "512170": ("医疗", "冷"), "159992": ("创新药", "冷"), "159647": ("中药", "冷"),
    "515080": ("红利", "宽基"), "513500": ("标普500", "跨境"), "513520": ("日经", "跨境"),
    "518880": ("黄金", "避险"),
    "159985": ("豆粕", "商品"),  # 2026-08-20 加入基本配置 (商品保险, 低相关0.104)
}
LOOKBACK_YEARS = 5     # 选赛道窗口
MIN_HISTORY_YEARS = 3  # 参与选池的最低数据长度(年), 防残缺窗口
TOP_N = 2              # 周期卫星池大小 (AI+黄金)
QDII = set()

# 固定定投池 (用户指定, 低相关组合)
FIXED_POOL = ["159819", "518880"]  # AI + 黄金 (周期卫星, 分档)

# 基本配置类资产池 (纳指/红利低波/豆粕, 纯定投)
# 验证结论: BOLL中轨调节对这类资产无超额收益, 等额定投最优
# 黄金已移至周期卫星(分档) — 组合验证: 黄金放周期Calmar 2.01 vs 放基本1.71 (2026-08-14)
# 豆粕(159985) 2026-08-20 加入: 商品期货ETF, 与全池最大相关仅0.104, 2022熊市对冲
#   (2022年+61.4%), 混合场景三窗口 Calmar 1.95→2.05/1.59→3.62/3.01→3.20
#   ⚠️ 豆粕不参与轮动 (1x纯定投保险角色, 参与轮动 Calmar 2.05→1.78)
# ⚠️ 回测含2020-2026特殊行情(2025年全球放水+AI泡沫+商品超级周期),
#    合理年化收益预期 8-12%, 最大回撤可能 -30%, 最长套牢 2-3 年
SLOW_POOL = {
    "513100": "纳指",
    "512890": "红利低波",
    "159985": "豆粕",
}
# 基本配置中参与月度轮动的配对 (豆粕不参与: 低相关保险角色, 轮动无再平衡溢价)
SLOW_ROTATE_PAIRS = ["513100", "512890"]
# 慢牛资产中的QDII (需溢价闸门)
SLOW_QDII = {"513100"}  # 纳指

# ============ 打分函数 ============
def score_ma60(dev):
    if dev < -15: return 50
    if dev < -5: return 40
    if dev < 5: return 25
    if dev < 15: return 10
    return 0

def score_mom60(mom):
    if mom < -20: return 30
    if mom < -5: return 20
    if mom < 5: return 12
    if mom < 20: return 5
    return 0

def score_boll(price, ma20, std20):
    lower = ma20 - 2*std20; upper = ma20 + 2*std20
    if price < lower: return 20
    if price < ma20: return 14
    if price < upper: return 8
    return 0

def compute_score_series(s):
    """计算全历史打分序列 (用于选赛道均值)"""
    ma20 = s.rolling(20).mean()
    ma60 = s.rolling(60).mean()
    std20 = s.rolling(20).std()
    mom60 = s.pct_change(60)*100
    dev60 = (s/ma60 - 1)*100
    scores = pd.Series(index=s.index, dtype=float)
    valid = ma60.notna() & mom60.notna()
    scores.loc[valid] = (
        dev60[valid].map(score_ma60) +
        mom60[valid].map(score_mom60) +
        ((s[valid] < (ma20[valid]-2*std20[valid])).astype(float)*20 +
         ((s[valid] >= (ma20[valid]-2*std20[valid])) & (s[valid] < ma20[valid])).astype(float)*14 +
         ((s[valid] >= ma20[valid]) & (s[valid] < (ma20[valid]+2*std20[valid]))).astype(float)*8)
    )
    return scores

# ============ 数据 ============
def load_qfq(code):
    """加载前复权收盘价: qfq_* 与 ohlcv_* 两套缓存中取末条更新者
    (2026-08-21 修复: 旧版固定优先 qfq_*, 曾被陈旧 qfq 遮蔽已刷新的 ohlcv)"""
    best, best_last = None, None
    for name in (f"qfq_{code}.pkl", f"ohlcv_{code}.pkl"):
        f = os.path.join(CACHE, name)
        if not os.path.exists(f):
            continue
        try:
            d = pd.read_pickle(f)
            s = d.iloc[:, 0] if isinstance(d, pd.DataFrame) and "close" not in d.columns else (
                d["close"] if isinstance(d, pd.DataFrame) else d)
            if s is None or len(s) == 0:
                continue
            if best_last is None or s.index[-1] > best_last:
                best, best_last = s, s.index[-1]
        except Exception:
            continue
    return best

def get_realtime_price(code):
    """腾讯实时价"""
    try:
        mkt = "sh" if code.startswith(("5", "6")) else "sz"
        r = requests.get(f"https://qt.gtimg.cn/q={mkt}{code}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r.encoding = "gbk"
        parts = r.text.split("~")
        if len(parts) >= 38 and "none_match" not in r.text:
            return float(parts[3])
    except Exception:
        pass
    return None

def batch_realtime(codes):
    """腾讯批量实时行情: {code: (current, prev_close)}, 单次请求 (打分总表涨跌列)"""
    if not codes:
        return {}
    try:
        syms = ",".join(("sh" if c.startswith(("5", "6")) else "sz") + c for c in codes)
        r = requests.get(f"https://qt.gtimg.cn/q={syms}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.encoding = "gbk"
        out = {}
        for line in r.text.strip().split("\n"):
            if "=" not in line or "none_match" in line:
                continue
            sym = line.split("=")[0].split("_")[-1].strip()
            parts = line.split("~")
            if len(parts) < 38:
                continue
            try:
                out[sym[2:]] = (float(parts[3]), float(parts[4]))
            except ValueError:
                continue
        return out
    except Exception:
        return {}

def get_nav(code):
    """QDII溢价 (备用)"""
    if code not in QDII:
        return None
    try:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {"fundCode": code, "pageIndex": 1, "pageSize": 3,
                  "startDate": "2026-01-01", "endDate": "2026-12-31"}
        r = requests.get(url, params=params,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"},
                         timeout=8)
        rows = ((r.json().get("Data") or {}).get("LSJZList") or [])
        if rows and float(rows[0]["DWJZ"]) > 0:
            return float(rows[0]["DWJZ"])
    except Exception:
        pass
    return None

# ============ 输出格式工具 (中文/emoji 宽度对齐) ============
def _w(s):
    """显示宽度: CJK/emoji 占2, ASCII占1 (终端对齐用)
    特殊处理: VS16变体选择符(0xFE0F)不占宽; ⭐➡等杂项符号区算2宽"""
    w = 0
    for c in str(s):
        o = ord(c)
        if o == 0xFE0F:
            continue  # 变体选择符 (emoji修饰) 不占显示宽
        if 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF or 0x2E50 <= o <= 0x2E7F:
            w += 2  # emoji/杂项符号区 (⭐➡🔥💰等)
        elif o > 0x2E80:
            w += 2  # CJK 中文
        else:
            w += 1
    return w

def _pad(s, width, align='l'):
    """按显示宽度填充到指定宽度"""
    s = str(s)
    gap = width - _w(s)
    if gap <= 0:
        return s
    if align == 'r':
        return ' ' * gap + s
    if align == 'c':
        l = gap // 2
        return ' ' * l + s + ' ' * (gap - l)
    return s + ' ' * gap

def _tbl(headers, rows, aligns=None, gap=3):
    """渲染对齐表格. headers=[(标题, 列宽)], rows=[[值,...]]
    表头与数据同对齐方向; 列间距 gap 空格"""
    if aligns is None:
        aligns = ['l'] * len(headers)
    sep = ' ' * gap
    out = ['  ' + sep.join(_pad(h, w, a) for (h, w), a in zip(headers, aligns))]
    out.append('  ' + sep.join('─' * w for w in [w for _, w in headers]))
    for row in rows:
        out.append('  ' + sep.join(_pad(v, w, a) for v, w, a in zip(row, [w for _, w in headers], aligns)))
    return '\n'.join(out)

def _hdr(title, width=70, icon="📊"):
    """区块标题: 上下横线"""
    return f"{icon} {title}\n{'─' * width}"

# ============ 数据新鲜度 (2026-08-21: 日报曾用 9 天前的缓存分数决策) ============
STALE_DAYS = 5   # 缓存末条距今超过此天数 → 醒目警告

def check_cache_staleness(codes, exit_on_missing=True):
    """检查核心标的缓存末条日期, 陈旧则醒目警告。返回 {code: 距今天数}"""
    today = pd.Timestamp.now().normalize()
    gaps = {}
    stale_list = []
    for c in codes:
        s = load_qfq(c)
        if s is None:
            stale_list.append(f"{c}(缺失)")
            continue
        gap = int((today - s.index[-1]).days)
        gaps[c] = gap
        if gap > STALE_DAYS:
            stale_list.append(f"{c}(距今{gap}天, 末条{s.index[-1].date()})")
    if stale_list:
        print("═" * 70)
        print("  ⚠️⚠️ 数据缓存陈旧: " + ", ".join(stale_list))
        print("     打分/分档/轮动指令基于旧价格! 请先执行数据更新:")
        print("     python research/engine/fetch_ohlcv.py  或  本脚本加 --refresh-data")
        print("═" * 70)
    return gaps

def refresh_data(codes):
    """增量刷新核心标的缓存 (复用 research/engine/fetch_ohlcv)"""
    sys.path.insert(0, os.path.join(BASE_DIR, "..", "research", "engine"))
    try:
        from fetch_ohlcv import update_code
    except ImportError:
        print("  ❌ 无法导入 fetch_ohlcv (research/engine/)")
        return
    for c in codes:
        st = update_code(c)
        print(f"  数据刷新 {c}: {st}")


# ============ 主流程 ============
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monthly-budget", type=float, default=3000,
                        help="周期卫星月预算(元), 基本配置=此值×6/4 (周期40%+基本60%)")
    parser.add_argument("--lookback-years", type=int, default=LOOKBACK_YEARS)
    parser.add_argument("--refresh-data", action="store_true",
                        help="运行前增量刷新核心标的缓存 (research/engine/fetch_ohlcv)")
    args = parser.parse_args()

    core_codes = list(SLOW_POOL.keys()) + FIXED_POOL
    if args.refresh_data:
        print("🔄 刷新数据缓存 (核心标的)...")
        refresh_data(core_codes)
    check_cache_staleness(core_codes)

    # 60/40 预算口径: 周期卫星40% + 基本配置60% (2026-08-21 修复后引擎重跑: Calmar 2.71→2.82)
    basic_budget = args.monthly_budget * 6 / 4

    today = pd.Timestamp.now().normalize()
    lookback_start = today - pd.DateOffset(years=args.lookback_years)

    # 1. 计算所有标的分数均值 (选赛道)
    W = 70
    print("═" * W)
    print(f"  📊 ETF 定投策略日报 | {today.strftime('%Y-%m-%d')}")
    print(f"     策略: 基本配置(纯定投60%) + 周期卫星(分档40%)")
    print("═" * W)

    # 大盘指数头 (复用 etf_quant_strategy)
    if HAS_ENHANCED:
        try:
            idx_data = fetch_index_data()
            header = format_index_header(idx_data)
            if header:
                print(f"\n{header}")
        except Exception:
            pass

    score_means = {}
    for code, (name, cat) in ETF_POOL.items():
        s = load_qfq(code)
        if s is None: continue
        scores = compute_score_series(s)
        mask = (scores.index >= lookback_start) & (scores.index <= today)
        hist = scores[mask].dropna()
        # 最低数据长度门槛: 至少满 MIN_HISTORY_YEARS 年且覆盖过半选池窗口
        hist_years = (s.index[-1] - s.index[0]).days / 365.25
        min_days = int(MIN_HISTORY_YEARS * 252)
        if len(hist) > min_days and hist_years >= MIN_HISTORY_YEARS:
            score_means[code] = hist.mean()
        else:
            print(f"   [跳过] {name} ({code}): 数据仅{hist_years:.1f}年, 不足{MIN_HISTORY_YEARS}年门槛")

    if len(score_means) < TOP_N:
        print("❌ 数据不足, 无法选赛道")
        return

    # 2. 选池: 固定池 (AI+有色+创业板), 不换标
    #    已验证: 5种动态换标规则均无稳定超额(100次随机池固定>换标仅43%), 固定池免换手成本
    pool = [c for c in FIXED_POOL if c in score_means]
    if len(pool) < TOP_N:
        # 固定池不足时, 从候选中补齐 (数据缺失兜底, 非换标)
        candidates = [c for c, _ in sorted(score_means.items(), key=lambda x: x[1]) if c not in pool]
        pool += candidates[:TOP_N - len(pool)]

    # 定投池说明: 固定组合不换标, 5年均值弹性排序已并入下方打分总表(均值列), 不再单独列表
    pool_names = " + ".join(ETF_POOL[c][0] for c in pool)
    print(f"\n🎯 当前定投池: 固定组合 [{pool_names}] 不换标 (5种动态换标规则均无稳定超额, 固定池免换手成本)")

    # 2.5 全ETF打分总表: 分值 + 强势度(bull_align) + 趋势 + 状态 + 建议, 一页整合
    # 选中标记: ⭐周期=周期卫星(分档定投) ◆基本=基本配置(纯定投)
    print(f"\n{_hdr(f'全部ETF打分总表 (分值 / {args.lookback_years}年均值 / 强势度 / 建议)', icon='📊')}")
    print(f"   标记: ⭐周期=当前周期卫星池  ◆基本=基本配置池 | 强势度: bull_align 0-1 (🟢>0.6强势 🟡0.3-0.6震荡 🔴<0.3转弱)")

    def _bull_align(s):
        if s is None or len(s) < 300: return np.nan, 0, np.nan
        ma20 = s.rolling(20).mean()
        ma60 = s.rolling(60).mean()
        ma250 = s.rolling(250).mean()
        ba = ((ma20 > ma60) & (ma60 > ma250)).astype(float).rolling(60).mean()
        cur_ba = ba.iloc[-1]
        pre_ba = ba.iloc[-60] if len(ba) > 60 else np.nan
        streak = 0
        for v in ba.iloc[::-1]:
            if v < 0.2: streak += 1
            else: break
        return cur_ba, streak, pre_ba

    current_scores = []
    for code, (name, cat) in list(ETF_POOL.items()) + [(c, (n, "基本")) for c, n in SLOW_POOL.items() if c not in ETF_POOL]:
        s = load_qfq(code)
        if s is None: continue
        scores = compute_score_series(s).dropna()
        if len(scores) == 0: continue
        cur = scores.iloc[-1]
        if np.isnan(cur): continue
        # 5年均值
        mask = (scores.index >= lookback_start) & (scores.index <= today)
        mean_hist = scores[mask]
        mean_val = mean_hist.mean() if len(mean_hist) > 300 else np.nan
        # 当前分值等级
        if cur >= 90: lv = "🔥深度低位"
        elif cur >= 70: lv = "🟢低位"
        elif cur >= 50: lv = "➡️正常"
        elif cur >= 30: lv = "🟡偏高"
        else: lv = "🟡高位"
        # 强势度
        cur_ba, streak, pre_ba = _bull_align(s)
        if np.isnan(cur_ba):
            ba_str, trend, st = "—", "—", "—"
        else:
            ba_str = f"{cur_ba:.2f}"
            if cur_ba > 0.6: st = "🟢强势"
            elif cur_ba >= 0.3: st = "🟡震荡"
            else: st = f"🔴转弱{streak}日" if streak > 0 else "🔴转弱"
            if not np.isnan(pre_ba):
                trend = "↑" if cur_ba > pre_ba + 0.05 else ("↓" if cur_ba < pre_ba - 0.05 else "→")
            else:
                trend = "—"
        # 角色与建议
        if code in pool:
            role = "⭐周期"
            if np.isnan(cur_ba): advice = "按规则投入"
            elif cur_ba >= 0.6: advice = "保持定投"
            elif cur_ba >= 0.3: advice = "正常观察"
            elif streak >= 60: advice = "⚠️趋势终结·年度审视"
            else: advice = "转弱初期·不宜卖"
        elif code in SLOW_POOL:
            role = "◆基本"
            advice = "纯定投不调"
        else:
            role = ""
            advice = ""
        current_scores.append((code, name, cur, mean_val, lv, ba_str, trend, st, advice, role))
        # 决策日志: 收集核心5标的分数
        if code in ("513100", "512890", "159985", "159819", "518880"):
            if "scores_5" not in locals():
                scores_5 = {}
            scores_5[code] = float(cur)

    # 选中标的置顶, 其余按5年均值升序 (弹性最强在前), 均值缺失排最后
    current_scores.sort(key=lambda x: (x[9] == "", np.isnan(x[3]), x[3] if not np.isnan(x[3]) else 0))

    # 本月调仓方向 (与下方"调仓指令"同一判定: 分差≥5, 基本30%/周期15%)
    # 在总表"建议"列直接标注, 一眼看到谁调出/谁调入
    # 基本配置仅 SLOW_ROTATE_PAIRS (纳指/红利低波) 参与轮动, 豆粕为保险角色不参与
    rot_tag = {}
    for codes, rot_pct in [(SLOW_ROTATE_PAIRS, 0.30), (pool, 0.15)]:
        valid = {c: sc for c, _, sc, *_ in current_scores if c in codes and not np.isnan(sc)}
        if len(valid) < 2: continue
        c1, c2 = list(valid.keys())[:2]
        diff = abs(valid[c1] - valid[c2])
        if diff < 5:
            rot_tag[c1] = rot_tag[c2] = f"不调仓(分差{diff:.0f}<5)"
            continue
        loser = c1 if valid[c1] < valid[c2] else c2
        winner = c2 if loser == c1 else c1
        nw = ETF_POOL.get(winner, (SLOW_POOL.get(winner, winner), ""))[0]
        nl = ETF_POOL.get(loser, (SLOW_POOL.get(loser, loser), ""))[0]
        pct_txt = f"{int(rot_pct*100)}%"
        rot_tag[loser] = f"🔻调出{pct_txt}→{nw}"
        rot_tag[winner] = f"🔺调入{pct_txt}←{nl}"

    # 批量取实时行情 (涨跌列, 单次请求)
    rt_map = batch_realtime([r[0] for r in current_scores])

    rows = []
    for code, name, cur, mean_val, lv, ba_str, trend, st, advice, role in current_scores:
        mean_str = f"{mean_val:.1f}" if not np.isnan(mean_val) else "—"
        diff = ""
        if not np.isnan(mean_val):
            d = cur - mean_val
            diff = f"{d:+.1f}"  # 正=当前比均值高(更超跌), 负=当前比均值低(更贵)
        chg_str = "—"
        if code in rt_map:
            cur_p, prev_p = rt_map[code]
            if prev_p and prev_p > 0:
                chg_pct = (cur_p / prev_p - 1) * 100
                chg_str = f"{'📈' if chg_pct >= 0 else '📉'}{chg_pct:+.2f}%"
        if code in rot_tag:
            advice = f"{advice} | {rot_tag[code]}" if advice else rot_tag[code]
        rows.append([name, f"({code})", chg_str, f"{cur:.0f}", mean_str, diff, lv, ba_str, trend, st, advice, role])
    print(_tbl(
        [("名称", 9), ("代码", 8), ("今日", 8), ("分值", 5), ("均值", 5), ("vs均值", 7), ("位置", 9), ("强势", 5), ("趋势", 4), ("状态", 10), ("建议/调仓", 24), ("选中", 6)],
        rows,
        aligns=['l', 'l', 'r', 'r', 'r', 'r', 'l', 'r', 'c', 'l', 'l', 'c'],
    ))
    print(f"   注: ① 转弱≠卖出(历史跌破后60日反弹+9.9%, 宜等反弹) ② 连续60日强势度<0.2=趋势终结区")
    print(f"       (后250日超额-6pp, 双周期验证) → 纳入年度替换审视 ③ 自动卖出已验证亏钱(IRR砍85%)")

    # 3. 每只当前分数 → 定投倍数
    print(f"\n{_hdr(f'今日定投清单 (周期卫星40%, 月预算{args.monthly_budget:.0f}元)', icon='💰')}")
    total = 0
    rows = []
    for code in pool:
        name, cat = ETF_POOL[code]
        s = load_qfq(code)
        if s is None: continue
        price = get_realtime_price(code) or s.iloc[-1]
        # 用最新可得数据算分数 (用历史最后一天的收盘)
        scores = compute_score_series(s)
        cur_score = scores.dropna().iloc[-1]
        dev60_display = ""
        ma60 = s.rolling(60).mean().iloc[-1]
        if not np.isnan(ma60):
            dev60_display = f"MA60偏离{(price/ma60-1)*100:+.1f}%"

        if np.isnan(cur_score): mult = 1.0; level = "数据不足"
        elif cur_score >= 90: mult = 3.0; level = "🔥深度低位"
        elif cur_score >= 70: mult = 2.0; level = "🟢低位"
        elif cur_score >= 50: mult = 1.0; level = "➡️正常"
        else: mult = 0.25; level = "🟡减投(高位)"  # 低档0.25x: 全参数寻优确认(急涨区更保守, Calmar提升)

        per_etf = args.monthly_budget / TOP_N
        amt = per_etf * mult
        total += amt

        # 涨跌幅 (复用 etf_quant_strategy 的 fetch_realtime)
        chg_str = ""
        if HAS_ENHANCED:
            try:
                rt = fetch_realtime(code)
                if rt:
                    chg_pct = (price - rt["prev_close"]) / rt["prev_close"] * 100 if rt["prev_close"] else 0
                    arrow = "📈" if chg_pct >= 0 else "📉"
                    chg_str = f"{arrow}{chg_pct:+.2f}%"
            except Exception:
                pass

        # 分值视觉条 (0-100) — 移到档位后, 不单独占列
        bar_len = 16
        filled = int(cur_score / 100 * bar_len) if not np.isnan(cur_score) else 8
        score_bar = f"[{'█'*filled}{'░'*(bar_len-filled)}]"

        rows.append([name, f"({code})", f"{cur_score:.0f}", level, score_bar,
                     f"{mult:.1f}x", f"{amt:.0f}元", dev60_display, chg_str])

    print(_tbl(
        [("标的", 8), ("代码", 9), ("分值", 5), ("档位", 12), ("分值条", 18), ("倍数", 5), ("金额", 7), ("信号", 16), ("涨跌", 9)],
        rows,
        aligns=['l', 'l', 'r', 'l', 'l', 'r', 'r', 'l', 'l'],
    ))
    print(f"\n   ── 周期卫星本月合计: {total:.0f}元 (基本配置另计)")
    print(f"   注: ≥2x/3x 加投从现金储备池扣 (引擎约束: 不透支, 需前期 0.25x 月积累),")
    print(f"       现金不足时按池余额可投额执行, 不动用未来预算")

    # 池内状态评估: 分数每日更新, 池子固定不换标
    # 周期池(⭐): 分数驱动分档定投 | 基本配置(◆): 1x 纯定投, 分数仅展示 (v3.3: 豆粕保险不加档)
    print(f"\n{_hdr('池内状态评估 (5只池内标的, 分数每日更新)', icon='🔄')}")
    for code in pool:
        name, cat = ETF_POOL[code]
        s = load_qfq(code)
        if s is None: continue
        scores = compute_score_series(s).dropna()
        if len(scores) == 0: continue
        cur = scores.iloc[-1]
        if np.isnan(cur): continue
        if cur >= 90:
            print(f"   ⭐ {_pad(name, 8)}: 🔥 深度低位({cur:.0f}分) → 黄金坑, 按3倍重仓")
        elif cur < 30:
            print(f"   ⭐ {_pad(name, 8)}: 🟡 高位({cur:.0f}分) → 减投(0.25x), 避免追高")
        elif cur < 50:
            print(f"   ⭐ {_pad(name, 8)}: 🟡 偏高({cur:.0f}分) → 减投(0.25x)")
        else:
            print(f"   ⭐ {_pad(name, 8)}: ✅ 位置合理({cur:.0f}分) → 按规则投入")
    for code, name in SLOW_POOL.items():
        s = load_qfq(code)
        if s is None: continue
        scores = compute_score_series(s).dropna()
        if len(scores) == 0: continue
        cur = scores.iloc[-1]
        if np.isnan(cur): continue
        if cur < 30:
            print(f"   ◆ {_pad(name, 8)}: 🟡 高位({cur:.0f}分) → 1x 纯定投(分数仅展示, 不加减仓)")
        elif cur < 50:
            print(f"   ◆ {_pad(name, 8)}: 🟡 偏高({cur:.0f}分) → 1x 纯定投(分数仅展示, 不加减仓)")
        elif cur >= 70:
            print(f"   ◆ {_pad(name, 8)}: 🟢 低位({cur:.0f}分) → 1x 纯定投(分数仅展示, 不加减仓)")
        else:
            print(f"   ◆ {_pad(name, 8)}: ✅ 位置合理({cur:.0f}分) → 1x 纯定投(分数仅展示)")

    # ============ 强势度数据已整合进上方"全部ETF打分总表" ============
    # 验证结论: 强势赛道是唯一alpha来源, bull_align(MA20>MA60>MA250成立占比)
    # 是实证有效的强势识别因子(IC60+0.094/IC120+0.180/79%正占比, 前周期同样成立)
    # 切换信号: 连续60日<0.2(持续空头) → 后250日超额-6pp(双周期验证); 但自动卖出
    # 执行亏钱(IRR砍85%, 信号滞后+踏空深V反弹) → 为人工决策参考, 非自动信号

    # 切换参考: 全池当前 bull_align 排序 (人工换标时的候选输入)
    print(f"\n{_hdr('切换参考 (全池当前强势排序, 人工换标候选)', icon='🔁')}")
    cands = []
    for code, (name, cat) in ETF_POOL.items():
        s = load_qfq(code)
        if s is None or len(s) < 300: continue
        ma20 = s.rolling(20).mean()
        ma60 = s.rolling(60).mean()
        ma250 = s.rolling(250).mean()
        ba = ((ma20 > ma60) & (ma60 > ma250)).astype(float).rolling(60).mean()
        cb = ba.iloc[-1]
        if np.isnan(cb): continue
        cands.append((name, code, cb))
    cands.sort(key=lambda x: -x[2])
    cand_rows = []
    for name, code, cb in cands[:6]:
        mark = "⭐" if code in pool else ""
        in_pool = "池内" if code in pool else "候选"
        cand_rows.append([name, f"({code})", f"{cb:.2f}", in_pool, mark])
    print(_tbl(
        [("标的", 8), ("代码", 8), ("bull_align", 10), ("池内/候选", 8), ("", 2)],
        cand_rows,
        aligns=['l', 'l', 'r', 'l', 'c'],
    ))
    print(f"   注: 机械换标已验证无效(43%≈随机), 此列表仅供人工年度审视时参考;")
    print(f"       换标需综合判断(强势期+主题逻辑), 不因单日信号机械执行")

    # ============ 基本配置类资产 (纯定投, 已验证调节无效) ============
    print(f"\n{_hdr(f'基本配置资产 (纳指/红利低波/豆粕, 占预算60%={basic_budget:.0f}元/月) | 纯定投', icon='🏦')}")
    print(f"   策略: 每月等额定投, 不做BOLL调节 (豆粕=商品保险, 低相关0.104, 不参与轮动)")
    print(f"   验证: BOLL中轨调节对慢牛资产无超额收益, 纯定投最优; 豆粕加入三窗口 Calmar 全面提升\n")

    slow_total = 0
    slow_rows = []
    for code, name in SLOW_POOL.items():
        s = load_qfq(code)
        if s is None: continue
        price = get_realtime_price(code) or s.iloc[-1]

        # 基本配置 = 纯定投 1x (2026-08-21 v3.3: 移除溢价闸门——验证: 闸门成本~0.4pp仅换崩塌保护,
        #   且 redirect 已消除现金拖累, 去闸门 IRR +0.37pp/Calmar 2.87→2.91; 溢价仅作信息展示)
        mult = 1.0
        level = "🟢 正常定投"
        prem_str = ""
        if code in SLOW_QDII:
            try:
                prem = get_premium(code, price)
                if prem:
                    prem_str = f"{prem['premium']:+.1f}%"
                    if prem.get("stale"):
                        prem_str += "⚠️净值陈旧"
            except Exception:
                pass

        amt = basic_budget / len(SLOW_POOL)
        slow_total += amt
        chg_str = ""
        if HAS_ENHANCED:
            try:
                rt = fetch_realtime(code)
                if rt:
                    chg_pct = (price - rt["prev_close"]) / rt["prev_close"] * 100 if rt["prev_close"] else 0
                    arrow = "📈" if chg_pct >= 0 else "📉"
                    chg_str = f"{arrow}{chg_pct:+.2f}%"
            except Exception:
                pass
        slow_rows.append([name, f"({code})", f"{price:.3f}", chg_str,
                          f"{mult:.1f}x", f"{amt:.0f}元", level, prem_str])

    print(_tbl(
        [("标的", 7), ("代码", 8), ("现价", 7), ("涨跌", 8), ("倍数", 4), ("金额", 6), ("状态", 22), ("溢价", 6)],
        slow_rows,
        aligns=['l', 'l', 'r', 'l', 'r', 'r', 'l', 'r'],
    ))
    print(f"\n   规则: 等额定投 | 纳指溢价>8%改场外申购(按净值成交, 实证: >8%买入后60日-4.1%)")

    # ============ 分数轮动指令 (再平衡纪律, 非择时) ============
    # 验证: 分差>=5触发, 基本配置30%/周期卫星15%, 双窗口Calmar 1.78→2.55 (2026-08-14)
    # 机制: 再平衡溢价——从高分位(涨多)流向低分位(跌多), 纪律性收割相对强弱切换
    # 与止盈互斥: 轮动已含渐进式高位减持, 止盈打断轮动(叠加验证2.55→2.26)
    print(f"\n{_hdr('本月分数调仓指令 (再平衡纪律, 分差≥5触发)', icon='🔄')}")
    print(f"   方向: 从低分(高位) → 高分(低位) | 基本配置调30%持仓 | 周期卫星调15%持仓")
    for block_label, codes, rot_pct in [("基本配置", SLOW_ROTATE_PAIRS, 0.30),
                                        ("周期卫星", pool, 0.15)]:
        if len(codes) < 2:
            continue
        sc_map = {}
        for code in codes:
            s = load_qfq(code)
            if s is None:
                continue
            sc_series = compute_score_series(s)
            cur = sc_series.dropna().iloc[-1] if len(sc_series.dropna()) else np.nan
            sc_map[code] = cur
        valid = {c: v for c, v in sc_map.items() if not np.isnan(v)}
        if len(valid) < 2:
            print(f"   {block_label}: 数据不足")
            continue
        c1, c2 = list(valid.keys())[:2]
        diff = abs(valid[c1] - valid[c2])
        n1 = ETF_POOL.get(c1, (SLOW_POOL.get(c1, c1), ""))[0]
        n2 = ETF_POOL.get(c2, (SLOW_POOL.get(c2, c2), ""))[0]
        if diff >= 5:
            loser = c1 if valid[c1] < valid[c2] else c2
            winner = c2 if loser == c1 else c1
            nl = ETF_POOL.get(loser, (SLOW_POOL.get(loser, loser), ""))[0]
            nw = ETF_POOL.get(winner, (SLOW_POOL.get(winner, winner), ""))[0]
            print(f"   {block_label}: {nl} {valid[loser]:.0f}分(高位) → {nw} {valid[winner]:.0f}分(低位) | "
                  f"分差{diff:.0f}分≥5 → 调出{nl}持仓的{int(rot_pct*100)}%买入{nw}")
        else:
            print(f"   {block_label}: {n1} {valid[c1]:.0f}分 vs {n2} {valid[c2]:.0f}分 | "
                  f"分差{diff:.0f}分<5 → 本月不调仓(方向噪声)")
    print(f"   注: 无条件再平衡最优(触发条件损失溢价)")

    # ============ 今日推荐汇总 ============
    print(f"\n{_hdr('今日推荐汇总 (V0策略)', icon='📌')}")
    print(_tbl(
        [("资产块", 10), ("标的", 18), ("投入", 8), ("说明", 30)],
        [
            ["周期卫星", "AI / 黄金", f"{total:.0f}元", "分档: 低位多投(3x/2x), 高位减半(0.25x)"],
            ["基本配置", "纳指 / 红利低波 / 豆粕", f"{slow_total:.0f}元", "等额定投, 豆粕不轮动 (v3.3 去溢价闸门)"],
            ["合计", "5 标的", f"{total + slow_total:.0f}元",
             f"月预算: 周期{args.monthly_budget:.0f}(30%) + 基本{basic_budget:.0f}(70%)"],
        ],
        aligns=['l', 'l', 'r', 'l'],
    ))

    print(f"\n{_hdr('分值说明 (0-100, 逆向指标: 分值越高=越超跌=越值得多投)', icon='📐')}")
    print(f"   90-100 🔥 深度低位(黄金坑) → 3倍重仓")
    print(f"   70-89  🟢 低位              → 2倍加仓")
    print(f"   50-69  ➡️ 正常              → 1倍定投")
    print(f"   30-49  🟡 偏高              → 0.25x减投")
    print(f"   0-29   🟡 高位              → 0.25x减投")
    print(f"   分值 = MA60偏离(50) + 60日动量(30) + BOLL位置(20)")
    print(f"\n💡 规则: ≥90投3倍 | ≥70投2倍 | ≥50投1倍 | <50投0.25x (不空仓)")
    print(f"   池子: 固定 AI+黄金 (组合验证: 黄金放周期Calmar 2.01 vs 放基本1.71)")
    print(f"   基本配置: 纳指/红利低波/豆粕 等额定投 (豆粕=商品保险, 不参与轮动)")
    print(f"   ⚠️ 合理年化预期 8-12%, 回测含2025特殊行情, 仅供参考, 不构成投资建议")

    # ============ 决策日志 (评审问题六: 把未来变成样本外) ============
    # 每日追加决策快照: 分数/倍数/溢价/轮动 — 3个月后可检验执行偏差与真实样本外
    try:
        import csv
        from datetime import datetime as _dt
        journal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decision_journal.csv")
        jrow = {
            "date": _dt.now().strftime("%Y-%m-%d"),
            "naz_s": f"{scores_5.get('513100', ''):.0f}" if isinstance(scores_5.get('513100'), float) else "",
            "hld_s": f"{scores_5.get('512890', ''):.0f}" if isinstance(scores_5.get('512890'), float) else "",
            "dou_s": f"{scores_5.get('159985', ''):.0f}" if isinstance(scores_5.get('159985'), float) else "",
            "ai_s": f"{scores_5.get('159819', ''):.0f}" if isinstance(scores_5.get('159819'), float) else "",
            "gold_s": f"{scores_5.get('518880', ''):.0f}" if isinstance(scores_5.get('518880'), float) else "",
        }
        if not os.path.exists(journal_path):
            with open(journal_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=list(jrow.keys())).writeheader()
        with open(journal_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=list(jrow.keys())).writerow(jrow)
    except Exception:
        pass

if __name__ == "__main__":
    main()
