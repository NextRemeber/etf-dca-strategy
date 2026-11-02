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
:root {
  --bg:#0f1219; --bg2:#151a26; --card:#1a2030; --card2:#202840;
  --ink:#e6eaf2; --muted:#8b95ab; --line:#2a3247;
  --red:#ff6b6b; --green:#3ddc97; --amber:#ffc857; --blue:#5eaaff; --purple:#a78bfa;
}
* { box-sizing:border-box; }
html { color-scheme: dark; }
body { margin:0; background:
       radial-gradient(1200px 600px at 80% -100px, rgba(94,170,255,.08), transparent),
       var(--bg);
       color:var(--ink);
       font-family:-apple-system,"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
       font-size:14px; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
       font-variant-numeric:tabular-nums; }
.wrap { max-width:1100px; margin:0 auto; padding:28px 20px 60px; }
.hero { background:linear-gradient(135deg,#16204a 0%,#1d3a7a 55%,#26418c 100%);
        border:1px solid #2e4a8f; border-radius:16px; padding:22px 26px; margin-bottom:18px;
        box-shadow:0 8px 32px rgba(29,58,122,.35); }
.hero h1 { margin:0 0 4px; font-size:21px; font-weight:700; letter-spacing:.5px; color:#f0f5ff; }
.hero .sub { opacity:.75; font-size:12.5px; letter-spacing:.3px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:22px; }
.kpi { background:linear-gradient(180deg,var(--card2),var(--card)); border:1px solid var(--line);
       border-radius:12px; padding:12px 16px; }
.kpi .k { font-size:12px; color:var(--muted); letter-spacing:.5px; }
.kpi .v { font-size:20px; font-weight:700; margin-top:2px;
          font-family:"JetBrains Mono","Cascadia Code",Consolas,monospace; }
.kpi .d { font-size:12px; margin-top:2px; font-family:Consolas,monospace; }
.up { color:var(--red); } .down { color:var(--green); }
section { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:18px 20px; margin-bottom:16px; box-shadow:0 2px 12px rgba(0,0,0,.25); }
section h2 { margin:0 0 12px; font-size:15.5px; font-weight:700; letter-spacing:.5px;
             border-left:4px solid var(--blue); padding-left:10px; color:#dbe4f5; }
.tw { overflow-x:auto; border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th { text-align:left; color:var(--muted); font-weight:600; padding:8px 10px;
     border-bottom:2px solid var(--line); white-space:nowrap; position:sticky; top:0;
     background:var(--card2); letter-spacing:.5px; }
td { padding:7px 10px; border-bottom:1px solid #222a3d; white-space:nowrap; }
tbody tr:hover { background:#232c44; }
.score { font-size:14px; font-family:Consolas,monospace; color:#fff; }
.amt { color:var(--blue); font-family:Consolas,monospace; font-weight:700; }
.chip { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px;
        letter-spacing:.3px; }
.chip.fire { background:rgba(255,107,107,.14); color:#ff8f8f; border:1px solid rgba(255,107,107,.4); font-weight:600; }
.chip.green { background:rgba(61,220,151,.12); color:#5ee6a8; border:1px solid rgba(61,220,151,.35); }
.chip.amber { background:rgba(255,200,87,.12); color:#ffd479; border:1px solid rgba(255,200,87,.35); }
.chip.gray { background:rgba(139,149,171,.12); color:#a3adc2; border:1px solid rgba(139,149,171,.3); }
.chip.warn { background:rgba(255,140,60,.15); color:#ffab6e; border:1px solid rgba(255,140,60,.45); font-weight:700; }
.chip.cycle { background:rgba(94,170,255,.14); color:#82bcff; border:1px solid rgba(94,170,255,.4); }
.chip.basic { background:rgba(167,139,250,.14); color:#bda4ff; border:1px solid rgba(167,139,250,.4); }
.note { font-size:12.5px; color:var(--muted); background:var(--bg2); border-left:3px solid #3b4a6b;
        padding:8px 12px; border-radius:0 8px 8px 0; margin:8px 0; white-space:pre-wrap; line-height:1.7; }
pre { font-size:12.5px; color:#b8c3da; background:var(--bg2); border:1px solid #232c44;
      padding:10px 12px; border-radius:8px; overflow-x:auto; margin:8px 0; white-space:pre-wrap;
      font-family:-apple-system,"Microsoft YaHei UI",Consolas,monospace; line-height:1.8; }
.foot { text-align:center; color:var(--muted); font-size:12px; margin-top:24px; letter-spacing:.5px; }
::-webkit-scrollbar { height:8px; width:8px; }
::-webkit-scrollbar-thumb { background:#2e3852; border-radius:4px; }
::-webkit-scrollbar-track { background:transparent; }
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
