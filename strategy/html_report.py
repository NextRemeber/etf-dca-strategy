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
    """分隔线: 去掉所有空白后只剩 ─ / ═ / = 等制表符"""
    s = "".join(line.split())
    if len(s) < 6:
        return False
    return set(s) <= set("─═=━-")


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
    """涨跌列: A股习惯 红涨绿跌; emoji 换成 ▲▼ 避免无字体环境变方块"""
    txt = cell.replace("📈", "▲").replace("📉", "▼")
    if cell.startswith("📈"):
        return f'<span class="up">{_esc(txt)}</span>'
    if cell.startswith("📉"):
        return f'<span class="down">{_esc(txt)}</span>'
    return _esc(txt)


def _status_chip(cell):
    if "⚠️" in cell:
        return f'<span class="chip warn">{_esc(cell)}</span>'
    if "🔴" in cell:
        return f'<span class="chip amber">{_esc(cell)}</span>'
    if "🟢" in cell:
        return f'<span class="chip green">{_esc(cell)}</span>'
    return _esc(cell)


_NUM_HEADS = ("分值", "均值", "vs均值", "强势", "倍数", "现价", "金额")
# 窄屏隐藏的次要列 (决策不需要: 代码/信号/现价/溢价/统计列/选中标记)
_SEC_HEADS = ("代码", "信号", "现价", "溢价", "均值", "vs均值", "强势", "趋势", "选中", "分值条")


def _sec_class(header):
    return "sec-col" if any(k in header for k in _SEC_HEADS) else ""


def _align_class(header):
    return "num" if any(k in header for k in _NUM_HEADS) else ""


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
    # 丢弃"分值条"列: █░ 字符在多数无衬线字体下渲染成乱块, 信息由分值列承载
    keep = [i for i, h in enumerate(header_cells) if "分值条" not in h]
    hdr = [header_cells[i] for i in keep]
    th = "".join(f'<th class="{_sec_class(h)}">{_esc(h)}</th>' for h in hdr)
    body = []
    for r in rows:
        # 池内标的行加底色突出 (选中列含 ⭐/◆)
        is_pool = any("⭐" in c or "◆" in c for c in r)
        tds = []
        for i in keep:
            cell = r[i] if i < len(r) else ""
            cls = (_align_class(header_cells[i]) + " " + _sec_class(header_cells[i])).strip()
            tds.append(f'<td class="{cls}">{_styled_cell(header_cells[i], cell)}</td>')
        body.append(f'<tr class="{"pool" if is_pool else ""}">' + "".join(tds) + "</tr>")
    wide = ' class="wide"' if len(hdr) >= 10 else ''
    return (f'<div class="tw"><table{wide}><thead><tr>{th}</tr></thead>'
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
.notes { font-size:12.5px; color:var(--muted); background:var(--bg2);
         border-left:3px solid #3b4a6b; padding:8px 12px; border-radius:0 8px 8px 0;
         margin:8px 0; line-height:1.75; }
.notes > div + div { margin-top:4px; }
td.num, th.num { text-align:right; font-family:Consolas,monospace; }
tr.pool td { background:rgba(94,170,255,.055); }
tr.pool td:first-child { font-weight:700; }
pre { font-size:12.5px; color:#b8c3da; background:var(--bg2); border:1px solid #232c44;
      padding:10px 12px; border-radius:8px; overflow-x:auto; margin:8px 0; white-space:pre-wrap;
      font-family:-apple-system,"Microsoft YaHei UI",Consolas,monospace; line-height:1.8; }
.action { display:flex; flex-wrap:wrap; align-items:center; gap:14px 26px;
           background:linear-gradient(135deg,#1b2942,#22304d); border:1px solid #2f4166;
           border-radius:14px; padding:16px 22px; margin-bottom:20px; }
.action .act-main { display:flex; align-items:baseline; gap:6px; }
.action .act-main .k { font-size:12.5px; color:var(--muted); }
.action .act-main .v { font-size:30px; font-weight:800; color:#7fc4ff;
                       font-family:"JetBrains Mono",Consolas,monospace; letter-spacing:-1px; }
.action .act-main .u { font-size:13px; color:var(--muted); }
.action .act-sub { display:flex; gap:8px; flex-wrap:wrap; }
.action .pill { font-size:12.5px; padding:3px 11px; border-radius:999px;
                background:rgba(94,170,255,.1); color:#9ccaff; border:1px solid rgba(94,170,255,.28); }
.action .pill.hot { background:rgba(255,107,107,.13); color:#ff9a9a; border-color:rgba(255,107,107,.4); font-weight:700; }
.action .act-rot { font-size:12.5px; color:#c3cee4; width:100%; padding-top:4px;
                   border-top:1px solid #2a3550; }
.foot { text-align:center; color:var(--muted); font-size:12px; margin-top:24px; letter-spacing:.5px; }
@media (max-width: 820px) {
  .wrap { padding:16px 12px 40px; max-width:100%; overflow-x:hidden; }
  .hero { padding:16px 18px; border-radius:12px; }
  .hero h1 { font-size:16px; letter-spacing:0; }
  .hero .sub { font-size:11.5px; }
  .kpis { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .kpi { padding:10px 12px; }
  .kpi .v { font-size:16px; }
  .action { padding:12px 14px; gap:10px 14px; }
  .action .act-main .v { font-size:24px; }
  .action .pill { font-size:11.5px; padding:2px 9px; }
  section { padding:14px 14px; border-radius:12px; }
  section h2 { font-size:13.5px; }
  table { font-size:11.5px; table-layout:auto; }
  th, td { padding:6px 6px; white-space:normal; word-break:break-word; }
  /* 窄屏只留决策列: 名称/分数/档位/倍数/金额/状态 */
  .sec-col { display:none !important; }
  .notes { font-size:11.5px; }
}
.tw { max-width:100%; }
::-webkit-scrollbar { height:8px; width:8px; }
::-webkit-scrollbar-thumb { background:#2e3852; border-radius:4px; }
::-webkit-scrollbar-track { background:transparent; }
"""


# 分区优先级: 先"今天做什么"(可执行), 再看市场全景, 最后参考信息
_ORDER = [
    ("💰", 1), ("🏦", 2), ("🔄", 3),       # 执行区
    ("📊", 4),                              # 市场全景 (42只明细)
    ("📌", 5), ("📐", 6), ("💡", 7),        # 参考区
]


def _order_key(sec):
    icon, title, _ = sec
    for i, (ic, pri) in enumerate(_ORDER):
        if icon == ic:
            # 🔄 有两个(调仓指令/池内状态): 含"调仓"的优先
            if ic == "🔄":
                return (pri, 0 if "调仓" in title else 1, i)
            return (pri, 0, i)
    return (99, 0, 0)


def render_html(text, date_str, extra_meta=""):
    indices, sections = _parse(text)
    sections = sorted(sections, key=_order_key)
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

    # 今日行动摘要条: 合计投入 + 各块金额 + 调仓方向 (关键信息前置)
    import re as _re
    def _grab(pat):
        m = _re.search(pat, text)
        return m.group(1) if m else ""
    total = _grab(r"合计\s+5 标的\s+(\d+)元")
    cyc = _grab(r"周期卫星本月合计: (\d+)元")
    basic = _grab(r"占预算60%=(\d+)元/月")
    ai_amt = _grab(r"AI\s+\(159819\).*?(\d+)元")
    gold_amt = _grab(r"黄金\s+\(518880\).*?(\d+)元")
    rot = _grab(r"周期卫星: (.*)")
    basic_rot = _grab(r"基本配置: 纳指 (\d+)分 vs 红利低波 (\d+)分")
    chips = []
    if total:
        chips.append(f'<div class="act-main"><span class="k">今日合计投入</span>'
                     f'<span class="v">{total}</span><span class="u">元</span></div>')
    sub = []
    if ai_amt:
        sub.append(f'<span class="pill hot">AI {ai_amt}元</span>')
    if gold_amt:
        sub.append(f'<span class="pill">黄金 {gold_amt}元</span>')
    if cyc:
        sub.append(f'<span class="pill">周期 {cyc}元</span>')
    if basic:
        sub.append(f'<span class="pill">基本 {basic}元</span>')
    if sub:
        chips.append('<div class="act-sub">' + "".join(sub) + '</div>')
    if rot:
        chips.append(f'<div class="act-rot"><b>调仓</b> · {_esc(rot)}</div>')
    if chips:
        out.append('<div class="action">' + "".join(chips) + '</div>')
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
        i = 0
        while i < len(items):
            it = items[i]
            if it[0] == "table":
                out.append(_render_table(it[1], it[2]))
                i += 1
            else:
                # 连续 note/pre 合并为一块说明区 (减少碎片化方框)
                buf = []
                while i < len(items) and items[i][0] != "table":
                    buf.append(_esc(items[i][1]))
                    i += 1
                out.append('<div class="notes">' +
                           "".join(f"<div>{b}</div>" for b in buf) + "</div>")
        out.append("</section>")
    out.append(f'<div class="foot">生成于 {_esc(date_str)} · 仅供研究参考, 不构成投资建议</div></div></body></html>')
    return "".join(out)
