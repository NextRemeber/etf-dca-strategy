#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股ETF量化投资策略引擎
基于多指标共振的智能定投系统

ETF: 黄金ETF(518880) / 纳指ETF(513100) / 红利低波ETF(512890)
指标: BOLL20 + RSI14 + MACD + MA系统 + 成交量分析

用法:
    python etf_quant_strategy.py                     # 默认输出完整报告
    python etf_quant_strategy.py --daily             # 每日操作建议
    python etf_quant_strategy.py --monthly           # 月度定投方案
    python etf_quant_strategy.py --signal-only       # 仅输出信号摘要
    python etf_quant_strategy.py --rsi               # RSI定投方案（定时任务用）
"""

import requests
import re
import sys
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

# ==================== 配置 ====================

ETF_CONFIG = [
    {"code": "518880", "name": "黄金ETF", "desc": "黄金", "base_alloc": 0.20, "full_name": "华安黄金ETF"},
    {"code": "513100", "name": "纳指ETF", "desc": "纳指", "base_alloc": 0.40, "full_name": "国泰纳指ETF"},
    {"code": "512890", "name": "红利低波ETF", "desc": "红利低波", "base_alloc": 0.40, "full_name": "华泰柏瑞红利低波ETF"},
]

# ==================== 指数配置 ====================
# 在每日 ETF 报告中显示大盘指数行情
INDEX_CONFIG = [
    {"code": "000001", "market": "sh", "name": "上证指数"},
    {"code": "399006", "market": "sz", "name": "创业板指"},
    {"code": "^IXIC", "market": "us", "name": "纳斯达克"},
]

MONTHLY_AMOUNT = 5000  # 每月定投总额
CASH_RESERVE = 500     # 预留备用现金

# ============ RSI 定投分配规则（核心） ============
# RSI 越低 → 越接近下轨 → 越值得加仓
# RSI 越高 → 越接近上轨 → 越应该减仓
RSI_DCA_RULES = [
    {"rsi_min": 0,  "rsi_max": 25, "level": "极度低估", "factor": 2.0, "action": "🔥 极度超卖，加倍定投"},
    {"rsi_min": 25, "rsi_max": 35, "level": "低估",     "factor": 1.5, "action": "✅ 超卖区，加码50%"},
    {"rsi_min": 35, "rsi_max": 45, "level": "偏低",     "factor": 1.2, "action": "✅ 偏低，加码20%"},
    {"rsi_min": 45, "rsi_max": 55, "level": "中性",     "factor": 1.0, "action": "➡️ 正常区间，按计划"},
    {"rsi_min": 55, "rsi_max": 65, "level": "偏高",     "factor": 0.7, "action": "⚠️ 偏高，减码30%"},
    {"rsi_min": 65, "rsi_max": 75, "level": "高估",     "factor": 0.3, "action": "🔴 高估区，减码70%"},
    {"rsi_min": 75, "rsi_max": 100, "level": "极度高估", "factor": 0.0, "action": "🔴 极度高估，暂停定投"},
]

# 组合整体是否建议投入的规则
# 根据三只ETF的平均RSI判断
INVEST_RULES = [
    {"avg_rsi_min": 0,  "avg_rsi_max": 30, "verdict": "🔥🔥 强烈建议投入", "reason": "市场整体超卖，黄金坑机会"},
    {"avg_rsi_min": 30, "avg_rsi_max": 40, "verdict": "✅ 建议投入",       "reason": "多数标的低估，逢低布局"},
    {"avg_rsi_min": 40, "avg_rsi_max": 55, "verdict": "➡️ 正常投入",       "reason": "市场中性，按计划定投"},
    {"avg_rsi_min": 55, "avg_rsi_max": 65, "verdict": "⚠️ 谨慎投入",       "reason": "部分标的偏高，精选入场"},
    {"avg_rsi_min": 65, "avg_rsi_max": 75, "verdict": "🔴 不建议投入",     "reason": "市场整体高估，等待回调"},
    {"avg_rsi_min": 75, "avg_rsi_max": 100, "verdict": "🔴🔴 暂停投入",     "reason": "市场过热，全部暂停"},
]

# 极端RSI单项否决规则：如果任何ETF超过此RSI，直接影响投入决定
SINGLE_VETO = {"sell_ceiling": 78, "buy_floor": 22}

# ==================== 数据获取 ====================

def _try_sources(sources: List[Tuple[str, callable]]):
    """
    多数据源 fallback: 按顺序尝试, 失败自动切换下一个。
    sources: [(数据源名, 无参函数), ...]
    返回第一个成功的结果, 全部失败返回 None。
    """
    for name, fn in sources:
        try:
            result = fn()
            if result is not None and result != [] and result != {}:
                return result
        except Exception:
            pass
    return None


def _tencent_symbol(code: str) -> str:
    """ETF代码转腾讯符号 (sh/sz前缀)"""
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def fetch_realtime_tencent(code: str) -> Optional[dict]:
    """腾讯实时行情 (fallback源)。字段位置: [3]现价 [4]昨收 [5]今开 [33]最高 [34]最低 [36]量 [37]额"""
    sym = _tencent_symbol(code)
    resp = requests.get(f"https://qt.gtimg.cn/q={sym}",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    resp.encoding = "gbk"
    parts = resp.text.split("~")
    if len(parts) < 38 or "none_match" in resp.text:
        return None
    current = float(parts[3])
    prev_close = float(parts[4])

    def _safe_float(idx, default):
        try:
            v = parts[idx].strip()
            return float(v) if v else default
        except (ValueError, IndexError):
            return default

    return {
        "current": current,
        "prev_close": prev_close,
        "open": _safe_float(5, current),
        "high": _safe_float(33, max(current, prev_close)),
        "low": _safe_float(34, min(current, prev_close)),
        "volume": _safe_float(36, 0.0),
        "amount": _safe_float(37, 0.0),
    }


def fetch_klines_tencent(code: str, days: int = 60) -> List[float]:
    """腾讯前复权K线收盘价 (fallback源, qfq避免份额折算失真)"""
    sym = _tencent_symbol(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{max(days + 10, 100)},qfq")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    payload = resp.json()
    item = (payload.get("data") or {}).get(sym) or {}
    rows = item.get("qfqday") or item.get("day") or []
    closes = [float(r[2]) for r in rows if len(r) >= 3]
    return closes[-days:] if closes else []


def fetch_klines_full_tencent(code: str, days: int = 60) -> Optional[Dict]:
    """腾讯前复权全量K线 (开高低收量)"""
    sym = _tencent_symbol(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{max(days + 10, 100)},qfq")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    payload = resp.json()
    item = (payload.get("data") or {}).get(sym) or {}
    rows = item.get("qfqday") or item.get("day") or []
    if not rows:
        return None
    data = rows[-days:]
    return {
        "date": [str(r[0]) for r in data],
        "open": [float(r[1]) for r in data],
        "close": [float(r[2]) for r in data],
        "high": [float(r[3]) for r in data],
        "low": [float(r[4]) for r in data],
        "volume": [float(r[5]) if len(r) > 5 and r[5] else 0.0 for r in data],
    }


def fetch_realtime(code: str) -> Optional[dict]:
    """获取ETF实时行情 (新浪优先, 腾讯兜底)"""
    def sina():
        url = f"https://hq.sinajs.cn/list=sh{code}"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn"
        }, timeout=10)
        text = resp.text.strip()
        data = text.split('"')[1].split(",")
        return {
            "current": float(data[3]),
            "prev_close": float(data[2]),
            "open": float(data[1]),
            "high": float(data[4]),
            "low": float(data[5]),
            "volume": float(data[8]),
            "amount": float(data[9]),
        }

    return _try_sources([("sina", sina), ("tencent", lambda: fetch_realtime_tencent(code))])


def fetch_klines(code: str, days: int = 60) -> List[float]:
    """获取历史K线收盘价 (新浪优先, 腾讯前复权兜底)"""
    def sina():
        url = (f"https://quotes.sina.cn/cn/api/jsonp.php/"
               f"var%20_shrp_{code}=/CN_MarketDataService.getKLineData?"
               f"symbol=sh{code}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        matches = re.findall(r'"close":"([\d.]+)"', resp.text)
        return [float(m) for m in matches] if matches else []

    return _try_sources([("sina", sina), ("tencent", lambda: fetch_klines_tencent(code, days))]) or []


def fetch_klines_full(code: str, days: int = 60) -> Optional[Dict]:
    """获取全量 K 线（开高低收量, 新浪优先, 腾讯前复权兜底）"""
    def sina():
        url = (f"https://quotes.sina.cn/cn/api/jsonp.php/"
               f"var%20_shrp_{code}=/CN_MarketDataService.getKLineData?"
               f"symbol=sh{code}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        text = resp.text
        import json as _json
        # 提取JSON数组
        start = text.index("[")
        end = text.rindex("]") + 1
        raw = text[start:end]
        # 清理JSON
        raw = raw.replace("'", "\"")
        raw = re.sub(r"(\w+):", r'"\1":', raw)
        data = _json.loads(raw)
        return {
            "date": [d["date"] for d in data],
            "open": [float(d["open"]) for d in data],
            "close": [float(d["close"]) for d in data],
            "high": [float(d["high"]) for d in data],
            "low": [float(d["low"]) for d in data],
            "volume": [float(d["volume"]) for d in data],
        }

    return _try_sources([("sina", sina), ("tencent", lambda: fetch_klines_full_tencent(code, days))])


# ==================== 指数数据获取 ====================

def fetch_ashare_index(code: str, market: str) -> Optional[dict]:
    """获取A股指数行情（上证/创业板, 新浪优先, 腾讯兜底）"""
    def sina():
        url = f"https://hq.sinajs.cn/list={market}{code}"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn"
        }, timeout=10)
        text = resp.text.strip()
        data = text.split('"')[1].split(",")
        current = float(data[3])
        prev_close = float(data[2])
        change = current - prev_close
        change_pct = (current - prev_close) / prev_close * 100
        return {
            "current": current,
            "change": change,
            "change_pct": change_pct,
            "open": float(data[1]),
            "high": float(data[4]),
            "low": float(data[5]),
            "amount_yi": float(data[9]) / 1e8,
        }

    def tencent():
        # 腾讯指数符号: sh000001 / sz399006
        resp = requests.get(f"https://qt.gtimg.cn/q={market}{code}",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        if len(parts) < 38 or "none_match" in resp.text:
            return None
        current = float(parts[3])
        prev_close = float(parts[4])
        change = current - prev_close

        def _safe(idx, default):
            try:
                v = parts[idx].strip()
                return float(v) if v else default
            except (ValueError, IndexError):
                return default

        return {
            "current": current,
            "change": change,
            "change_pct": (change / prev_close * 100) if prev_close else 0.0,
            "open": _safe(5, current),
            "high": _safe(33, max(current, prev_close)),
            "low": _safe(34, min(current, prev_close)),
            "amount_yi": _safe(37, 0.0) / 1e8,
        }

    return _try_sources([("sina", sina), ("tencent", tencent)])


def fetch_us_index_from_sina(code: str) -> Optional[dict]:
    """获取美股指数行情（纳斯达克, 新浪优先, 腾讯兜底）"""
    def sina():
        symbol = code.replace("^", "").lower()
        url = f"https://hq.sinajs.cn/list=gb_{symbol}"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn"
        }, timeout=10)
        text = resp.text.strip()
        data = text.split('"')[1].split(",")
        current = float(data[1])
        change_pct = float(data[2])
        change = float(data[4])
        return {
            "current": current,
            "change": change,
            "change_pct": change_pct,
            "high": float(data[8]) if data[8] else current,
            "low": float(data[7]) if data[7] else current,
        }

    def tencent():
        # 腾讯美股指数符号: usIXIC
        symbol = code.replace("^", "").upper()
        resp = requests.get(f"https://qt.gtimg.cn/q=us{symbol}",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        if len(parts) < 35 or "none_match" in resp.text:
            return None
        current = float(parts[3])
        prev_close = float(parts[4])
        change = current - prev_close

        def _safe(idx, default):
            try:
                v = parts[idx].strip()
                return float(v) if v else default
            except (ValueError, IndexError):
                return default

        return {
            "current": current,
            "change": change,
            "change_pct": (change / prev_close * 100) if prev_close else 0.0,
            "high": _safe(33, current),
            "low": _safe(34, current),
        }

    return _try_sources([("sina", sina), ("tencent", tencent)])


# ==================== 溢价率获取（BOLL模式用） ====================
# QDII类ETF: 有外汇额度限制, 场内易出现高溢价(市价>>净值)
# 溢价率 = (市价/官方净值 - 1) × 100
# 溢价>8%时场内买入会白亏溢价, 应暂停或改投场外联接基金
QDII_CODES = {"513100", "513500", "513050", "159941", "513300",
              "513870", "159632", "159712", "159513", "513980", "159509"}
PREMIUM_HALT = 8.0  # 溢价暂停阈值(%)


def fetch_etf_nav(etf_code: str) -> Optional[Tuple[float, str]]:
    """从东财F10获取ETF官方净值, 返回(净值, 净值日期)"""
    try:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {"fundCode": etf_code, "pageIndex": 1, "pageSize": 3,
                  "startDate": "2026-01-01", "endDate": "2026-12-31"}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        rows = ((resp.json().get("Data") or {}).get("LSJZList") or [])
        if rows:
            return float(rows[0]["DWJZ"]), rows[0]["FSRQ"]
    except Exception:
        pass
    return None


def get_premium(etf_code: str, current: float) -> Optional[dict]:
    """
    计算溢价率(仅QDII)。返回 {"premium": %, "nav":, "nav_date":} 或 None。
    非QDII(黄金/红利低波)溢价≈0, 无参考价值, 返回None。
    """
    if etf_code not in QDII_CODES:
        return None
    nav_info = fetch_etf_nav(etf_code)
    if nav_info is None or nav_info[0] <= 0:
        return None
    nav, nav_date = nav_info
    premium = (current / nav - 1) * 100
    return {"premium": round(premium, 2), "nav": nav, "nav_date": nav_date}


def fetch_index_data() -> List[dict]:
    """获取所有指数行情"""
    results = []
    for idx in INDEX_CONFIG:
        if idx["market"] == "us":
            data = fetch_us_index_from_sina(idx["code"])
        else:
            data = fetch_ashare_index(idx["code"], idx["market"])
        if data:
            results.append({
                "name": idx["name"],
                **data,
            })
        else:
            results.append({
                "name": idx["name"],
                "error": "数据获取失败",
            })
    return results


# ==================== 指标计算 ====================

# ==================== 辅助函数 ====================

def _render_bar(value: float, max_len: int = 20, filled: str = "█", empty: str = "░") -> str:
    """渲染百分比条状图"""
    pos = max(0, min(max_len, int((value / 100) * max_len)))
    return f"[{filled * pos}{empty * (max_len - pos)}]"


def _find_rule(value: float, rules: list, key_min: str, key_max: str) -> dict:
    """在规则列表中查找匹配的规则"""
    for rule in rules:
        if rule[key_min] <= value < rule[key_max]:
            return rule
    return rules[len(rules) // 2]  # 默认返回中间规则


def calc_boll(closes: List[float], window: int = 20) -> Optional[dict]:
    """BOLL20 布林带计算"""
    if len(closes) < window:
        return None
    recent = closes[-window:]
    mid = sum(recent) / window
    std = (sum((x - mid) ** 2 for x in recent) / window) ** 0.5
    return {
        "upper": mid + 2 * std,
        "mid": mid,
        "lower": mid - 2 * std,
        "std": std,
        "bandwidth": 4 * std,
    }


def calc_rsi(closes: List[float], window: int = 14) -> Optional[float]:
    """RSI14 相对强弱指标"""
    if len(closes) < window + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(-window, 0)]
    gains = sum(d for d in deltas if d > 0)
    losses = sum(-d for d in deltas if d < 0)
    if losses == 0:
        return 100.0
    rs = (gains / window) / (losses / window)
    return round(100 - 100 / (1 + rs), 1)


def calc_rsi_level(rsi: float) -> dict:
    """根据RSI值查表，返回对应的定投规则"""
    return _find_rule(rsi, RSI_DCA_RULES, "rsi_min", "rsi_max")


def rsi_dca_plan(results: List[dict]) -> dict:
    """
    以RSI为核心分配定投金额

    返回:
    {
        "allocations": [
            {"desc": "黄金", "rsi": 39.3, "level": "偏低", "factor": 1.2,
             "base": 1000, "suggest": 1200, "action": "✅ 偏低，加码20%"},
        ],
        "avg_rsi": 48.9,
        "verdict": "➡️ 正常投入",
        "verdict_reason": "市场中性，按计划定投",
        "total_suggest": 5300,
        "has_veto": False,
        "veto_reason": "",
    }
    """
    allocations = []
    rsi_values = []
    veto_triggers = []

    for r in results:
        if "error" in r:
            continue
        rsi = r.get("rsi")
        if rsi is None:
            continue

        rsi_values.append(rsi)
        rule = calc_rsi_level(rsi)
        base = r["base_amount"]
        suggest = int(base * rule["factor"])

        # 检查单项否决
        if rsi > SINGLE_VETO["sell_ceiling"]:
            veto_triggers.append(f"{r['desc']}RSI={rsi}超过{SINGLE_VETO['sell_ceiling']}，极度过热")
        if rsi < SINGLE_VETO["buy_floor"]:
            veto_triggers.append(f"{r['desc']}RSI={rsi}低于{SINGLE_VETO['buy_floor']}，极度超卖机会")

        allocations.append({
            "desc": r["desc"],
            "code": r["code"],
            "rsi": rsi,
            "level": rule["level"],
            "factor": rule["factor"],
            "base": base,
            "suggest": suggest,
            "action": rule["action"],
            "change_pct": r["change_pct"],
            "price": r["current"],
            "boll_upper": round(r["boll"]["upper"], 3) if r.get("boll") else "-",
            "boll_mid": round(r["boll"]["mid"], 3) if r.get("boll") else "-",
            "boll_lower": round(r["boll"]["lower"], 3) if r.get("boll") else "-",
            "boll_position": round(r["boll_position"], 1) if r.get("boll") else "-",
        })

    # 整体判定
    avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else 50
    verdict = "➡️ 正常投入"
    verdict_reason = "数据不足，按计划定投"

    invest_rule = _find_rule(avg_rsi, INVEST_RULES, "avg_rsi_min", "avg_rsi_max")
    verdict = invest_rule["verdict"]
    verdict_reason = invest_rule["reason"]

    # 单项否决
    has_veto = len(veto_triggers) > 0
    if has_veto:
        # 如果任何ETF极度高估(>=78)，整体判定改为"不建议"
        high_overheats = [t for t in veto_triggers if "极度过热" in t]
        if high_overheats:
            verdict = "🔴 暂停投入"
            verdict_reason = f"单项否决： {'; '.join(high_overheats)}"

    total_suggest = sum(a["suggest"] for a in allocations)
    total_base = sum(a["base"] for a in allocations)

    return {
        "allocations": allocations,
        "avg_rsi": round(avg_rsi, 1),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "total_suggest": total_suggest,
        "total_base": total_base,
        "has_veto": has_veto,
        "veto_reason": "; ".join(veto_triggers) if veto_triggers else "",
    }


def calc_macd(closes: List[float]) -> Optional[dict]:
    """MACD 指标（12, 26, 9）"""
    if len(closes) < 26:
        return None

    def ema(data, period):
        """计算指数移动平均"""
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(ema12))]
    dea = ema(dif, 9)

    prev_dif = dif[-2] if len(dif) > 1 else dif[-1]
    prev_dea = dea[-2] if len(dea) > 1 else dea[-1]

    return {
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "macd": round(2 * (dif[-1] - dea[-1]), 4),
        "cross": "金叉" if prev_dif < prev_dea and dif[-1] > dea[-1] else
                 "死叉" if prev_dif > prev_dea and dif[-1] < dea[-1] else
                 "多头" if dif[-1] > dea[-1] else "空头",
        "trend": "上升" if dif[-1] > dif[-2] else "下降" if len(dif) > 1 else "持平",
    }


def calc_ma(closes: List[float], periods: List[int] = [5, 10, 20, 30, 60]) -> dict:
    """MA 均线系统"""
    result = {}
    for p in periods:
        if len(closes) >= p:
            result[f"MA{p}"] = round(sum(closes[-p:]) / p, 4)
        else:
            result[f"MA{p}"] = None
    return result


def calc_volume_ma(volumes: List[float], window: int = 5) -> Optional[float]:
    """成交量均线"""
    if len(volumes) >= window:
        return sum(volumes[-window:]) / window
    return None


def calc_boll_position(price: float, boll: dict) -> float:
    """计算价格在 BOLL 中的百分位(0-100)"""
    bw = boll["upper"] - boll["lower"]
    if bw <= 0:
        return 50.0
    return round((price - boll["lower"]) / bw * 100, 1)


# ==================== 信号生成 ====================

def signal_boll(price: float, boll: dict) -> Tuple[str, str, float]:
    """BOLL 信号"""
    if not boll:
        return "中性", "数据不足", 1.0
    if price > boll["upper"]:
        return "强烈卖出", "突破上轨，严重超买", 0.0
    elif price > boll["mid"] + (boll["upper"] - boll["mid"]) * 0.5:
        return "卖出", "靠近上轨，偏高", 0.5
    elif price > boll["mid"]:
        return "偏多", "中轨上方，正常偏强", 1.0
    elif price > boll["lower"] + (boll["mid"] - boll["lower"]) * 0.5:
        return "偏多", "中轨下方，偏低", 1.2
    elif price > boll["lower"]:
        return "买入", "靠近下轨，超卖", 1.5
    else:
        return "强烈买入", "跌破下轨，极端超卖", 2.0


def signal_rsi(rsi: Optional[float]) -> Tuple[str, str, float]:
    """RSI 信号"""
    if rsi is None:
        return "中性", "数据不足", 1.0
    if rsi > 75:
        return "强烈卖出", f"RSI={rsi} 严重超买", 0.0
    elif rsi > 60:
        return "卖出", f"RSI={rsi} 偏强，注意回调", 0.7
    elif rsi > 40:
        return "中性", f"RSI={rsi} 正常区间", 1.0
    elif rsi > 25:
        return "买入", f"RSI={rsi} 偏弱，机会区域", 1.3
    else:
        return "强烈买入", f"RSI={rsi} 极度超卖", 1.8


def signal_macd(macd: Optional[dict]) -> Tuple[str, str, float]:
    """MACD 信号"""
    if macd is None:
        return "中性", "数据不足", 1.0
    if macd["cross"] == "金叉" and macd["trend"] == "上升":
        return "强烈买入", f"MACD金叉+上行，DIF={macd['dif']}", 1.8
    elif macd["cross"] == "金叉":
        return "买入", f"MACD金叉，DIF={macd['dif']}", 1.4
    elif macd["cross"] == "多头" and macd["trend"] == "上升":
        return "偏多", f"MACD多头+上行，DIF={macd['dif']}", 1.1
    elif macd["cross"] == "多头":
        return "中性", f"MACD多头，DIF={macd['dif']}", 1.0
    elif macd["cross"] == "空头" and macd["trend"] == "下降":
        return "卖出", f"MACD空头+下行，DIF={macd['dif']}", 0.6
    elif macd["cross"] == "死叉":
        return "强烈卖出", f"MACD死叉，DIF={macd['dif']}", 0.2
    else:
        return "偏空", f"MACD空头，DIF={macd['dif']}", 0.8


def signal_ma(current: float, ma: dict) -> Tuple[str, str]:
    """均线排列信号"""
    if not ma.get("MA5") or not ma.get("MA10") or not ma.get("MA20"):
        return "中性", "数据不足"
    # 均线排列
    if ma["MA5"] > ma["MA10"] > ma["MA20"]:
        return "偏多", f"多头排列 MA5={ma['MA5']:.3f}>MA10={ma['MA10']:.3f}>MA20={ma['MA20']:.3f}"
    elif ma["MA5"] < ma["MA10"] < ma["MA20"]:
        return "偏空", f"空头排列 MA5={ma['MA5']:.3f}<MA10={ma['MA10']:.3f}<MA20={ma['MA20']:.3f}"
    else:
        return "中性", f"均线交错 MA5={ma['MA5']:.3f} MA10={ma['MA10']:.3f} MA20={ma['MA20']:.3f}"


def composite_signal(signals: List[Tuple[str, str, float]]) -> Tuple[str, str, float]:
    """综合信号（多指标共振）"""
    score_map = {
        "强烈买入": 5, "买入": 4, "偏多": 3,
        "中性": 2,
        "偏空": 1, "卖出": 0, "强烈卖出": -1,
    }
    scores = [score_map.get(s[0], 2) for s in signals]
    avg_score = sum(scores) / len(scores)

    # 平均因子(取倒数调整权重，极端信号强化)
    factors = [s[2] for s in signals]
    avg_factor = sum(factors) ** 0.8 / len(factors) ** 0.8  # 非线性因子，强化极端

    # 一致性检查（多空分歧度）
    strong_buy = sum(1 for s in scores if s >= 4)
    strong_sell = sum(1 for s in scores if s <= 0)
    divergence = min(strong_buy, strong_sell)

    if divergence >= 2:
        signal_name = "分歧"
        desc = f"多空分歧大({strong_buy}买 vs {strong_sell}卖)，建议观望"
    elif avg_score >= 4:
        signal_name = "强烈买入"
        desc = "多指标共振看多，强烈买入"
    elif avg_score >= 3.5:
        signal_name = "买入"
        desc = "多数指标偏多，适合买入"
    elif avg_score >= 2.5:
        signal_name = "偏多"
        desc = "中性偏多，正常操作"
    elif avg_score >= 1.5:
        signal_name = "中性"
        desc = "信号中性，按计划执行"
    elif avg_score >= 1:
        signal_name = "偏空"
        desc = "中性偏空，谨慎"
    elif avg_score >= 0:
        signal_name = "卖出"
        desc = "多数指标偏空，减仓"
    else:
        signal_name = "强烈卖出"
        desc = "多指标共振看空，离场"

    return signal_name, desc, round(avg_factor, 2)


# ==================== 风险评估 ====================

def risk_assessment(price: float, boll: dict, rsi: Optional[float],
                    macd: Optional[dict], ma: dict) -> List[str]:
    """风险评估"""
    warnings = []
    if boll and price > boll["upper"]:
        warnings.append("🔴 价格突破BOLL上轨，回调风险极高")
    if rsi and rsi > 80:
        warnings.append("🔴 RSI>80 严重超买，注意获利了结")
    if rsi and rsi < 20:
        warnings.append("🔵 RSI<20 严重超卖，可能反弹")

    if macd and macd["cross"] == "死叉":
        warnings.append("🟠 MACD死叉，趋势转弱")

    if boll and boll.get("bandwidth"):
        # 检查带宽：带宽收窄预示变盘
        pass  # 需要历史带宽百分位数据

    if not warnings:
        warnings.append("🟢 当前无显著风险信号")
    return warnings


# ==================== 定投计算 ====================

def calc_dca_amount(base_amount: float, signal_name: str,
                    composite_factor: float) -> Tuple[int, str]:
    """计算定投金额"""
    factor_map = {
        "强烈买入": 2.0,
        "买入": 1.5,
        "偏多": 1.2,
        "中性": 1.0,
        "分歧": 0.5,
        "偏空": 0.7,
        "卖出": 0.3,
        "强烈卖出": 0.0,
    }
    factor = factor_map.get(signal_name, 1.0)
    # 用平均因子微调
    final_factor = max(0, min(2.5, factor * (composite_factor / 1.0)))

    amount = int(base_amount * final_factor)
    reason = f"信号:{signal_name}(系数×{final_factor:.1f})"
    return amount, reason


# ==================== 网格计算 ====================

def calc_grid_levels(current: float, boll: dict, etf_name: str) -> List[dict]:
    """计算网格交易参考价位"""
    if not boll:
        return [{"level": "基准", "price": current}]

    grid = []
    upper = boll["upper"]
    mid = boll["mid"]
    lower = boll["lower"]
    step = (upper - lower) / 4

    for i in range(5):
        price = lower + step * i
        position = "上轨" if i == 4 else "中上" if i == 3 else "中轨" if i == 2 else "中下" if i == 1 else "下轨"
        action = "卖出" if i >= 3 else "持有" if i == 2 else "买入"
        grid.append({
            "level": position,
            "price": round(price, 3),
            "action": action,
        })

    return grid


# ==================== 报告输出 ====================

def analyze_etf(code: str, name: str, desc: str, base_alloc: float, full_name: str) -> dict:
    """分析单个 ETF"""
    rt = fetch_realtime(code)
    if not rt:
        return {"error": f"获取{name}数据失败", "code": code}

    closes = fetch_klines(code, 60)
    klines_full = fetch_klines_full(code, 60)

    if not closes or len(closes) < 20:
        return {"error": f"{name}历史数据不足", "code": code}

    current = rt["current"]
    change_pct = (current - rt["prev_close"]) / rt["prev_close"] * 100
    prev_close = rt["prev_close"]

    # 指标计算
    boll = calc_boll(closes)
    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    ma = calc_ma(closes)

    # 成交量分析
    volume_today = rt["volume"]
    volumes = klines_full["volume"] if klines_full else []
    vol_ma5 = calc_volume_ma(volumes) if volumes else None

    # 信号
    if boll:
        boll_sig_name, boll_sig_desc, boll_factor = signal_boll(current, boll)
        boll_position = calc_boll_position(current, boll)
    else:
        boll_sig_name = boll_sig_desc = "数据不足"
        boll_factor = 1.0
        boll_position = 50.0

    rsi_sig_name, rsi_sig_desc, rsi_factor = signal_rsi(rsi)
    macd_sig_name, macd_sig_desc, macd_factor = signal_macd(macd)
    ma_sig_name, ma_sig_desc = signal_ma(current, ma)

    sig_name, sig_desc, comp_factor = composite_signal([
        (boll_sig_name, boll_sig_desc, boll_factor),
        (rsi_sig_name, rsi_sig_desc, rsi_factor),
        (macd_sig_name, macd_sig_desc, macd_factor),
        (ma_sig_name, ma_sig_desc, 1.0),
    ])

    # 风险
    warnings = risk_assessment(current, boll, rsi, macd, ma)

    # 定投
    base_amount = MONTHLY_AMOUNT * base_alloc
    dca_amount, dca_reason = calc_dca_amount(base_amount, sig_name, comp_factor)

    # 网格
    grid = calc_grid_levels(current, boll, desc)

    # MACD走势图(EOD using ASCII)
    macd_bars = ""
    if macd:
        macd_val = macd["macd"]
        bars = "█" * max(1, int(abs(macd_val) * 100))
        macd_bars = f"{'🟩' if macd_val >= 0 else '🔴'}{bars} {macd_val:.4f}"

    # 两类型Boll位置指示
    boll_visual = ""
    if boll:
        pos = calc_boll_position(current, boll)
        bw = boll["upper"] - boll["lower"]
        rel = (current - boll["lower"]) / bw if bw > 0 else 0.5
        bar_len = 20
        pos_bar = int(rel * bar_len)
        boll_visual = f"[{'=' * pos_bar}{'●'}{'.' * (bar_len - pos_bar - 1)}] {pos:.1f}%"
        marker = "↑超买" if pos >= 80 else "↓超卖" if pos <= 20 else "─中轨"
        boll_visual += f" {marker}"

    status_emoji = "🟢" if sig_name in ("强烈买入", "买入", "偏多") else \
                   "🟡" if sig_name in ("中性", "分歧") else "🔴"

    volume_desc = ""
    if vol_ma5 and volume_today:
        vol_ratio = volume_today / vol_ma5
        if vol_ratio > 2:
            volume_desc = f"🔥放量{vol_ratio:.1f}倍"
        elif vol_ratio > 1.3:
            volume_desc = f"📊量增{vol_ratio:.1f}倍"
        elif vol_ratio < 0.5:
            volume_desc = f"💤缩量{vol_ratio:.1f}倍"
        else:
            volume_desc = f"📊正常{vol_ratio:.1f}倍"

    return {
        "code": code,
        "name": name,
        "desc": desc,
        "full_name": full_name,
        "base_alloc": base_alloc,
        "current": current,
        "prev_close": prev_close,
        "change_pct": round(change_pct, 2),
        "high": rt["high"],
        "low": rt["low"],
        "open": rt["open"],
        "volume": volume_today,
        "amount": rt["amount"],
        "boll": boll,
        "boll_position": boll_position,
        "boll_visual": boll_visual,
        "rsi": rsi,
        "macd": macd,
        "macd_bars": macd_bars,
        "ma": ma,
        "vol_ma5": vol_ma5,
        "volume_desc": volume_desc,
        "signals": {
            "boll": (boll_sig_name, boll_sig_desc),
            "rsi": (rsi_sig_name, rsi_sig_desc),
            "macd": (macd_sig_name, macd_sig_desc),
            "ma": (ma_sig_name, ma_sig_desc),
        },
        "composite_signal": (sig_name, sig_desc, comp_factor),
        "warnings": warnings,
        "dca_amount": dca_amount,
        "dca_reason": dca_reason,
        "base_amount": int(base_amount),
        "grid": grid,
        "status_emoji": status_emoji,
    }


def format_index_header(index_data: List[dict]) -> str:
    """输出大盘指数行情头（上证、创业板、纳斯达克）"""
    lines = []
    for idx in index_data:
        if "error" in idx:
            continue
        arrow = "📈" if idx["change_pct"] >= 0 else "📉"
        sign = "+" if idx["change_pct"] >= 0 else ""
        lines.append(f"{arrow} {idx['name']}: {idx['current']:.2f}  {sign}{idx['change_pct']:.2f}%")
    return "\n\n".join(lines)


def format_full_report(results: List[dict], index_data: List[dict] = None) -> str:
    """输出完整报告"""
    lines = []
    now = datetime.now()
    lines.append("=" * 60)
    lines.append(f"📊 A股ETF 量化投资策略报告")
    lines.append(f"📅 {now.strftime('%Y-%m-%d %H:%M')} (交易日)")
    lines.append("=" * 60)
    lines.append("")
    # 大盘指数
    if index_data:
        lines.append(format_index_header(index_data))
        lines.append("")
        lines.append("─" * 60)
        lines.append("")

    total_dca = 0
    buy_signals = 0

    for r in results:
        if "error" in r:
            lines.append(f"❌ {r['code']}: {r['error']}")
            lines.append("")
            continue

        buy_signals += 1 if "买入" in r["composite_signal"][0] else 0

        # === 标题行 ===
        arrow = "📈" if r["change_pct"] >= 0 else "📉"
        sign = "+" if r["change_pct"] >= 0 else ""
        lines.append(f"{r['status_emoji']} {r['desc']}ETF ({r['code']})  {arrow}¥{r['current']:.3f}  {sign}{r['change_pct']:.2f}%")
        lines.append(f"   🏢 {r['full_name']}")

        # === 价格数据 ===
        lines.append(f"   📊 高:{r['high']:.3f}  低:{r['low']:.3f}  开:{r['open']:.3f}  昨收:{r['prev_close']:.3f}")
        amount_yi = r["amount"] / 1e8
        vol_wan = r["volume"] / 10000
        lines.append(f"   💹 成交:{vol_wan:.0f}万手  金额:{amount_yi:.2f}亿  {r['volume_desc']}")

        lines.append("")

        # === 技术指标 ===
        lines.append(f"   📐 [技术指标]")
        if r["boll"]:
            b = r["boll"]
            lines.append(f"   📍 BOLL20：上轨{b['upper']:.3f} | 中轨{b['mid']:.3f} | 下轨{b['lower']:.3f}")
            lines.append(f"            {r['boll_visual']}")
        rsi_val = "数据不足" if r["rsi"] is None else str(r["rsi"])
        lines.append(f"   📍 RSI14:   {rsi_val}")
        if r["macd"]:
            m = r["macd"]
            lines.append(f"   📍 MACD:    {r['macd_bars']}  {m['cross']}/{m['trend']}")
        if r["ma"]:
            ma_str = "  ".join(f"MA{k}={v:.3f}" for k, v in sorted(r["ma"].items()) if v)
            lines.append(f"   📍 均线:    {ma_str}")

        lines.append("")

        # === 信号详情 ===
        lines.append(f"   🔔 [多指标信号]")
        sigs = r["signals"]
        sig_emoji = {"强烈买入": "🟢🟢", "买入": "🟢", "偏多": "🟩",
                     "中性": "⚪", "分歧": "🟡",
                     "偏空": "🟧", "卖出": "🔴", "强烈卖出": "🔴🔴"}
        for s_type, (s_name, s_desc) in sigs.items():
            emoji = sig_emoji.get(s_name, "⚪")
            lines.append(f"       {s_type:6s}: {emoji} {s_name:<6s} | {s_desc}")

        # 综合信号
        comp_name, comp_desc, comp_factor = r["composite_signal"]
        comp_emoji = sig_emoji.get(comp_name, "⚪")
        lines.append(f"       {'─' * 30}")
        lines.append(f"       {'综合':6s}: {comp_emoji} {comp_name:<6s} | {comp_desc} (系数×{comp_factor:.2f})")

        lines.append("")

        # === 风险提示 ===
        lines.append(f"   ⚠️ [风险评估]")
        for w in r["warnings"]:
            lines.append(f"       {w}")

        lines.append("")

        # === 定投建议 ===
        lines.append(f"   💰 [定投建议]")
        lines.append(f"       基础: ¥{r['base_amount']}  →  建议: ¥{r['dca_amount']}  ({r['dca_reason']})")

        total_dca += r["dca_amount"]

        # === 网格参考 ===
        lines.append(f"      ")
        lines.append(f"   🔲 [网格交易参考]")
        for g in r["grid"]:
            action_emoji = "🔴卖" if g["action"] == "卖出" else "🟢买" if g["action"] == "买入" else "⚪持"
            lines.append(f"       {action_emoji} {g['level']:4s}  ¥{g['price']:.3f}")
        lines.append("")
        lines.append("─" * 60)
        lines.append("")

    # === 汇总 ===
    lines.append("=" * 60)
    lines.append("💰 本月定投方案")
    lines.append("=" * 60)

    total_base = MONTHLY_AMOUNT
    for r in results:
        if "error" in r:
            continue
        pct = r["dca_amount"] / MONTHLY_AMOUNT * 100
        lines.append(f"   {r['desc']:6s}: ¥{r['dca_amount']:5d}  ({pct:.0f}%)  ← 基{chr(0x00A5)}{r['base_amount']}")
    lines.append(f"   {'合计':6s}: ¥{total_dca:5d}  ({total_dca / MONTHLY_AMOUNT * 100:.0f}%)")

    # 超出或节省
    diff = total_dca - total_base
    if diff > 0:
        lines.append(f"   ⚠️ 超预算¥{diff}，从预留现金补足")
    elif diff < 0:
        lines.append(f"   ✅ 节省¥{-diff}，转入货币基金")

    # 整体策略
    buy_count = buy_signals
    if buy_count >= 3:
        strategy = "🔵 三标的同步看多，积极加仓！"
    elif buy_count >= 2:
        strategy = "🟢 多数标的低估，加码定投"
    elif buy_count >= 1:
        strategy = "🟡 部分标的偏低，结构性加仓"
    else:
        strategy = "⚪ 暂无强烈买入信号，按计划执行"

    lines.append("")
    lines.append(f"📌 整体策略： {strategy}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("💡 BOLL20 | RSI14 | MACD（12, 26, 9） | MA 均线系统 | 成交量")
    lines.append("⚠️ 本报告仅供参考，不构成投资建议。投资有风险，入市需谨慎。")

    return "\n\n".join(lines)


def format_signal_only(results: List[dict]) -> str:
    """仅输出信号摘要"""
    lines = [f"📊 {datetime.now().strftime('%m/%d %H:%M')} ETF量化信号"]
    for r in results:
        if "error" in r:
            continue
        comp_name = r["composite_signal"][0]
        emoji = r["status_emoji"]
        lines.append(f"{emoji} {r['desc']}: {comp_name} 现价¥{r['current']:.3f} "
                     f"({r['change_pct']:+.2f}%) 建议¥{r['dca_amount']}")
    return "\n\n".join(lines)


def format_daily_action(results: List[dict]) -> str:
    """每日操作建议"""
    lines = [f"📋 {datetime.now().strftime('%m/%d')} ETF操作清单"]
    for r in results:
        if "error" in r:
            continue
        comp_name = r["composite_signal"][0]
        if "买入" in comp_name:
            action = "✅ 可以加仓"
        elif "卖出" in comp_name:
            action = "⏸️ 暂停或减仓"
        else:
            action = "➡️ 按计划执行"
        lines.append(f"  {r['desc']}({r['code']}): {action} | {comp_name}")
    return "\n\n".join(lines)


def format_rsi_plan(results: List[dict], index_data: List[dict] = None) -> str:
    """
    RSI 定投方案输出（定时任务专用）
    以 RSI 为唯一核心依据，输出简洁的定投分配方案
    """
    plan = rsi_dca_plan(results)
    now = datetime.now()

    lines = []
    lines.append(f"📊 RSI定投方案 | {now.strftime('%m/%d %H:%M')}")
    lines.append("")
    # 大盘指数
    if index_data:
        lines.append(format_index_header(index_data))
        lines.append("")
        lines.append("─" * 40)
        lines.append("")

    # 各ETF RSI明细
    for a in plan["allocations"]:
        # RSI条状图可视化
        rsi_bar = _render_bar(a["rsi"])

        # 涨跌幅箭头
        arrow = "📈" if a["change_pct"] >= 0 else "📉"
        sign = "+" if a["change_pct"] >= 0 else ""

        lines.append(f"{a['desc']}ETF ({a['code']})")
        lines.append(f"  {arrow} ¥{a['price']:.3f} ({sign}{a['change_pct']:.2f}%)")
        lines.append(f"  📐 RSI(14): {a['rsi']}")
        lines.append(f"     {rsi_bar}")
        lines.append(f"     LV：{a['level']}  | 系数 ×{a['factor']}")
        lines.append(f"     {a['action']}")
        # BOLL20
        lines.append(f"  📍 BOLL20：上轨{a['boll_upper']}  中轨{a['boll_mid']}  下轨{a['boll_lower']}")
        # BOLL位置条状图
        boll_pos = a["boll_position"]
        if isinstance(boll_pos, (int, float)):
            boll_bar_len = 20
            boll_pos_int = max(0, min(boll_bar_len, int((boll_pos / 100) * boll_bar_len)))
            boll_bar = f"[{'=' * boll_pos_int}{'●'}{'.' * (boll_bar_len - boll_pos_int - 1)}]"
            lines.append(f"     {boll_bar} {boll_pos}%")
        lines.append(f"  💰 基¥{a['base']:>5d} → 投¥{a['suggest']:>5d}")
        lines.append("")

    # 整体建议
    lines.append("─" * 40)
    avg = plan["avg_rsi"]
    # 平均RSI条状图
    avg_bar = _render_bar(avg)
    lines.append(f"📈 组合平均 RSI： {plan['avg_rsi']}")
    lines.append(f"   {avg_bar}")
    lines.append(f"")
    lines.append(f"💰 {plan['verdict']}")
    lines.append(f"   {plan['verdict_reason']}")

    # 金额汇总
    diff = plan["total_suggest"] - plan["total_base"]
    lines.append(f"")
    lines.append(f"📋 月度定投分配方案（基¥{plan['total_base']}）")
    for a in plan["allocations"]:
        pct = a["suggest"] / MONTHLY_AMOUNT * 100
        lines.append(f"   {a['desc']:6s}: ¥{a['suggest']:5d} ({pct:.0f}%)")
    lines.append(f"   {'合计':6s}: ¥{plan['total_suggest']:5d} "
                 f"({'超¥'+str(diff) if diff>0 else '省¥'+str(-diff) if diff<0 else '正好'})")

    if plan["has_veto"]:
        lines.append(f"")
        lines.append(f"⚠️ 单项否决： {plan['veto_reason']}")

    lines.append("")
    lines.append("💡 RSI < 30 加码 | RSI 30-45 正常 | RSI > 55 减码 | RSI > 75 暂停")

    return "\n\n".join(lines)


def format_boll_plan(results: List[dict], index_data: List[dict] = None) -> str:
    """
    BOLL20+溢价 定投方案输出（定时任务专用, 12.6年回测验证）
    第1层 溢价闸门(仅QDII): 溢价>8% 暂停场内买入(改投场外联接基金)
    第2层 BOLL20中轨: 价格<MA20 加倍(2x) / >MA20 减半(0.5x) / 贴均线 正常(1x)
    验证结论: IRR 22%+ vs 等额定投 20%, 资金效率+26%
    """
    now = datetime.now()

    lines = []
    lines.append(f"📊 BOLL20+溢价定投方案 | {now.strftime('%m/%d %H:%M')}")
    lines.append("")
    if index_data:
        lines.append(format_index_header(index_data))
        lines.append("")
        lines.append("─" * 40)
        lines.append("")

    allocations = []
    veto_triggers = []

    for r in results:
        if "error" in r:
            lines.append(f"❌ {r['code']}: {r['error']}")
            lines.append("")
            continue

        code = r["code"]
        desc = r["desc"]
        current = r["current"]
        change_pct = r["change_pct"]
        boll = r["boll"]
        ma20 = boll["mid"] if boll else None

        # 第1层: 溢价闸门(仅QDII)
        prem_info = get_premium(code, current)
        premium = prem_info["premium"] if prem_info else None
        halted = premium is not None and premium > PREMIUM_HALT

        # 第2层: BOLL20中轨
        if halted:
            factor = 0.0
            action = f"🚫 暂停场内买入 → 溢价 {premium:+.2f}% > {PREMIUM_HALT}%"
            veto_triggers.append(f"{desc}溢价{premium:+.1f}%超{PREMIUM_HALT}%，场内买贵")
        elif ma20:
            dev = (current - ma20) / ma20 * 100
            if dev < -0.5:
                factor = 2.0
                action = "🟢🟢 加倍买入 (2倍) — 价格低于MA20, 逆势加仓"
            elif dev > 0.5:
                factor = 0.5
                action = "🟡 减半买入 (0.5倍) — 价格高于MA20, 高位少买"
            else:
                factor = 1.0
                action = "🟢 正常买入 (1倍) — 价格贴近MA20"
        else:
            factor = 1.0
            action = "➡️ 按计划 (数据不足)"

        base = int(MONTHLY_AMOUNT * r["base_alloc"] if "base_alloc" in r else MONTHLY_AMOUNT * 0.33)
        suggest = int(base * factor)

        arrow = "📈" if change_pct >= 0 else "📉"
        sign = "+" if change_pct >= 0 else ""

        lines.append(f"{desc}ETF ({code})")
        lines.append(f"  {arrow} ¥{current:.3f} ({sign}{change_pct:.2f}%)")
        if premium is not None:
            lines.append(f"  💱 溢价率: {premium:+.2f}% (净值{prem_info['nav']:.4f}@{prem_info['nav_date']})")
        if ma20:
            dev = (current - ma20) / ma20 * 100
            lines.append(f"  📍 MA20: {ma20:.3f} (偏离{dev:+.1f}%)")
            boll_pos = r.get("boll_position")
            if isinstance(boll_pos, (int, float)):
                boll_bar_len = 20
                boll_pos_int = max(0, min(boll_bar_len, int((boll_pos / 100) * boll_bar_len)))
                boll_bar = f"[{'=' * boll_pos_int}{'●'}{'.' * (boll_bar_len - boll_pos_int - 1)}]"
                lines.append(f"     {boll_bar} {boll_pos}%")
        lines.append(f"  💰 基¥{base:>5d} → 投¥{suggest:>5d}  ({action})")
        lines.append("")

        allocations.append({"desc": desc, "base": base, "suggest": suggest})

    # 整体判定
    lines.append("─" * 40)
    total_base = sum(a["base"] for a in allocations)
    total_suggest = sum(a["suggest"] for a in allocations)

    # 暂停数占比判定
    halted_count = len([a for a in allocations if a["suggest"] == 0])
    if halted_count == len(allocations) and halted_count > 0:
        verdict = "🔴 全部暂停投入"
        reason = "所有标的溢价超限或高估, 场内暂停, 资金转场外/货基"
    elif halted_count > 0:
        verdict = "🟡 部分暂停"
        reason = f"{halted_count}只溢价超限暂停, 其余按BOLL20执行"
    elif total_suggest > total_base:
        verdict = "🟢 建议投入"
        reason = "多数标的在MA20下方, 处于可加仓区域"
    elif total_suggest < total_base:
        verdict = "🟠 谨慎投入"
        reason = "多数标的在MA20上方, 高位少买"
    else:
        verdict = "➡️ 正常投入"
        reason = "信号中性, 按计划定投"

    lines.append(f"💰 {verdict}")
    lines.append(f"   {reason}")

    diff = total_suggest - total_base
    lines.append("")
    lines.append(f"📋 月度定投分配方案（基¥{total_base}）")
    for a in allocations:
        pct = a["suggest"] / MONTHLY_AMOUNT * 100
        lines.append(f"   {a['desc']:6s}: ¥{a['suggest']:5d} ({pct:.0f}%)")
    lines.append(f"   {'合计':6s}: ¥{total_suggest:5d} "
                 f"({'超¥'+str(diff) if diff>0 else '省¥'+str(-diff) if diff<0 else '正好'})")

    if veto_triggers:
        lines.append("")
        lines.append("⚠️ 暂停原因： " + "; ".join(veto_triggers))

    lines.append("")
    lines.append("💡 溢价>8%暂停(改场外) | 价格<MA20加倍 | >MA20减半")
    lines.append("⚠️ 本方案基于12.6年回测验证, 仅供参考, 不构成投资建议")

    return "\n\n".join(lines)


# ==================== 主入口 ====================

def main():
    """主入口函数，解析命令行参数并执行对应模式"""
    # 解析参数
    mode = "full"  # full, signal-only, daily, monthly, rsi, boll
    if "--signal-only" in sys.argv:
        mode = "signal"
    elif "--daily" in sys.argv:
        mode = "daily"
    elif "--monthly" in sys.argv:
        mode = "monthly"
    elif "--rsi" in sys.argv:
        mode = "rsi"
    elif "--boll" in sys.argv:
        mode = "boll"

    results = []
    for etf in ETF_CONFIG:
        result = analyze_etf(**etf)
        results.append(result)

    # 获取大盘指数数据
    index_data = fetch_index_data()

    if mode == "signal":
        output = format_signal_only(results)
    elif mode == "daily":
        output = format_daily_action(results)
    elif mode == "rsi":
        output = format_rsi_plan(results, index_data)
    elif mode == "boll":
        output = format_boll_plan(results, index_data)
    else:
        output = format_full_report(results, index_data)

    print(output)


if __name__ == "__main__":
    main()