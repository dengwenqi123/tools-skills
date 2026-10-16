"""metrics.json + review/*.json + findings.json → report.md + report.html (单文件, 无外部依赖)."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import glob, html, json, os, sys
from collections import Counter

WORK = AIW
OUT = sys.argv[1] if len(sys.argv) > 1 else f'{WORK}/out'
os.makedirs(OUT, exist_ok=True)
m = json.load(open(f'{WORK}/metrics.json'))
F = json.load(open(f'{WORK}/findings.json'))
review = {os.path.basename(p)[:-5]: json.load(open(p)) for p in sorted(glob.glob(f'{WORK}/review/*.json'))}
o, ta = m['overview'], m['timing_all']
W = f"{m['window'][0]} ~ {m['window'][1]}"
H = 'http://vela.agent.xiaomi.srv/vela/issues/'
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
rows = {r['key']: r for r in json.load(open(f'{WORK}/rows.json'))}
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metric_doc import DOC   # 看板右上角 💡 的指标说明


def hrs(mn):
    return '—' if mn is None else (f'{mn:.0f} 分钟' if mn < 120 else f'{mn / 60:.1f} 小时')


def pct(v):
    return '—' if v is None else f'{v}%'


# AI 复核汇总
# 两种复核口径共用一套计数键: 人工依据对照 (一致/部分一致/不一致/无法判定), 源码核对 (成立/存疑/不成立/无法核对)
_SRC = {'源码核对-成立': '一致', '源码核对-存疑': '部分一致', '源码核对-不成立': '不一致', '无法核对': '无法判定'}
SRC_MODE = any(x['verdict'] in _SRC for L in review.values() for x in L)
LB = (dict(ok='成立', part='存疑', bad='不成立', na='无法核对') if SRC_MODE else dict(ok='一致', part='部分', bad='不一致', na='无法判定'))
RV_TITLE = '源码核对成立率' if SRC_MODE else 'AI 复核正确率'
RV_CHART = '源码核对成立率（按垂域）' if SRC_MODE else 'AI 抽样复核正确率（按垂域）'
_cnt = lambda L: Counter(_SRC.get(x['verdict'], x['verdict']) for x in L)


def rv_rate_of(c):
    # 源码核对: 只有「成立」算对 (存疑不折半); 人工依据: 一致 + 0.5 × 部分一致
    judged_n = sum(v for k, v in c.items() if k != '无法判定')
    if not judged_n:
        return None, 0
    ok = c['一致'] if SRC_MODE else c['一致'] + 0.5 * c['部分一致']
    return 100 * ok / judged_n, judged_n


rv_all = _cnt([x for L in review.values() for x in L])
_r, judged = rv_rate_of(rv_all)
rv_rate = round(_r, 1) if _r is not None else None
rv_dom = {d: _cnt(L) for d, L in review.items()}

# ───────────────────────── 表格数据 (md/html 共用) ─────────────────────────
T = {}
T['volume'] = (['入口', '问题类型', '总数', '已分析', '失败', '跳过', '仅总结', '回写 Jira'],
               [[v['entry'], v['cat'], v['total'], v.get('analyzed', 0), v.get('failed', 0), v.get('skip', 0), v.get('circle_only', 0), v['commented']] for v in m['volume']])
T['timing'] = (['问题类型', '成功数', '首结果 P50', '首结果 P90', '端到端 P50', '排队 P50', '排队 P90', '失败数', '失败平均耗时', '单问题 token'],
               [[c, t['n_ok'], hrs(t['first_p50']), hrs(t['first_p90']), hrs(t['e2e_p50']), hrs(t['queue_p50']), hrs(t['queue_p90']),
                 t['n_fail'], hrs(t['fail_avg']), f"{t['tok_avg_k']}k" if t['tok_avg_k'] else '—'] for c, t in m['timing_by_cat'].items()]
               + [['合计', ta['n_ok'], hrs(ta['first_p50']), hrs(ta['first_p90']), hrs(ta['e2e_p50']), hrs(ta['queue_p50']), hrs(ta['queue_p90']),
                   ta['n_fail'], hrs(ta['fail_avg']), f"{ta['tok_avg_k']}k"]])
T['stage'] = (['阶段', '样本', '均值', 'P50', 'P90'], [[k, v['n'], hrs(v['avg']), hrs(v['p50']), hrs(v['p90'])] for k, v in m['stage'].items() if v['n']])
T['cp'] = (['检查点', '应过', '通过', '未过', '通过率', '未过原因 (单数)', '说明'],
           [[c['name'], c['base'], c['ok'], c['bad'], pct(c['rate']), '；'.join(f'{a} {n}' for a, n in c['reasons'][:3]) or '—', c['note'] or ''] for c in m['checkpoints']])
EXEC_ORDER = ['环境', '代码下载', '工具', 'skill', 'MCP']
T['exec'] = (['类别', '涉及问题数', '报错次数', '具体问题 (涉及单数)', '例子'],
             [[c, m['exec_issues'].get(c, {}).get('issues', 0), m['exec_issues'].get(c, {}).get('events', 0),
               '；'.join(f"{s['name']} {s['issues']}" for s in m['exec_issues'].get(c, {}).get('sub', [])[:5]),
               ', '.join(sorted({e['key'] for s in m['exec_issues'].get(c, {}).get('sub', [])[:2] for e in s['ex']}))] for c in EXEC_ORDER])
T['mcp'] = (['MCP 工具', '调用', '失败'], [[t, c, e] for t, c, e in m['usage']['mcp']])
T['domain'] = (['垂域', '问题数', '已分析', '失败', '回写', '有反馈', '反馈覆盖', '有用/部分/无用', '准确率(反馈)', f"{'源码核对' if SRC_MODE else 'AI 复核'} {LB['ok']}/{LB['part']}/{LB['bad']}/{LB['na']}", 'Jira 已解决', '其中已修复', '首结果 P50', '回写但无反馈'],
               [[d['domain'], d['total'], d['analyzed'], d['failed'], d['commented'], d['fb'], pct(d['fb_cover']),
                 f"{d['useful']}/{d['partial']}/{d['useless']}", pct(d['acc']) if d['acc'] is not None else ('样本不足' if d['fb'] else '—'),
                 (lambda c: f"{c['一致']}/{c['部分一致']}/{c['不一致']}/{c['无法判定']}" if c else '未抽样')(rv_dom.get(d['domain'])),
                 d['resolved'], d['fixed'], hrs(d['first_p50']), d['no_fb_n']] for d in m['domains']])
_has_phase = any('phase' in x for L in review.values() for x in L)
_has_chk = any('checked' in x for L in review.values() for x in L)
T['review'] = (['垂域', 'Jira'] + (['阶段'] if _has_phase else []) + ['结论', '依据', 'AI 根因'] + (['核对内容'] if _has_chk else []) + ['理由', '存在的问题'],
               [[d, x['jira_key']] + ([x.get('phase', '')] if _has_phase else []) + [x['verdict'], x['basis'], x['ai_root_cause']]
                + ([x.get('checked', '')] if _has_chk else []) + [x['reason'], '；'.join(x.get('problems') or []) or '—'] for d, L in review.items() for x in L])
T['lowfb'] = (['Jira', '垂域', '评价', '反馈内容'], [[x['key'], x['domain'], x['fb'], x['detail'][:120]] for x in m['low_fb']])
T['fbcat'] = (['问题类型', '本周新增反馈', '有用', '部分有用', '无用', '准确率'],
              [[c, v['n'], v['useful'], v['partial'], v['useless'], pct(v['acc']) if v['acc'] is not None else '样本不足'] for c, v in m['fbweek_by_cat'].items()])
T['nofb'] = (['垂域', '回写但无反馈 (前 10)'], [[d['domain'], ', '.join(d['no_fb_list'])] for d in m['domains'] if d['no_fb_list']])

KPI = [('进入 VelaAgent', o['total'], f"需主动分析 {o['need']}"), ('已分析', o['analyzed'], f"{round(100 * o['analyzed'] / o['need'], 1)}% · 失败 {o['failed']}"),
       ('链路完整走通', o['flow_ok'], f"小队模式 {o['squad']} 单的 {round(100 * o['flow_ok'] / o['squad'], 1)}%"),
       ('首结果 P50 / P90', f"{hrs(ta['first_p50'])}", f"P90 {hrs(ta['first_p90'])}"),
       (RV_TITLE, pct(rv_rate), f"复核 {sum(rv_all.values())} · 可{'核对' if SRC_MODE else '判定'} {judged}"),
       ('有反馈问题', o['fb_issues'], f"VT 表仅 {m['fb_sources']['vt_table']} · 仅在 VelaAgent {m['fb_sources']['multica_only']}"),
       ('Jira 已解决', o['resolved'], f"其中已修复 {o['fixed']}")]


# ───────────────────────── Markdown ─────────────────────────
def md_table(head, body):
    esc = lambda v: str(v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(esc(c) for c in r) + ' |' for r in body])


TITLE = os.environ.get('AIW_TITLE', 'AI 分析周报'); W = os.environ.get('AIW_WINDOW_LABEL', W)
md = [f'# {TITLE} · {W}', '',
      f"> 数据：multica_jira_bridge {m['data_note']['bridge']} 单；执行明细由 me 机器 bug_agents multica CLI 拉原始数据、按 VT 采集器同一 builder 重算；"
      f"Jira 状态实时查询；生成于 {m['generated_at']}。", '', '## 一、本期结论', ''] + [f'- {x}' for x in F['headline']] + ['',
      '## 二、分析量与耗时', '', '### 入口 × 问题类型', '', md_table(*T['volume']), '', '### 耗时（按问题类型）', '', md_table(*T['timing']), '',
      '### 各阶段耗时', '', md_table(*T['stage']), '',
      f"重跑：{m['retry']['issues_with_rerun']} 单共重跑 {m['retry']['reruns']} 次，原因 " + '、'.join(f'{a} {n}' for a, n in m['retry']['reasons']) + '。', '',
      '## 三、调用链逐点检查（vela-tools → VelaAgent）', '', md_table(*T['cp']), '', '### 流程不合规', '']
for p in F['process']:
    md += [f"- **{p['title']}**：{p['detail']} 例：{', '.join(p['examples'])}"]
md += ['', '## 四、执行问题（环境 / 代码下载 / 工具 / skill / MCP）', '', md_table(*T['exec']), '']
md += [f'- **{c}**：{F["exec_notes"][c]}' for c in EXEC_ORDER]
md += ['', '### MCP 调用', '', md_table(*T['mcp']), '',
       f"skill 覆盖率 {m['usage']['skill_cover']}%（有工具调用的问题中用到 skill 的比例）；Top：" + '、'.join(f'{k} {n}' for k, n in m['usage']['skill_top'][:8]) + '。', '',
       '## 五、准确率与反馈（按垂域）', '', md_table(*T['domain']), '', '### 本周新增反馈记录（ai_feedback_records，按问题类型）', '', md_table(*T['fbcat']), '',
       '### ' + ('源码核对复核' if SRC_MODE else 'AI 抽样复核'), '', F['review_note'], '', md_table(*T['review']), '', '### 低分反馈', '', md_table(*T['lowfb']), '',
       '### 已回写但无反馈（催反馈清单）', '', md_table(*T['nofb']), '', '## 六、数据缺口', ''] + [f'- {x}' for x in F['gaps']] + ['', '## 七、建议动作', ''] + [f'{i}. {x}' for i, x in enumerate(F['actions'], 1)]
open(f'{OUT}/report.md', 'w').write('\n'.join(md) + '\n')


# ───────────────────────── HTML ─────────────────────────
def link(k):
    r = rows.get(k)
    if not r:
        return html.escape(k)
    srv = 'phone' if k.split('-')[0] in ('Q61NHQ', 'Q65ZH', 'Q63HQ', 'Q66GTK', 'P62LC', 'P67LC', 'MIWEARRTOS', 'O61WOS6') else 'n'
    return f'<a href="{JIRA[srv]}{k}" target="_blank" rel="noopener">{k}</a><a class="va" href="{html.escape(r["url"] or "")}" target="_blank" rel="noopener" title="VelaAgent">↗</a>'


def cell(v):
    s = str(v)
    toks = s.replace('，', ', ').split(', ')
    if all(t in rows for t in toks if t):
        return ' '.join(link(t) for t in toks if t)
    return html.escape(s)


def h_table(head, body, cls=''):
    return (f'<div class="tw"><table class="{cls}"><thead><tr>' + ''.join(f'<th scope="col">{html.escape(h)}</th>' for h in head) + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td>{cell(c)}</td>' for c in r) + '</tr>' for r in body) + '</tbody></table></div>')


def bars(items, maxv=None, unit=''):
    maxv = maxv or max([v for _, v, _ in items] + [1])
    return '<div class="bars">' + ''.join(
        f'<div class="bar"><span class="bl">{html.escape(n)}</span><span class="bt"><span class="bf {c}" style="width:{max(1, 100 * v / maxv):.1f}%"></span></span><span class="bv">{v}{unit}</span></div>'
        for n, v, c in items) + '</div>'


cp_bars = bars([(c['name'], c['rate'] or 0, 'ok' if (c['rate'] or 0) >= 90 else ('warn' if (c['rate'] or 0) >= 70 else 'bad')) for c in m['checkpoints']], 100, '%')
ex_bars = bars([(c, m['exec_issues'].get(c, {}).get('issues', 0), 'bad' if c in ('环境', '工具') else 'warn') for c in EXEC_ORDER])
stage_bars = bars([(k, v['p90'] or 0, 'bad' if (v['p90'] or 0) > 120 else 'ok') for k, v in m['stage'].items() if v['n']], unit=' 分')
dom_rows = [d for d in m['domains'] if d['domain'] not in ('—',)]
def review_chart():
    """AI 复核正确率条形图. 可判定 <3 单不出比例 (灰), 避免「全部无法判定」被画成 0%."""
    out = []
    for d, c in rv_dom.items():
        r, judged_n = rv_rate_of(c)
        total_n = sum(c.values())
        detail = f"{LB['ok']} {c['一致']} · {LB['part']} {c['部分一致']} · {LB['bad']} {c['不一致']} · {LB['na']} {c['无法判定']}"
        if judged_n < 3:
            cls, w, txt = 'na', 0, f"样本不足（可判定 {judged_n}/{total_n}）"
        else:
            cls, w, txt = ('ok' if r >= 70 else 'warn' if r >= 50 else 'bad'), r, f"{r:.0f}%（可判定 {judged_n}/{total_n}）"
        out.append(f'<div class="bar" title="{html.escape(detail)}"><span class="bl">{html.escape(d)}</span><span class="bt"><span class="bf {cls}" style="width:{max(w, 0.5):.1f}%"></span></span>'
                   f'<span class="bv">{txt}</span></div><div class="bd">{html.escape(detail)}</div>')
    how = ('每根柱是该垂域复核的单里，AI 根因经源码核对「成立」的比例 = 成立 ÷（成立 + 存疑 + 不成立）。「存疑」不折半。'
           '结论没引用可核对代码的单判「无法核对」，不进分母；' if SRC_MODE else
           '每根柱是该垂域抽样的 5 单里，AI 结论与人工依据相符的比例 =（一致 + 0.5 × 部分一致）÷ 可判定数。'
           '没有人工依据的单判「无法判定」，不进分母；')
    return ('<p class="legend"><b>怎么读：</b>' + how + '可判定不足 3 单就不出比例（灰色），不是 0%。'
            '颜色：<span class="lg ok">绿 ≥70%</span><span class="lg warn">橙 50–69%</span><span class="lg bad">红 &lt;50%</span><span class="lg na">灰 样本不足</span>。'
            '这是抽样复核，不是全量准确率，也不是用户反馈算出的「准确率」。</p><div class="bars wide">' + ''.join(out) + '</div>')


acc_bars = review_chart()

_n = [0]


def bulb(title):
    """💡 按钮 + 说明浮层. 没有登记说明的看板不显示 (宁缺毋滥, 说明必须和口径一致)."""
    d = DOC.get(title)
    if not d:
        return ''
    _n[0] += 1
    tid = f'tip{_n[0]}'
    meaning, rule, src = d
    return (f'<span class="bw"><button type="button" class="bulb" aria-label="指标说明：{html.escape(title)}" aria-expanded="false" aria-controls="{tid}">💡</button>'
            f'<span class="tip" id="{tid}" role="tooltip"><b>含义</b>{html.escape(meaning)}<b>怎么算</b>{html.escape(rule)}<b>数据来源</b>{html.escape(src)}</span></span>')


css = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1f2329;--mut:#646a73;--line:#e4e6eb;--ok:#2ea121;--warn:#de7802;--bad:#d83931;--acc:#3370ff}
*{box-sizing:border-box}body{margin:0;font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
header{background:#1f2329;color:#fff;padding:28px 40px}header h1{margin:0 0 6px;font-size:24px}header p{margin:0;color:#c9cdd4;font-size:13px}
nav{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:8px 40px;z-index:5}nav a{margin-right:18px;color:var(--acc);text-decoration:none;font-size:13px}
main{max-width:1320px;margin:0 auto;padding:24px 40px 60px}section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px 24px;margin-bottom:20px}
h2{margin:0 0 14px;font-size:18px}h3{margin:18px 0 8px;font-size:15px;color:#373c43}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px 16px}.kpi b{display:block;font-size:24px;margin:2px 0}.kpi span{color:var(--mut);font-size:12px}
ul.hl li{margin:6px 0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:24px}@media(max-width:960px){.grid2{grid-template-columns:1fr}main,header,nav{padding-left:16px;padding-right:16px}}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f2f3f5;font-weight:600;white-space:nowrap}tbody tr:hover{background:#f7f9ff}
.bars{display:flex;flex-direction:column;gap:6px}.bar{display:grid;grid-template-columns:150px 1fr 70px;align-items:center;gap:8px;font-size:13px}
.bt{background:#eff0f1;border-radius:4px;height:14px;overflow:hidden}.bf{display:block;height:100%}.bf.ok{background:var(--ok)}.bf.warn{background:var(--warn)}.bf.bad{background:var(--bad)}
.bv{text-align:right;font-variant-numeric:tabular-nums}.note{color:var(--mut);font-size:12px}
.card{border-left:4px solid var(--warn);background:#fffaf2;padding:10px 14px;margin:10px 0;border-radius:4px}.card.bad{border-color:var(--bad);background:#fff5f5}
.bars.wide .bar{grid-template-columns:150px 1fr 190px}.bd{margin:-2px 0 6px 158px;color:var(--mut);font-size:11px}
.bf.na{background:#c9cdd4}.legend{background:#f7f8fa;border-left:3px solid var(--acc);padding:8px 12px;margin:0 0 12px;font-size:12px;line-height:1.7}
.lg{display:inline-block;padding:0 6px;margin:0 4px 0 2px;border-radius:3px;color:#fff;font-size:11px}.lg.ok{background:var(--ok)}.lg.warn{background:var(--warn)}.lg.bad{background:var(--bad)}.lg.na{background:#8f959e}
.bw{position:relative;display:inline-block;margin-left:8px;vertical-align:middle}
.bulb{border:0;background:#fff7e0;color:#b26a00;border-radius:50%;width:26px;height:26px;font-size:15px;line-height:26px;padding:0;cursor:pointer;box-shadow:0 0 0 1px #f0d9a0}
.bulb:hover,.bulb:focus-visible,.bulb[aria-expanded=true]{background:#ffe9a8;outline:2px solid var(--acc);outline-offset:1px}
.tip{display:none;position:absolute;top:32px;right:0;width:340px;max-width:calc(100vw - 32px);z-index:20;background:#1f2329;color:#f2f3f5;font-size:12px;line-height:1.65;font-weight:400;padding:10px 14px;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.25);text-align:left;white-space:normal}
.kpi .tip,.kpi .bw{color:#f2f3f5;font-size:12px}
.tip b{display:block;color:#ffd666;margin:6px 0 1px;font-size:12px}.tip b:first-child{margin-top:0}
.bw.open .tip,.bw:hover .tip{display:block}
.kpi{position:relative}.kpi .bw{position:absolute;top:8px;right:8px;margin:0}
.hd{display:flex;align-items:center;justify-content:space-between;gap:12px}
.card b{display:block;margin-bottom:4px}a{color:var(--acc)}a.va{margin-left:2px;font-size:11px;text-decoration:none}ol li{margin:4px 0}
"""
P = []
P.append(f'<header><h1>{TITLE} · {W}</h1><p>vela-tools → VelaAgent 全链路 · 生成于 {m["generated_at"]} · 数据：bridge 台账 + bug_agents multica 原始数据重算 + Jira 实时状态</p></header>')
P.append('<nav aria-label="章节">' + ''.join(f'<a href="#{i}">{t}</a>' for i, t in [('s1', '结论'), ('s2', '分析量与耗时'), ('s3', '调用链检查'), ('s4', '执行问题'), ('s5', '准确率与反馈'), ('s6', '缺口与动作')]) + '</nav><main>')
P.append('<div class="kpis">' + ''.join(f'<div class="kpi">{bulb(a)}<span>{html.escape(a)}</span><b>{html.escape(str(b))}</b><span>{html.escape(c)}</span></div>' for a, b, c in KPI) + '</div>')
P.append('<section id="s1"><h2>一、本期结论</h2><ul class="hl">' + ''.join(f'<li>{html.escape(x)}</li>' for x in F['headline']) + '</ul></section>')
P.append('<section id="s2"><h2>二、分析量与耗时</h2><h3>入口 × 问题类型</h3>' + h_table(*T['volume']) + '<h3>耗时（按问题类型）</h3>' + h_table(*T['timing'])
         + f'<div class="grid2"><div><h3>各阶段耗时</h3>{h_table(*T["stage"])}</div><div><h3>各阶段 P90（分钟）</h3>{stage_bars}'
         + f'<p class="note">重跑 {m["retry"]["issues_with_rerun"]} 单 / {m["retry"]["reruns"]} 次：' + '、'.join(f'{a} {n}' for a, n in m['retry']['reasons']) + '</p></div></div></section>')
P.append('<section id="s3"><h2>三、调用链逐点检查（vela-tools → VelaAgent）</h2><div class="grid2"><div><h3>各检查点通过率</h3>' + cp_bars
         + '<p class="note">①–③ 在 VT 侧，④–⑦ 在 VelaAgent 侧，⑧ 由 VT poller 回写，⑨ 为用户反馈。④–⑥ 只统计走小队模式的单，coverity 走单 Agent 模式。</p></div><div><h3>流程不合规</h3>'
         + ''.join(f'<div class="card {"bad" if i < 2 else ""}"><b>{html.escape(p["title"])}</b>{html.escape(p["detail"])}<br>例：{" ".join(link(k) for k in p["examples"])}</div>' for i, p in enumerate(F['process']))
         + '</div></div>' + h_table(*T['cp']) + '</section>')
P.append('<section id="s4"><h2>四、执行问题（环境 / 代码下载 / 工具 / skill / MCP）</h2><div class="grid2"><div><h3>涉及问题数</h3>' + ex_bars + '</div><div>'
         + ''.join(f'<p><b>{c}</b>：{html.escape(F["exec_notes"][c])}</p>' for c in EXEC_ORDER) + '</div></div>' + h_table(*T['exec'])
         + f'<div class="grid2"><div><h3>MCP 调用</h3>{h_table(*T["mcp"])}</div><div><h3>skill 使用 Top</h3>'
         + bars([(k, n, 'ok') for k, n in m['usage']['skill_top'][:10]]) + f'<p class="note">skill 覆盖率 {m["usage"]["skill_cover"]}%（分母：有工具调用的问题）</p></div></div>'
         + '<p class="note">报错类别是对 tool_result 文本做规则推断得出的，属于「疑似」，例子可以点链接到 VelaAgent 核对。</p></section>')
P.append('<section id="s5"><h2>五、准确率与反馈（按垂域）</h2>' + h_table(*T['domain'])
         + f'<div class="grid2"><div><h3>{RV_CHART}</h3>{acc_bars}<p class="note">{html.escape(F["review_note"])}</p></div>'
         + f'<div><h3>本周新增反馈记录（按问题类型）</h3>{h_table(*T["fbcat"])}<p class="note">ai_feedback_records 中本周新建的记录，多数是以前的 circle 单。上面的垂域表取的是本周问题的反馈，包括 VelaAgent 评论里的反馈。</p></div></div>'
         + '<h3>AI 复核明细</h3>' + h_table(*T['review']) + '<h3>低分反馈</h3>' + h_table(*T['lowfb']) + '<h3>已回写但无反馈（催反馈清单）</h3>' + h_table(*T['nofb']) + '</section>')
P.append('<section id="s6"><h2>六、数据缺口与建议动作</h2><div class="grid2"><div><h3>数据缺口</h3><ul>' + ''.join(f'<li>{html.escape(x)}</li>' for x in F['gaps'])
         + '</ul></div><div><h3>建议动作</h3><ol>' + ''.join(f'<li>{html.escape(x)}</li>' for x in F['actions']) + '</ol></div></div></section></main>')
JS = """<script>
(function(){var open=null;
function close(){if(open){open.classList.remove('open');open.querySelector('.bulb').setAttribute('aria-expanded','false');open=null}}
function place(w){var t=w.querySelector('.tip');t.style.transform='';var r=t.getBoundingClientRect(),dx=0;
 if(r.left<8)dx=8-r.left;else if(r.right>innerWidth-8)dx=innerWidth-8-r.right;if(dx)t.style.transform='translateX('+dx+'px)'}
document.addEventListener('click',function(e){var b=e.target.closest('.bulb');
 if(!b){if(!e.target.closest('.tip'))close();return}
 var w=b.parentNode,was=w===open;close();if(!was){w.classList.add('open');b.setAttribute('aria-expanded','true');open=w;place(w)}});
document.querySelectorAll('.bw').forEach(function(w){w.addEventListener('mouseenter',function(){place(w)})});
document.addEventListener('keydown',function(e){if(e.key==='Escape')close()});
})();
</script>"""
body = ''.join(P)
for tag in ('h2', 'h3'):
    for title in DOC:
        body = body.replace(f'<{tag}>{html.escape(title)}</{tag}>', f'<{tag} class="hd"><span>{html.escape(title)}</span>{bulb(title)}</{tag}>')
open(f'{OUT}/report.html', 'w').write(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{TITLE} {W}</title><style>{css}</style></head><body>{body}{JS}</body></html>')
print('written', OUT)
