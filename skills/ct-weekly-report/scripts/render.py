"""CT 周报渲染：$CTW_WORK/data.json (+ 可选 insights.json) → 单文件 HTML（内联 SVG，无外部依赖）。

用法：python render.py [--out docs/reports/ct-weekly-YYYY-MM-DD.html]
  --issues-only  只打印自动识别的问题清单（给 agent 写 insights.json 用），不出 HTML

insights.json（agent 填写，可缺省）:
  {"advice": {"<问题 id>": "一句建议"}, "callback_missing": [["10-14", 591], ...] | null}
"""
import argparse
import html
import json
import math
import os
from collections import defaultdict
from datetime import datetime

FAIL_TYPES = {'ct_failure': 'CT 连续失败', 'patch_failure': 'Patch 失败', 'dashboard_task_fail_rate': '失败率超标'}
TYPES = [('queue', '排队积压', '#3370ff'), ('ct_failure', 'CT 连续失败', '#f54a45'), ('patch_failure', 'Patch 失败', '#ff8800'),
         ('dashboard_task_fail_rate', '失败率超标', '#d83931'), ('killed', 'Daily 被终止', '#7f3bf5')]
REASONS = [('rcase', '用例问题', '#ff8800'), ('rsys', '未定位（疑似系统）', '#d83931'),
           ('renv', '测试环境', '#3370ff'), ('rother', '其他已分析原因', '#7f3bf5'), ('rnone', '未分析', '#c9cdd4')]
MAIN_GROUP = 'oc_81a9114a889e9d2a8ffe393593913ea9'   # 自动化测试框架开发小组
e = html.escape


def hrs(m):
    if m is None:
        return '-'
    return f'{m / 60:.1f} 小时' if m >= 60 else f'{round(m)} 分钟'


def pct(a, b, d=1):
    return f'{a / b * 100:.{d}f}%' if b else '-'


def rate(f, done):
    return f / done * 100 if done else 0


# ---------------- 数据整理 ----------------
def build(D):
    W = D['window']
    stopped = D['stopped']
    qline = {r['tk']: r for r in D['queue']}
    cur, prev = D['ct'], D['ct_prev']
    R = {'W': W, 'cur': cur, 'prev': prev, 'stopped': stopped}

    # 排队预警：按产品 + 分支（任务线）
    qa = defaultdict(lambda: {'c': 0, 'claimed': 0})
    for a in D['alarms']:
        if a['t'] == 'queue_overflow':
            k = (a['p'], a['br'], a['tk'])
            qa[k]['c'] += a['c']
            qa[k]['claimed'] += a['claimed']
    R['queue_alarms'] = sorted(({'p': p, 'br': br, 'tk': tk, **v, 'line': qline.get(tk)} for (p, br, tk), v in qa.items()),
                               key=lambda x: -x['c'])
    R['queue_total'] = sum(x['c'] for x in R['queue_alarms'])
    R['queue_claimed'] = sum(x['claimed'] for x in R['queue_alarms'])
    R['queue_prev'] = sum(a['c'] for a in D['alarms_prev'] if a['t'] == 'queue_overflow')

    # 失败预警
    fa = defaultdict(int)
    for a in D['alarms']:
        if a['t'] in FAIL_TYPES:
            fa[(a['t'], a['p'], a['br'])] += a['c']
    R['fail_alarms'] = {t: sorted([(p, br, c) for (tt, p, br), c in fa.items() if tt == t], key=lambda x: -x[2]) for t in FAIL_TYPES}
    R['fail_alarm_total'] = sum(fa.values())
    R['fail_alarm_prev'] = sum(a['c'] for a in D['alarms_prev'] if a['t'] in FAIL_TYPES)

    # 失败率（含剔除停线后的对照）
    fl = D['fail_lines']
    R['fail_lines'] = sorted(fl, key=lambda r: -r['f'])
    keep = lambda rows: [r for r in rows if r['tk'] not in stopped]
    R['rate'] = rate(cur['failed'], cur['done'])
    R['rate_prev'] = rate(prev['failed'], prev['done'])
    ex_c = [sum(r['f'] for r in keep(D['fail_lines'])), cur['done'] - sum(r['done'] for r in D['fail_lines'] if r['tk'] in stopped)]
    # 本期停线的线若没有失败，不在 fail_lines 里，done 也要扣掉：用 queue 明细补
    for tk in stopped:
        if not any(r['tk'] == tk for r in D['fail_lines']) and tk in qline:
            ex_c[1] -= (qline[tk]['started'] or 0)
    ex_p = [sum(r['f'] for r in keep(D['fail_lines_prev'])), sum(r['done'] for r in keep(D['fail_lines_prev']))]
    R['rate_ex'], R['rate_ex_prev'] = rate(*ex_c), rate(*ex_p)
    R['fail_products'] = len({r['p'] for r in fl})

    # 产品 × 异常矩阵、每日
    mx = defaultdict(lambda: defaultdict(int))
    days = defaultdict(lambda: defaultdict(int))
    for a in D['alarms']:
        t = 'queue' if a['t'] == 'queue_overflow' else a['t']
        if t in dict((k, 1) for k, _, _ in TYPES):
            mx[(a['p'], a['br'])][t] += a['c']
            days[a['d']][t] += a['c']
    for k in D['killed']:
        mx[(k['p'], k['br'])]['killed'] += k['c']
        days[k['d']]['killed'] += k['c']
    R['matrix'] = sorted(mx.items(), key=lambda kv: -sum(kv[1].values()))
    R['alarm_days'] = sorted(days.items())
    R['killed_total'] = sum(k['c'] for k in D['killed'])
    R['killed_prev'] = sum(k['c'] for k in D['killed_prev'])
    R['groups'] = defaultdict(int)
    for a in D['alarms']:
        R['groups'][a['grp'] or '（无规则，内置预警）'] += a['c']
    R['sync'] = sum(a['c'] for a in D['alarms'] if a['t'] == 'sync_failure')
    R['sync_prev'] = sum(a['c'] for a in D['alarms_prev'] if a['t'] == 'sync_failure')

    # 自动识别的问题
    issues = []
    busy = [x for x in R['queue_alarms'] if x['line'] and not x['line'].get('stopped') and (x['line']['p50'] or 0) > 60]
    if busy:
        issues.append({'id': 'queue', 'title': '、'.join(f"{x['p']} {x['br']}" for x in busy) + ' CT 排队严重',
                       'impact': '；'.join(f"{x['p']} {x['br']} 一半任务排队超过 {hrs(x['line']['p50'])}（平均 {hrs(x['line']['avg_w'])}）" for x in busy),
                       'products': '、'.join(f"{x['p']} {x['br']}" for x in busy)})
    if stopped:
        ls = [qline.get(tk) or next((r for r in D['fail_lines'] if r['tk'] == tk), {'tk': tk}) for tk in stopped]
        issues.append({'id': 'stopped', 'title': f'{len(stopped)} 条 CT 线停止产生新任务',
                       'impact': '；'.join(f"{tk} 自 {t} 起无新任务" for tk, t in stopped.items())
                                 + (f"；窗口内从未执行 {sum((l.get('ns') or 0) for l in ls)} 个" if any(l.get('ns') for l in ls) else ''),
                       'products': '、'.join(sorted({f"{l.get('p', '')} {l.get('br', '')}".strip() for l in ls}))})
    high = [r for r in fl if rate(r['f'], r['done']) >= 5 and r['f'] >= 5]
    if high:
        high.sort(key=lambda r: -rate(r['f'], r['done']))
        issues.append({'id': 'fail', 'title': '个别产品 CT 失败率高', 'impact': '代码合不进去',
                       'products': '，'.join(f"{r['p']} {r['br']} {rate(r['f'], r['done']):.0f}%（{r['f']} 次）" for r in high)})
    kd = defaultdict(set)
    kc = defaultdict(int)
    for k in D['killed']:
        kd[(k['p'], k['br'], k['tk'])].add(k['d'])
        kc[(k['p'], k['br'], k['tk'])] += k['c']
    rep = sorted([k for k, d in kd.items() if len(d) >= 3], key=lambda k: -kc[k])
    if rep:
        issues.append({'id': 'killed', 'title': 'Daily 例行测试反复被终止', 'impact': '当天缺少该产品的测试结果',
                       'products': '，'.join(f"{p} {br}（{tk}）{len(kd[(p, br, tk)])} 天 {kc[(p, br, tk)]} 次" for p, br, tk in rep)})
    if R['queue_total'] and R['queue_claimed'] / R['queue_total'] < 0.2:
        issues.append({'id': 'unclaimed', 'title': '预警没人处理',
                       'impact': f"{R['queue_total']} 次排队预警只有 {R['queue_claimed']} 次被认领", 'products': '平台'})
    R['issues'] = issues
    return R


# ---------------- SVG 图表 ----------------
def hbars(rows, label_w=240, width=1060, unit_label=lambda r: ''):
    """rows: [(label, [(value, color, title)], tail_text, muted)]"""
    rh = 28
    mx = max((sum(v for v, _, _ in segs) for _, segs, _, _ in rows), default=1) or 1
    scale = (width - label_w - 260) / mx
    out = [f'<svg viewBox="0 0 {width} {len(rows) * rh + 6}" width="100%" role="img">']
    for i, (label, segs, tail, muted) in enumerate(rows):
        y = i * rh + 3
        mstyle = ' style="fill:#8f959e"' if muted else ''
        mop = ' opacity="0.55"' if muted else ''
        out.append(f'<text x="{label_w - 8}" y="{y + 14}" text-anchor="end" class="val"{mstyle}>{e(label)}</text>')
        x = label_w
        for v, color, title in segs:
            if not v:
                continue
            w = max(2, v * scale)
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="18" fill="{color}"{mop}><title>{e(title)}</title></rect>')
            x += w
        out.append(f'<text x="{x + 6:.1f}" y="{y + 14}" class="val">{e(tail)}</text>')
    return ''.join(out) + '</svg>'


def vstack(days, types, height=240, width=1060):
    L, B, T = 40, 28, 30
    tot = [sum(v.get(k, 0) for k, _, _ in types) for _, v in days]
    top = max(10, math.ceil(max(tot, default=1) / 10) * 10)
    bw = (width - L) / max(1, len(days))
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for y in range(0, top + 1, max(1, top // 4)):
        py = height - B - (height - B - T) * y / top
        out.append(f'<line x1="{L}" x2="{width}" y1="{py:.1f}" y2="{py:.1f}" stroke="#e5e6eb"/><text x="{L - 6}" y="{py + 4:.1f}" text-anchor="end">{y}</text>')
    for i, (d, v) in enumerate(days):
        x, w, y = L + i * bw + bw * 0.22, bw * 0.56, height - B
        for k, name, color in types:
            n = v.get(k, 0)
            if not n:
                continue
            h = (height - B - T) * n / top
            y -= h
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{color}"><title>{d} {name}: {n}</title></rect>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" class="val">{tot[i]}</text>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{height - 8}" text-anchor="middle">{d}</text>')
    return ''.join(out) + '</svg>'


def legend(items):
    return '<div class="legend">' + ''.join(f'<span><i style="background:{c}"></i>{e(n)}</span>' for n, c in items) + '</div>'


def heat(n, mx, color):
    if not n:
        return '<td style="color:#c9cdd4">-</td>'
    a = 0.12 + 0.88 * n / mx
    return f'<td class="heat" style="background:{color}{round(a * 255):02x};color:{"#fff" if a > 0.5 else "#1f2329"}">{n}</td>'


# ---------------- 页面 ----------------
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2329;--muted:#646a73;--line:#e5e6eb}*{box-sizing:border-box}
body{margin:0;font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:1120px;margin:0 auto;padding:24px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 4px}h3{font-size:15px;margin:16px 0 4px}
a{color:#3370ff}.hint{color:var(--muted);font-size:13px;margin:0 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px;margin-top:16px}
.boss{border:2px solid #3370ff;background:linear-gradient(#f0f4ff,#fff 160px)}
.top{font-size:15px;margin:6px 0 14px;padding-left:20px;line-height:1.9}.top ul{font-size:14px}
.kpis{display:grid;gap:12px;grid-template-columns:repeat(4,1fr)}.kpi{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.kpi .v{font-size:26px;font-weight:700;line-height:1.2}.kpi span{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:center}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:500;background:#fafbfc}
.todo td,.todo th{text-align:left;vertical-align:top}td.heat{font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
svg text{font-size:12px;fill:var(--muted)}svg .val{fill:var(--fg);font-weight:600}
h2.part{font-size:15px;color:var(--muted);margin:28px 0 0;padding-top:12px;border-top:1px dashed var(--line)}
.red{color:#d83931;font-weight:600}.note li{color:var(--muted);margin:4px 0}
@media(max-width:820px){.kpis{grid-template-columns:1fr 1fr}}
"""


def page(R, ins):
    W, cur, prev = R['W'], R['cur'], R['prev']
    advice = (ins or {}).get('advice', {})
    cb = (ins or {}).get('callback_missing')
    win = f"{W['start'][:16]} ~ {W['end'][:16]}"
    P = []
    P.append(f'<h1>CT 周报</h1><p class="hint">{win}（最近 7 天）· 范围：全部 CT（准入测试）执行记录 + 各群收到的预警 · '
             f'「上期」= {W["prev_start"][:16]} ~ {W["start"][:16]} · 生成于 {W["generated_at"]}</p>')

    # 顶部三条
    def qline_txt(x):
        l = x['line']
        if not l:
            return f"{e(x['p'])} {e(x['br'])}：{x['c']} 次"
        s = (f"{e(x['p'])} {e(x['br'])}：{x['c']} 次，平均排队 <b>{hrs(l['avg_w'])}</b>，一半任务超过 {hrs(l['p50'])}，"
             f"最长 {hrs(l['max_w'])}")
        if l.get('stopped'):
            s += f"（{l['stopped'][5:]} 起停止产生新任务）"
        return s
    fa_items = ''.join(
        f"<li>{name} {sum(c for _, _, c in R['fail_alarms'][t])} 次"
        + (('：' + '、'.join(f"{e(p)} {e(br)} {c} 次" for p, br, c in R['fail_alarms'][t])) if R['fail_alarms'][t] else '') + '</li>'
        for t, name in FAIL_TYPES.items())
    ex = ''
    if R['stopped']:
        ex = f"；剔除本期停线的 {len(R['stopped'])} 条线后为 {R['rate_ex']:.2f}%，上期 {R['rate_ex_prev']:.2f}%"
    P.append(f"""<section class="card boss"><h2 style="font-size:19px">本周要点</h2><ul class="top">
<li><b>本周排队预警 {R['queue_total']} 次</b>（上期 {R['queue_prev']} 次，本期 {R['queue_claimed']} 次被认领）：
<ul>{''.join(f'<li>{qline_txt(x)}</li>' for x in R['queue_alarms'])}</ul></li>
<li><b>CT 失败率 {R['rate']:.1f}%</b>（{cur['failed']} / {cur['done']}），上期 {R['rate_prev']:.1f}%{ex}</li>
<li><b>CT 失败预警 {R['fail_alarm_total']} 次</b>（上期 {R['fail_alarm_prev']} 次）：<ul>{fa_items}</ul></li></ul>""")
    gt1h, gt1h_p = rate(cur['gt1h'], cur['started']), rate(prev['gt1h'], prev['started'])
    kp = [('CT 排队超过 1 小时', f'{gt1h:.1f}%', f"{cur['gt1h']} / {cur['started']}，上期 {gt1h_p:.1f}%"),
          ('CT 任务一直没执行', cur['never_ran'], f"上期 {prev['never_ran']}"),
          ('Daily 被终止', R['killed_total'], f"上期 {R['killed_prev']}"),
          ('出现失败的产品', f"{R['fail_products']} / {cur['products']}", f"CT 共 {cur['n']} 次执行")]
    P.append('<div class="kpis">' + ''.join(f'<div class="kpi"><div class="v">{v}</div>{l}<br><span>{s}</span></div>' for l, v, s in kp) + '</div>')
    P.append('<h3>问题与建议</h3><table class="todo"><tr><th>问题</th><th>影响</th><th>涉及产品</th><th>建议</th></tr>'
             + ''.join(f"<tr><td><b>{i + 1}. {e(x['title'])}</b></td><td>{e(x['impact'])}</td><td>{e(x['products'])}</td>"
                       f"<td>{e(advice.get(x['id'], '待补充'))}</td></tr>" for i, x in enumerate(R['issues']))
             + ('' if R['issues'] else '<tr><td colspan="4">本周没有自动识别出的问题</td></tr>') + '</table>'
             '<p class="hint" style="margin:10px 0 0"><a href="#queue">排队</a> · <a href="#fail">CT 失败</a> · <a href="#alarm">预警统计</a> · <a href="#platform">平台问题</a></p></section>')

    # 排队
    Q = sorted(R['queue_lines'], key=lambda r: -(r['avg_w'] or 0))
    rows = []
    for r in Q:
        m = r['avg_w'] or 0
        color = '#d83931' if m > 600 else '#ff8800' if m > 60 else '#3370ff'
        rows.append((f"{r['p']} {r['br']}", [(m, color, f"平均 {hrs(m)}")],
                     f"平均 {hrs(m)} · 一半超过 {hrs(r['p50'])}" + (f"（{r['stopped'][5:]} 起停止）" if r.get('stopped') else ''), bool(r.get('stopped'))))
    dset = sorted({d for d, _ in R['queue_days_all']})
    hm = '<table><tr><th>任务线</th>' + ''.join(f'<th>{d}</th>' for d in dset) + '</tr>'
    for r in Q:
        hm += f"<tr><td><b>{e(r['p'])} {e(r['br'])}</b></td>"
        for d in dset:
            x = R['queue_days'].get((r['tk'], d))
            if not x:
                hm += '<td style="color:#c9cdd4">-</td>'
            elif not x['started']:
                hm += '<td class="heat" style="background:#1f2329;color:#fff">全天未执行</td>'
            else:
                m = x['avg_w'] or 0
                a = min(1, math.log10(1 + m) / math.log10(3000))
                hm += f'<td class="heat" style="background:rgba(216,57,49,{0.08 + 0.92 * a:.2f});color:{"#fff" if a > 0.55 else "#1f2329"}">{m / 60:.1f}</td>'
        hm += '</tr>'
    hm += '</table>'
    qt = ('<table><tr><th>任务线</th><th>任务数</th><th>平均排队</th><th>中位数</th><th>最长</th><th>排队超 1 小时</th><th>从未执行</th><th>单次运行</th><th>状态</th></tr>'
          + ''.join(f"<tr><td><b>{e(r['p'])} {e(r['br'])}</b></td><td>{r['n']}</td><td>{hrs(r['avg_w'])}</td><td>{hrs(r['p50'])}</td><td>{hrs(r['max_w'])}</td>"
                    f"<td>{r['gt1h']}（{pct(r['gt1h'], r['started'], 0)}）</td><td>{r['ns'] or '-'}</td><td>{(r['run'] or 0):.1f} 分钟</td>"
                    f"<td>{'自 ' + r['stopped'][5:] + ' 起停止' if r.get('stopped') else '运行中'}</td></tr>" for r in Q) + '</table>')
    P.append(f"""<section class="card" id="queue"><h2>CT 任务排队多久</h2>
<p class="hint">排队时长 = 任务创建到开始执行的时间，只统计已开始执行的任务。列出平均排队超过 15 分钟、从未执行超过 5 个、或触发过排队预警的任务线。</p>
{hbars(rows)}<h3>每天平均排队时长（小时）</h3><p class="hint">按任务创建日期。「全天未执行」= 当天创建的任务一个都没开始；「-」= 当天没有新任务。</p>{hm}<h3>明细</h3>{qt}</section>""")

    # 失败
    FL = R['fail_lines']
    key = [r for r in FL if r['f'] >= 10 or rate(r['f'], r['done']) >= 5]
    frows = [(f"{r['p']} {r['br']}", [(r[k], c, n) for k, n, c in REASONS],
              f"{r['f']} 次 · 失败率 {rate(r['f'], r['done']):.1f}%" + ('（样本少）' if r['done'] < 20 else ''), False) for r in key]
    fdays = [(x['d'], {'f': x['f']}) for x in R['ct_days']]
    ft = ('<table><tr><th>产品</th><th>分支</th><th>失败</th><th>失败率</th>' + ''.join(f'<th>{n}</th>' for _, n, _ in REASONS)
          + '<th>未跑完</th><th>出现失败的日期</th></tr>'
          + ''.join(f"<tr><td><b>{e(r['p'])}</b></td><td>{e(r['br'])}</td><td><b>{r['f']}</b></td>"
                    f"<td class='{'red' if rate(r['f'], r['done']) >= 5 else ''}'>{rate(r['f'], r['done']):.1f}%</td>"
                    + ''.join(f"<td>{r[k] or ''}</td>" for k, _, _ in REASONS) + f"<td>{r['unfin'] or ''}</td><td>{e(r['days'] or '')}</td></tr>" for r in FL)
          + f"<tr><td><b>合计</b></td><td></td><td><b>{sum(r['f'] for r in FL)}</b></td><td><b>{R['rate']:.1f}%</b></td>"
          + ''.join(f"<td><b>{sum(r[k] for r in FL)}</b></td>" for k, _, _ in REASONS) + f"<td><b>{sum(r['unfin'] for r in FL)}</b></td><td></td></tr></table>")
    none_rate = pct(sum(r['rnone'] for r in FL), sum(r['f'] for r in FL), 0)
    P.append(f"""<section class="card" id="fail"><h2>CT 失败：哪些产品失败了、失败几次</h2>
<p class="hint">只统计执行结果为「失败」的 CT 任务，按执行次数计。失败率 = 失败 / 跑完（成功 + 失败）；超时和被取消不算失败。本期失败原因未分析占 {none_rate}。</p>
<h3>重点产品（失败 ≥ 10 次或失败率 ≥ 5%）</h3>{hbars(frows, label_w=260)}{legend([(n, c) for _, n, c in REASONS])}
<h3>每天失败次数</h3>{vstack(fdays, [('f', '失败', '#d83931')], height=200)}<h3>全部失败产品明细</h3>{ft}
<p class="hint" style="margin-top:10px">失败原因来自日志自动分析（规则匹配 + 运行时长推断），不是人工确认的根因：用例问题 = testcase_vela / Test；未定位（疑似系统）= 用例没失败且日志没命中规则；测试环境 = 珊瑚海环境或日志不可访问；其他已分析原因 = 刷机失败、busyloop、Gerrit 等其他规则；未分析 = 没有结论。</p></section>""")

    # 预警
    P.append('<h2 class="part">预警与平台问题（研发基本无感知，供平台组跟进）</h2>')
    mxv = max((v for _, d in R['matrix'] for v in d.values()), default=1)
    mt = ('<table><tr><th>产品</th><th>分支</th>' + ''.join(f'<th>{n}</th>' for _, n, _ in TYPES) + '<th>合计</th></tr>'
          + ''.join(f"<tr><td><b>{e(p)}</b></td><td>{e(br)}</td>" + ''.join(heat(d.get(k, 0), mxv, c) for k, _, c in TYPES)
                    + f"<td><b>{sum(d.values())}</b></td></tr>" for (p, br), d in R['matrix'])
          + '<tr><td><b>合计</b></td><td></td>' + ''.join(f"<td><b>{sum(d.get(k, 0) for _, d in R['matrix'])}</b></td>" for k, _, _ in TYPES)
          + f"<td><b>{sum(sum(d.values()) for _, d in R['matrix'])}</b></td></tr></table>")
    grp = '、'.join(f"{'自动化测试框架开发小组' if g == MAIN_GROUP else g} {c} 次" for g, c in R['groups'].items())
    P.append(f"""<section class="card" id="alarm"><h2>哪些产品出了哪些异常、各几次</h2>
<p class="hint">排队、失败类按预警次数计；Daily 被终止按被终止的任务数计（dev / trunk-26rc01）。按群：{e(grp) or '无'}。</p>{mt}
<h3>每天发生多少次</h3>{vstack(R['alarm_days'], TYPES)}{legend([(n, c) for _, n, c in TYPES])}</section>""")
    cb_txt = ('数据缺失（未从日志回捞）' if cb is None else
              f"{len(cb)} 份日报，共 {sum(n for _, n in cb)} 条：" + '、'.join(f'{d} {n} 条' for d, n in cb) if cb else '0')
    P.append(f"""<section class="card" id="platform"><h2>平台自身问题</h2><table><tr><th>问题</th><th>本周</th><th>上期</th></tr>
<tr><td>数据同步失败预警</td><td>{R['sync']}</td><td>{R['sync_prev']}</td></tr>
<tr><td>CT 结果回调缺失</td><td>{e(cb_txt)}</td><td>-</td></tr></table></section>""")
    P.append("""<section class="card note"><h2>数据说明</h2><ul>
<li>数据来源：vela_autotest_execution_record、vela_autotest_alarm_record；回调缺失来自线上日志，可能有漏，按下限看。</li>
<li>执行记录按创建时间归属窗口。同步有延迟，窗口末尾的数据在之后几小时内还会增加，重跑同一窗口数字可能略有变化。</li>
<li>「从未执行」= 一直排不上、本地 6 小时无进展后被收口为超时的任务，不计入排队时长。停线 = 窗口内 ≥ 50 个任务、但最后 24 小时没有新任务的 CT 线。</li>
<li>失败率预警（看护看板）按另一套口径统计，与执行记录的失败数不一定一致。</li></ul></section>""")
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>CT 周报 · {win}</title><style>{CSS}</style></head><body><main>{"".join(P)}</main></body></html>')


def check(R, D):
    """自检：各口径合计必须一致，不一致直接报错。"""
    assert sum(r['f'] for r in D['fail_lines']) == R['cur']['failed'] == sum(x['f'] for x in D['ct_days']), '失败合计不一致'
    assert R['cur']['gt1h'] == sum(x['gt1h'] for x in D['ct_days']), '排队 >1h 合计不一致'
    for r in D['fail_lines']:
        assert sum(r[k] for k, _, _ in REASONS) == r['f'], f"失败原因合计不一致 {r['tk']}"
    tot = sum(sum(d.values()) for _, d in R['matrix'])
    assert tot == sum(sum(d.values()) for _, d in R['alarm_days']), '预警矩阵与每日合计不一致'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--issues-only', action='store_true')
    a = ap.parse_args()
    work = os.environ.get('CTW_WORK')
    if not work:
        base = '/tmp/ct-weekly'
        work = os.path.join(base, sorted(d for d in os.listdir(base) if d != 'regress')[-1])
    D = json.load(open(os.path.join(work, 'data.json')))
    R = build(D)
    R['queue_lines'] = D['queue']
    R['queue_days'] = {(x['tk'], x['d']): x for x in D['queue_daily']}
    R['queue_days_all'] = [(x['d'], 1) for x in D['queue_daily']]
    R['ct_days'] = D['ct_days']
    check(R, D)
    if a.issues_only:
        print(json.dumps(R['issues'], ensure_ascii=False, indent=1))
        return
    ip = os.path.join(work, 'insights.json')
    ins = json.load(open(ip)) if os.path.exists(ip) else None
    end = datetime.strptime(D['window']['end'], '%Y-%m-%d %H:%M:%S')
    out = a.out or f'docs/reports/ct-weekly-{end:%Y-%m-%d}.html'
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    open(out, 'w', encoding='utf-8').write(page(R, ins))
    print('已生成', out, '| 问题', len(R['issues']), '| insights', '有' if ins else '无（建议列为「待补充」）')


if __name__ == '__main__':
    main()
