"""把流程异常（B: intake 越过分诊/求解；C: 已出 FINAL 却被判失败）全量列成 MD, 带时间线, 供研发分析.
用法: AIW_WORK=... python anomaly_list.py <输出.md>   (需先跑 analyze.py)
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, re, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import bridge, J

m = json.load(open(f'{AIW}/metrics.json'))
rows = {r['key']: r for r in json.load(open(f'{AIW}/rows.json'))}
bk = {b['jira_key']: b for b in bridge}
ag = {a['id']: a['name'] for a in json.load(open(f'{AIW}/agents.json'))}
MK = re.compile(r'(?m)^[\s>*#]*\[(ROUTE REQUEST|ROUTING_DECISION|ROUTE DECISION|SOLVE REQUEST|ANALYSIS RESULT|FINAL|DISTILL REQUEST|DISTILL RESULT)\s*#(\d+)\]')
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
t = lambda v: (v or '')[5:16].replace('T', ' ')


def timeline(k):
    raw = json.load(open(f'{AIW}/raw/{k}.json'))
    ev = []
    for c in raw['comments']:
        body = c['content'] or ''
        mk = MK.search(body)
        who = ag.get(c['author_id'], c['author_type'])
        if mk:
            ev.append((c['created_at'], f"{who}: [{mk.group(1)} #{mk.group(2)}]"))
        elif c['author_type'] == 'agent' and not body.startswith('[@') and len(body) > 200:
            ev.append((c['created_at'], f"{who}: 无 marker 的长评论（{len(body)} 字，疑似直接结论）"))
        elif c['author_type'] == 'member' and '[vela 自动分析]' in body:
            ev.append((c['created_at'], 'VT: 「请继续」触发评论'))
    for r in raw['runs']:
        ev.append((r['created_at'], f"run {ag.get(r['agent_id'], r['agent_id'][:8])} {r['status']}" + (f" ({r['error']})" if r.get('error') else '')))
    ev.sort()
    mentions_router = any('mention://agent/c88fd942' in (c['content'] or '') for c in raw['comments'])
    first_route_req = next((c['created_at'] for c in raw['comments'] if 'ROUTE REQUEST' in (c['content'] or '')[:40]), None)
    return ev, mentions_router, first_route_req, raw


def head(r, b):
    jira = f"[{r['key']}]({JIRA[b['jira_server']]}{r['key']})"
    va = f"[VelaAgent]({r.get('url') or ''})"
    return f"{jira} · {va} · {r['entry']}/{r['cat']} · 垂域 {r['domain']}"


out = ['# W42 流程异常明细（供研发分析）', '',
       f"> 窗口 {m['window'][0]} ~ {m['window'][1]}；数据同 report.md。时间为 CST，格式 MM-DD HH:MM。", '']
B = m['anomalies']['B intake 越过分诊/求解自行出结论']['keys']
C = m['anomalies']['C 已出 FINAL 却被判失败(未回写)']['keys']

# ---- B ----
rows_b = []
for k in B:
    ev, ment, frr, raw = timeline(k)
    kinds = Counter(re.match(r'.*?: \[(.*?) #', e[1]).group(1) for e in ev if re.match(r'.*?: \[(.*?) #', e[1]))
    n_cont = sum(1 for e in ev if '请继续' in e[1])
    rr = kinds.get('ROUTE REQUEST', 0); rd = kinds.get('ROUTING_DECISION', 0) + kinds.get('ROUTE DECISION', 0)
    sr = kinds.get('SOLVE REQUEST', 0); ar = kinds.get('ANALYSIS RESULT', 0); fi = kinds.get('FINAL', 0)
    runs = Counter(re.match(r'run (\S+) (\w+)', e[1]).group(1) for e in ev if e[1].startswith('run '))
    if rr and not rd: pattern = 'B1 发了 ROUTE REQUEST 但 router 未产出 DECISION'
    elif rd and sr and not ar: pattern = 'B2 已派 SOLVE REQUEST 但 solver 无 ANALYSIS RESULT'
    elif not rr: pattern = 'B3 没发 ROUTE REQUEST 直接出结论'
    else: pattern = 'B4 其他'
    rows_b.append((k, pattern, rr, rd, sr, ar, fi, n_cont, ment, dict(runs), ev, bk[k]))
pc = Counter(x[1] for x in rows_b)
out += [f'## 一、intake 越过分诊 / 求解，自行出结论（{len(B)} 单）', '',
        '判定：状态 analyzed，stage 中没有 route 和 solve，即没有 router / solver 的 task。', '', '细分模式：', '']
out += [f'- {p}：{n} 单' for p, n in sorted(pc.items())]
ec = Counter((x[1][:2], rows[x[0]]['entry'], rows[x[0]]['cat']) for x in rows_b)
out += ['', '按入口 × 类型：', ''] + [f'- {p} · {e}/{c}：{n} 单' for (p, e, c), n in sorted(ec.items())]
out += ['', '| Jira | 模式 | ROUTE REQ | DECISION | SOLVE REQ | ANALYSIS | FINAL | VT「请继续」次数 | 是否 @router | 出现的 run | 回写 |', '|---|---|---|---|---|---|---|---|---|---|---|']
for k, pat, rr, rd, sr, ar, fi, nc, ment, runs, ev, b in rows_b:
    r = rows[k]
    out.append(f"| {head(r, b).split(' · ')[0]} | {pat[:2]} | {rr} | {rd} | {sr} | {ar} | {fi} | {nc} | {'是' if ment else '否'} | {', '.join(f'{a}×{n}' for a, n in runs.items())} | {'是' if r['commented'] else '否'} |")
out += ['', '### 逐单时间线', '']
for k, pat, rr, rd, sr, ar, fi, nc, ment, runs, ev, b in rows_b:
    r = rows[k]
    out += [f"#### {head(r, b)}", f"- 模式：{pat}；重试 {b['analysis_retry_count']} 次；最终 {b['analysis_state']}" + (f"；last_error：{b['last_error']}" if b['last_error'] else ''), '- 时间线：']
    out += [f"  - {t(a)} {x}" for a, x in ev]
    out.append('')

# ---- C ----
out += [f'## 二、已出 FINAL 却被判失败，结论没有回写（{len(C)} 单）', '',
        '判定：bridge.analysis_state=failed，但评论区已有 [FINAL]。「判失败时间」取 analysis_retry_history 最后一次重试时间；「FINAL 晚了」= FINAL 时间 − 判失败时间。', '',
        '| Jira | 入口 | 重试 | 末次原因 | 触发 | 判失败 | FINAL | FINAL 晚了 | 最后 run 状态 |', '|---|---|---|---|---|---|---|---|---|']
det = []
for k in C:
    ev, ment, frr, raw = timeline(k)
    b = bk[k]; r = rows[k]
    h = [x.get('at') for x in (J(b['analysis_retry_history'], []) or []) if x.get('at')]
    failed_at = max(h) if h else None
    fin = [c['created_at'] for c in raw['comments'] if re.search(r'(?m)^[\s>*#]*\[FINAL\s*#', c['content'] or '')]
    fin_at = max(fin) if fin else None
    late = ''
    if failed_at and fin_at:
        from datetime import datetime
        d = (datetime.fromisoformat(fin_at[:19]) - datetime.fromisoformat(failed_at[:19])).total_seconds() / 3600
        late = f"{d:+.1f} h"
    last_run = raw['runs'][0]['status'] if raw['runs'] else '—'
    reason = (b['last_error'] or '').split('末次原因=')[-1]
    out.append(f"| {head(r, b).split(' · ')[0]} | {r['entry']} | {b['analysis_retry_count']} | {reason} | {t(str(b['triggered_at']).replace(' ', 'T'))} | {t(failed_at)} | {t(fin_at)} | {late} | {last_run} |")
    det.append((k, ev, b, r))
out += ['', '### 逐单时间线', '']
for k, ev, b, r in det:
    out += [f"#### {head(r, b)}", f"- 重试历史：" + '；'.join(f"{t(x.get('at'))} {x.get('reason')}" for x in (J(b['analysis_retry_history'], []) or [])), '- 时间线：']
    out += [f"  - {t(a)} {x}" for a, x in ev]
    out.append('')
open(sys.argv[1], 'w').write('\n'.join(out) + '\n')
print('written', sys.argv[1], 'B', len(B), 'C', len(C), dict(pc))
