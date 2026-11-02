# -*- coding: utf-8 -*-
"""ETF 定投日报 HTML 渲染器 (v3.5.3)

把 final_strategy.py 的终端文本解析成结构化仪表盘:
  - 顶部 KPI 卡片 (指数行情)
  - 分节卡片 (执行区/参考区/规则区)
  - ASCII 表格 → 带样式的 HTML 表格 (位置/涨跌/状态/选中标记着色)
解析失败的行原样落入 <pre>, 保证信息零丢失。
"""
import html as _html
import re

# 各 section 的起始图标 (与 final_strategy._hdr 一致)
_HEADER_ICONS = ['📊', '💰', '🏦', '🔄', '🔁', '📌', '📐', '💡']
_INDEX_RE = re.compile(r"^(📈|📉)?\s*(上证指数|创业板指|纳斯达克):\s*([\d.]+)\s*([+\-][\d.]+%)")
_CELL_SPLIT = re.compile(r"\s{2,}")
# 表格行前缀 (数据行以 2-3 空格缩进)
_NOTE_PREFIX = ("注:", "规则:", "标记:", "方向:", "策略:", "验证:", "本月执行:", "⚡", "摘要:")


def _esc(s):
    return _html.escape(s, quote=False)


def _is_rule(line):
    """分隔线: 去掉所有空白后只剩 ─ 或 ="""
    s = "".join(line.split())
    return len(s) >= 6 and (set(s) <= set("─") or set(s) <= set("="))


def _split_row(line):
    return [c.strip() for c in _CELL_SPLIT.split(line.strip()) if c.strip() != ""]


def _pos_chip(cell):
    if "🔥" in cell:
        cls = "fire"
    elif "🟢" in cell:
        cls = "green"
    elif "🟡" in cell:
        cls = "amber"
    elif "➡️" in cell:
        cls = "gray"
    else:
        return _esc(cell)
    return f'<span class="chip {cls}">{_esc(cell)}</span>'


def _chg_span(cell):
    """涨跌列: A股习惯 红涨绿跌"""
    if cell.startswith("📈"):
        return f'<span class="up">{_esc(cell)}</span>'
    if cell.startswith("📉"):
        return f'<span class="down">{_esc(cell)}</span>'
    return _esc(cell)


def _status_chip(cell):
    if "⚠️" in cell:
        return f'<span class="chip warn">{_esc(cell)}</span>'
    if "🔴" in cell:
        return f'<span class="chip amber">{_esc(cell)}</span>'
    if "🟢" in cell:
        return f'<span class="chip green">{_esc(cell)}</span>'
    return _esc(cell)


def _styled_cell(header, cell):
    if "今日" in header or "涨跌" in header:
        return _chg_span(cell)
    if header in ("位置", "档位", "倍数"):
        return _pos_chip(cell)
    if header == "状态":
        return _status_chip(cell)
    if header == "分值":
        return f'<b class="score">{_esc(cell)}</b>'
    if "金额" in header:
        return f'<b class="amt">{_esc(cell)}</b>'
    if header == "选中":
        if "⭐" in cell:
            return '<span class="chip cycle">⭐周期</span>'
        if "◆" in cell:
            return '<span class="chip basic">◆基本</span>'
        return ""
    if header == "池内/候选":
        return _status_chip(cell)
    return _esc(cell)


def _render_table(header_cells, rows):
    th = "".join(f"<th>{_esc(h)}</th>" for h in header_cells)
    body = []
    for r in rows:
        tds = []
        for i in range(len(header_cells)):
            cell = r[i] if i < len(r) else ""
            tds.append(f"<td>{_styled_cell(header_cells[i], cell)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _is_header_line(stripped):
    """判断是否 section 标题行 (以已知图标开头)"""
    return bool(stripped) and any(stripped.startswith(ic) for ic in _HEADER_ICONS)


def _parse(text):
    lines = text.split("\n")
    indices = []
    sections = []
    cur = None
    i = 0
    n = len(lines)

    def ensure_section():
        nonlocal cur
        if cur is None:
            cur = ("📊", "", [])
            sections.append(cur)
        return cur

    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _INDEX_RE.match(stripped)
        if m:
            arrow, name, px, chg = m.groups()
            indices.append((arrow, name, px, chg))
            i += 1
            continue
        if _is_rule(line):
            i += 1
            continue
        if _is_header_line(stripped):
            for ic in _HEADER_ICONS:
                if stripped.startswith(ic):
                    title = stripped[len(ic):].strip()
                    break
            cur = (ic, title, [])
            sections.append(cur)
            i += 1
            continue
        # 表格识别: 当前行有多个单元格, 且下一行是 ─ 分隔线
        cells = _split_row(stripped)
        if len(cells) >= 2 and i + 1 < n and _is_rule(lines[i + 1]):
            hdr = cells
            rows = []
            j = i + 2
            while j < n:
                raw = lines[j]
                s2 = raw.strip()
                if not s2:
                    break
                if _is_rule(raw) or _is_header_line(s2) or s2.startswith(_NOTE_PREFIX):
                    break
                # 数据行: 缩进且不含 section 特征
                if raw.startswith("  "):
                    rows.append(_split_row(raw))
                    j += 1
                else:
                    break
            ensure_section()[2].append(("table", hdr, rows))
            i = j
            continue
        # 普通说明行
        if stripped.startswith(_NOTE_PREFIX):
            ensure_section()[2].append(("note", stripped))
        else:
            # 连续 pre 行合并 (避免碎片化)
            items = ensure_section()[2]
            if items and items[-1][0] == "pre":
                items[-1] = ("pre", items[-1][1] + chr(10) + stripped)
            else:
                items.append(("pre", stripped))
        i += 1
    return indices, sections


_CSS = """
:root { --bg:#f4f6fa; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
        --red:#dc2626; --green:#059669; --amber:#d97706; --blue:#2563eb; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif; }
.wrap { max-width:1100px; margin:0 auto; padding:28px 20px 60px; }
.hero { background:linear-gradient(135deg,#1e3a8a,#2563eb); color:#fff; border-radius:16px;
        padding:22px 26px; margin-bottom:18px; }
.hero h1 { margin:0 0 4px; font-size:21px; }
.hero .sub { opacity:.85; font-size:13px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:22px; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; }
.kpi .k { font-size:12px; color:var(--muted); }
.kpi .v { font-size:19px; font-weight:700; margin-top:2px; }
.kpi .d { font-size:12px; margin-top:2px; }
.up { color:var(--red); } .down { color:var(--green); }
section { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:18px 20px; margin-bottom:16px; }
section h2 { margin:0 0 12px; font-size:16px; border-left:4px solid var(--blue); padding-left:10px; }
.tw { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th { text-align:left; color:var(--muted); font-weight:600; padding:7px 10px; border-bottom:2px solid var(--line);
     white-space:nowrap; position:sticky; top:0; background:#fff; }
td { padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
tbody tr:hover { background:#f0f5ff; }
.score { font-size:14px; }
.amt { color:var(--blue); }
.chip { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; }
.chip.fire { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.chip.green { background:#ecfdf5; color:#047857; border:1px solid #a7f3d0; }
.chip.amber { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.chip.gray { background:#f3f4f6; color:#4b5563; border:1px solid #e5e7eb; }
.chip.warn { background:#fff7ed; color:#c2410c; border:1px solid #fed7aa; font-weight:600; }
.chip.cycle { background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; }
.chip.basic { background:#f5f3ff; color:#6d28d9; border:1px solid #ddd6fe; }
.note { font-size:12.5px; color:var(--muted); background:#f8fafc; border-left:3px solid #cbd5e1;
        padding:7px 12px; border-radius:0 8px 8px 0; margin:8px 0; white-space:pre-wrap; }
pre { font-size:12.5px; color:#374151; background:#f8fafc; padding:10px 12px; border-radius:8px;
      overflow-x:auto; margin:8px 0; white-space:pre-wrap; }
.foot { text-align:center; color:var(--muted); font-size:12px; margin-top:24px; }
"""


def render_html(text, date_str, extra_meta=""):
    indices, sections = _parse(text)
    out = [f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
           f'<meta name="viewport" content="width=device-width, initial-scale=1.0">'
           f'<title>ETF 定投策略日报 | {date_str}</title><style>{_CSS}</style></head><body><div class="wrap">']
    out.append(f'<div class="hero"><h1>📊 ETF 定投策略日报 · {date_str}</h1>'
               f'<div class="sub">基本配置(纯定投60%) + 周期卫星(分档40%) | v3.5 {extra_meta}</div></div>')
    if indices:
        cards = []
        for arrow, name, px, chg in indices:
            cls = "up" if arrow == "📈" else "down"
            cards.append(f'<div class="kpi"><div class="k">{_esc(name)}</div>'
                         f'<div class="v">{_esc(px)}</div><div class="d {cls}">{_esc(chg)}</div></div>')
        out.append(f'<div class="kpis">{"".join(cards)}</div>')
    for icon, title, items in sections:
        if not title and not items:
            continue
        if not title:
            # 无标题 section (如"当前定投池"行) → 并入 hero 下方提示条
            for it in items:
                txt = it[1] if it[0] == "note" else it[1]
                out.append(f'<div class="note">{_esc(txt)}</div>')
            continue
        # 跳过与 hero 重复的首个日报标题 section
        if title.startswith("ETF 定投策略日报"):
            # 其内容(若有)并入
            for it in items:
                if it[0] == "pre":
                    out.append(f'<pre>{_esc(it[1])}</pre>')
            continue
        out.append(f'<section><h2>{icon} {_esc(title)}</h2>')
        for it in items:
            if it[0] == "table":
                out.append(_render_table(it[1], it[2]))
            elif it[0] == "note":
                out.append(f'<div class="note">{_esc(it[1])}</div>')
            else:
                out.append(f'<pre>{_esc(it[1])}</pre>')
        out.append("</section>")
    out.append(f'<div class="foot">生成于 {_esc(date_str)} · 仅供研究参考, 不构成投资建议</div></div></body></html>')
    return "".join(out)
