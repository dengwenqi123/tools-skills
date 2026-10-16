"""周报指标计算: bridge(台账) + summary(线上采集 ∪ me 原始数据补算) + 反馈 + Jira 实时状态 → metrics.json."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import bridge, summ, fb, J, tab, gap

WIN = (os.environ['AIW_START'], os.environ['AIW_END'])   # 含两端, 按 bridge.created_at
CAT = {'crash': '稳定性', 'coverity': '代码质量', 'circle': '全闭环', 'testcase': '功能性', 'ct_failure': '功能性'}
cat_of = lambda t: CAT.get((t or '').lower(), '其他')
jira = json.load(open(f'{AIW}/db/jira_live.json'))
errs = {r['jira_key']: J(r['errors_json'], []) or [] for r in tab('errors')}
for s in gap:
    errs[s['jira_key']] = J(s.get('errors_json'), []) or []


def pq(v, q):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def mins(x):
    return round(x / 60, 1) if x is not None else None


def ts(v):
    return datetime.fromisoformat(str(v).replace('Z', '')[:19]) if v else None


# 反馈: 每个 issue 取最新一条
fb_by = {}
for f in sorted(fb, key=lambda x: str(x['created_at'])):
    if f['feedback'] in ('useful', 'partial_useful', 'not_useful'):
        fb_by[f['issue_key']] = dict(f, _src='vt')
mfb = json.load(open(f'{AIW}/db/multica_fb.json'))   # VelaAgent 评论里的反馈 (未同步进 ai_feedback_records)
fb_only_multica = [k for k in mfb if k not in fb_by]
for k, f in mfb.items():
    fb_by.setdefault(k, f)
fbweek = tab('fbweek')

rows = []
for b in bridge:
    s = summ.get(b['jira_key'])
    st = (J(s['stage_json'], {}) or {}) if s else {}
    j = jira.get(b['jira_key'], {})
    single = b['module'] == 'jql' and b['jira_type'] == 'coverity'
    # 直派 Agent: 路由配置 (multica_analysis_prompt) 把某类单直接指派给某个 solver (如 testcase → vtf-solver),
    # 不经 intake / router。识别: 首个 task 就是 solver。这类单没有分诊是设计如此, 垂域取该 solver 的垂域。
    spans = J((s or {}).get('spans_json'), []) or []
    first = next((x for x in spans if x.get('task_id')), {})
    direct = first.get('stage') == 'solve' and not single
    direct_dom = first.get('domain') if direct else None
    rows.append({
        'key': b['jira_key'], 'entry': b['module'], 'cat': cat_of(b['jira_type']), 'jira_type': b['jira_type'],
        'url': b['multica_issue_url'], 'server': b['jira_server'],
        'state': b['analysis_state'], 'skip': bool(b['skip_analysis']) and b['module'] != 'circle', 'circle': b['module'] == 'circle', 'single': single or direct, 'direct': direct,
        'pushed': b['push_status'] == 'pushed' and bool(b['multica_issue_id']), 'commented': bool(b['jira_commented']),
        'domain': (s or {}).get('domain') or direct_dom or ('单Agent(coverity)' if single else ('—' if b['module'] == 'circle' else '未路由')),
        'stage': st, 'has_s': bool(s), 'src': (s or {}).get('_src'),
        'trigger': (s or {}).get('trigger_mode'), 'final': (s or {}).get('final_outcome'), 'first_at': (s or {}).get('first_result_at'),
        't_first': (s or {}).get('t_first_result_s'), 't_e2e': (s or {}).get('t_e2e_s'), 't_queue': (s or {}).get('t_queue_s'),
        'tasks': (s or {}).get('task_count') or 0, 'skills': J((s or {}).get('skills_used'), {}) or {},
        'tools': J((s or {}).get('tool_err_json'), {}) or {}, 'tool_calls': (s or {}).get('tool_calls'),
        'labels': J((s or {}).get('err_label_json'), {}) or {}, 'errs': errs.get(b['jira_key'], []),
        'retry_hist': J(b['analysis_retry_history'], []) or [], 'triggered_at': b['triggered_at'], 'created_at': b['created_at'],
        'last_error': b['last_error'],
        'resolved': bool(j.get('resolution')) or j.get('category') == 'done', 'resolution': j.get('resolution'),
        'jstatus': j.get('status'), 'fb': fb_by.get(b['jira_key']),
        'tok': ((s or {}).get('tok_input') or 0) + ((s or {}).get('tok_output') or 0) if s and s.get('tok_input') is not None else None,
    })
need = [r for r in rows if not r['circle'] and not r['skip']]   # 期望主动分析的问题

# ───────── 1 分析量 (入口 × 类型) ─────────
vol = defaultdict(Counter)
for r in rows:
    k = (r['entry'], r['cat'])
    vol[k]['total'] += 1
    vol[k]['circle_only' if r['circle'] else ('skip' if r['skip'] else r['state'])] += 1
    vol[k]['commented'] += r['commented']
volume = [{'entry': e, 'cat': c, **v} for (e, c), v in sorted(vol.items(), key=lambda x: -x[1]['total'])]

# ───────── 2 耗时 ─────────
def timing(rs):
    ok = [r for r in rs if r['state'] == 'analyzed' and r['has_s']]
    fail = [r for r in rs if r['state'] == 'failed']
    fail_d = []
    for r in fail:
        h = [ts(x.get('at')) for x in r['retry_hist'] if x.get('at')]
        t0 = ts(r['triggered_at']) or ts(r['created_at'])
        if h and t0:
            fail_d.append((max(h) - t0).total_seconds())
    return {'n_ok': len(ok), 'first_p50': mins(pq([r['t_first'] for r in ok], .5)), 'first_p90': mins(pq([r['t_first'] for r in ok], .9)),
            'e2e_p50': mins(pq([r['t_e2e'] for r in ok], .5)), 'queue_p50': mins(pq([r['t_queue'] for r in rs if r['has_s']], .5)),
            'queue_p90': mins(pq([r['t_queue'] for r in rs if r['has_s']], .9)),
            'n_fail': len(fail), 'fail_avg': mins(statistics.mean(fail_d)) if fail_d else None,
            'tok_avg_k': round(statistics.mean([r['tok'] for r in ok if r['tok']]) / 1000) if any(r['tok'] for r in ok) else None}
timing_by_cat = {c: timing([r for r in need if r['cat'] == c]) for c in sorted({r['cat'] for r in need})}
timing_all = timing(need)
STAGES = ['push', 'queue', 'triage', 'route', 'solve', 'analyze', 'writeback']
stage_stat = {st: {'n': len(v := [r['stage'][st] for r in need if r['stage'].get(st) is not None]),
                   'avg': mins(statistics.mean(v)) if v else None, 'p50': mins(pq(v, .5)), 'p90': mins(pq(v, .9))} for st in STAGES}

# ───────── 3 链路检查点 ─────────
def cp(name, base, ok, fail_reason=None, note=''):
    b = [r for r in base]
    o = [r for r in b if ok(r)]
    bad = [r for r in b if not ok(r)]
    reasons = Counter(fail_reason(r) for r in bad) if fail_reason else Counter()
    return {'name': name, 'base': len(b), 'ok': len(o), 'bad': len(bad), 'rate': round(100 * len(o) / len(b), 1) if b else None,
            'reasons': reasons.most_common(6), 'examples': [r['key'] for r in bad[:5]], 'note': note}

squad = [r for r in need if not r['single']]
def intake_ok(r): return bool(r['stage'].get('triage') is not None or r['trigger'])
def routed(r): return r['domain'] not in ('未路由', '—')
def solved(r): return r['stage'].get('solve') is not None
def concluded(r): return bool(r['final']) or (r['single'] and r['state'] == 'analyzed')
def queue_reason(r):
    return '排队过期(task expired in queue)' if any('task expired' in k for k in r['labels']) else ('未采集到 task' if not r['has_s'] else '未接诊')
def route_reason(r):
    if not intake_ok(r): return '未接诊'
    if any('无路由结论' in k for k in r['labels']): return 'ROUTE REQUEST 未唤醒 router (无路由结论)'
    return 'intake 未发起分诊 / 直接出结论'
def final_reason(r):
    if r['state'] == 'failed':
        return '重试 4 次后判失败: ' + ('排队过期' if 'queued_expired' in (r['last_error'] or '') else '分析超时 180min')
    return r['state']
checkpoints = [
    cp('① 入口建单', rows, lambda r: bool(r['key'])),
    cp('② 台账落库', rows, lambda r: True, note=f"含 circle 仅总结 {sum(r['circle'] for r in rows)} 单、跳过分析 {sum(r['skip'] for r in rows)} 单"),
    cp('③ 推送 VelaAgent', rows, lambda r: r['pushed'], lambda r: 'push 失败'),
    cp('④ 小队接诊', squad, intake_ok, queue_reason, note='小队模式; coverity 单 Agent、按路由配置直派 solver 的单 (如 testcase → vtf-solver) 不计'),
    cp('⑤ 分诊出垂域', squad, routed, route_reason),
    cp('⑥ 求解分析', squad, solved, lambda r: '未分诊' if not routed(r) else '派单后 solver 未执行'),
    cp('⑦ 最终结论', need, lambda r: r['state'] == 'analyzed', final_reason, note=f"其中无 [FINAL] 标记 {sum(1 for r in need if r['state']=='analyzed' and not concluded(r))} 单"),
    cp('⑧ 回写 Jira', [r for r in need if r['state'] == 'analyzed'], lambda r: r['commented'], lambda r: f"未回写 ({r['server']})"),
    cp('⑨ 用户反馈', [r for r in rows if r['commented'] or r['circle']], lambda r: bool(r['fb']), lambda r: '无反馈', note='含 VelaAgent 侧评论反馈'),
]
retry = {'issues_with_rerun': sum(1 for r in need if r['retry_hist']), 'reruns': sum(len(r['retry_hist']) for r in need),
         'reasons': Counter(x.get('reason') for r in need for x in r['retry_hist']).most_common()}

# 流程合规异常 (逐单核对后归纳的 4 类)
def anomaly(r):
    if r['circle'] or r['skip'] or r['single']: return None
    st = r['stage']
    if r['state'] == 'analyzed' and st.get('triage') is None and not r['trigger']: return 'A 小队未接诊却判已分析'
    if r['state'] == 'analyzed' and st.get('route') is None and st.get('solve') is None: return 'B intake 越过分诊/求解自行出结论'
    if r['state'] == 'failed' and r['final']: return 'C 已出 FINAL 却被判失败(未回写)'
    if r['state'] == 'analyzed' and not r['commented']: return 'D 已分析未回写 Jira'
    return None
anom = defaultdict(list)
for r in need:
    a = anomaly(r)
    if a: anom[a].append(r['key'])
anomalies = {k: {'n': len(v), 'examples': v[:6], 'keys': v} for k, v in sorted(anom.items())}
flow_ok = sum(1 for r in squad if r['state'] == 'analyzed' and routed(r) and solved(r) and r['final'] and r['commented'])

# ───────── 4 执行问题 5 类 ─────────
SKILL_P = re.compile(r'\.claude/skills/|SKILL\.md')
def classify(e):
    lab, cmd, sn, tool = e.get('label') or '', e.get('cmd') or '', e.get('snippet') or e.get('message') or '', e.get('tool') or ''
    txt = cmd + '\n' + sn
    if e.get('layer') == 'flow': return None
    if 'task expired' in lab or e.get('layer') == 'platform': return '环境'
    if tool.startswith('mcp__') or re.search(r'McpError|MCP error|ListMcpResources', txt + tool): return 'MCP'
    if re.search(r'SKILL\.md[^\n]*(没有那个文件|No such file|not found)', sn): return 'skill'
    if re.search(r'curl: \(|attachment download|未找到请求的资源|归档中找不到|coredump|\.core\b|不是 git 仓库|^fatal: |git ', txt, re.M) \
            or lab in ('附件/日志下载失败', 'coredump/ELF 缺失', 'git 操作失败', '退出码 128'):
        return '代码下载'
    if re.search(r'权限不够|Permission denied|需要密码|timed out|未找到命令|command not found|已杀死|已终止', txt) \
            or lab in ('工作目录权限不足', '命令超时', '命令不存在') or re.match(r'退出码 (124|137|143|144|127|126)', lab):
        return '环境'
    return '工具'
SUB = {  # 每类的细分 (用于看板「具体问题」列)
    '环境': [('排队过期 task expired in queue', r'task expired'), ('命令超时 (2min 上限 / 全树 find/grep)', r'timed out|Exit code 14[34]|Exit code 124|已终止'),
            ('agent-files 目录权限不足 (NFS ACL)', r'agent-files.*(权限不够|Permission)|sudo: 需要密码'),
            ('经验库目录只读', r'experience-base'), ('工具链缺失 (addr2line / gdb-multiarch)', r'未找到命令|not found|Exit code 127'),
            ('平台 API Error', r'API Error')],
    '代码下载': [('源码树非 git 仓库 / git 历史不可用', r'不是 git 仓库|fatal:|Exit code 128'), ('附件下载失败 (multica attachment)', r'未找到请求的资源|attachment'),
              ('日志/coredump 下载或解包失败', r'curl|归档中找不到|coredump|\.core')],
    'skill': [('skill 文件不存在 (SKILL.md 缺失)', r'SKILL\.md')],
    'MCP': [('mi-jira MCP jira_get_issue 失败', r'mi-jira'), ('gdbmcp 连接失败', r'gdbmcp'), ('MCP 资源枚举', r'ListMcpResources')],
    '工具': [('multica CLI 无权限 / issue 未指派小队', r'无权访问该资源|not assigned to a squad'), ('agent 自写 Python 解析报错', r'Traceback|SyntaxError|File "<st'),
            ('Claude Code 工具误用 (tool_use_error)', r'tool_use_error'), ('--content-file 文件不存在', r'content-file'), ('其他命令失败', r'.')],
}
def sub_of(cat, e):
    txt = (e.get('label') or '') + '\n' + (e.get('cmd') or '') + '\n' + (e.get('snippet') or e.get('message') or '') + '\n' + (e.get('tool') or '')
    for name, p in SUB.get(cat, []):
        if re.search(p, txt): return name
    return '其他'
issue_cat = defaultdict(lambda: {'issues': set(), 'events': 0, 'sub': defaultdict(lambda: {'issues': set(), 'events': 0, 'ex': []})})
for r in rows:
    if r['circle']: continue
    for e in r['errs']:
        c = classify(e)
        if not c: continue
        sname = sub_of(c, e)
        issue_cat[c]['issues'].add(r['key']); issue_cat[c]['events'] += 1
        sub = issue_cat[c]['sub'][sname]; sub['issues'].add(r['key']); sub['events'] += 1
        if len(sub['ex']) < 2:
            sub['ex'].append({'key': r['key'], 'agent': e.get('agent'), 'cmd': (e.get('cmd') or '')[:140], 'snippet': (e.get('snippet') or e.get('message') or '')[:200]})
# 采集未覆盖 errors_json 的平台排队过期 (label 统计) 也计入环境
for r in rows:
    if not r['circle'] and any('task expired' in k for k in r['labels']):
        issue_cat['环境']['issues'].add(r['key']); issue_cat['环境']['sub']['排队过期 task expired in queue']['issues'].add(r['key'])
exec_issues = {c: {'issues': len(v['issues']), 'events': v['events'],
                   'sub': sorted([{'name': n, 'issues': len(s['issues']), 'events': s['events'], 'ex': s['ex']} for n, s in v['sub'].items()],
                                 key=lambda x: -x['issues'])}
               for c, v in issue_cat.items()}
# skill / MCP 使用面 (非报错视角)
with_tools = [r for r in need if r['tool_calls']]
skill_use = Counter(k for r in need for k in r['skills'])
mcp_use = Counter(); mcp_err = Counter()
for r in need:
    for t, v in r['tools'].items():
        if t.startswith('mcp__') or 'Mcp' in t:
            mcp_use[t] += v.get('calls') or 0; mcp_err[t] += v.get('errors') or 0
usage = {'skill_cover': round(100 * sum(1 for r in with_tools if r['skills']) / len(with_tools), 1) if with_tools else None,
         'no_skill': [r['key'] for r in with_tools if not r['skills']][:10], 'no_skill_n': sum(1 for r in with_tools if not r['skills']),
         'skill_top': skill_use.most_common(12), 'mcp': [(t, mcp_use[t], mcp_err[t]) for t, _ in mcp_use.most_common()],
         'jira_mcp_setup_reads': skill_use.get('jira-mcp-setup', 0)}

# ───────── 5 准确率 (反馈口径) / 反馈 / 已解决 按垂域 ─────────
def acc(rs):
    f = [r['fb'] for r in rs if r['fb']]
    if len(f) < 5: return None, len(f)
    v = sum(1 if x['feedback'] == 'useful' else ((x['partial_score'] if x['partial_score'] is not None else 5) / 10 if x['feedback'] == 'partial_useful' else 0) for x in f)
    return round(100 * v / len(f), 1), len(f)
by_dom = defaultdict(list)
for r in rows:
    by_dom[('仅总结(circle)' if r['circle'] else r['domain'])].append(r)
fb_cat = defaultdict(list)
for f in fbweek:
    fb_cat[cat_of(f['jira_type'])].append(f)
domain_tbl = []
for d, rs in sorted(by_dom.items(), key=lambda x: -len(x[1])):
    a, n = acc(rs)
    an = [r for r in rs if r['commented'] or r['circle']]
    domain_tbl.append({'domain': d, 'total': len(rs), 'analyzed': sum(r['state'] == 'analyzed' for r in rs), 'failed': sum(r['state'] == 'failed' for r in rs),
                       'commented': sum(r['commented'] for r in rs), 'fb': n, 'fb_cover': round(100 * n / len(an), 1) if an else None,
                       'useful': sum(1 for r in rs if r['fb'] and r['fb']['feedback'] == 'useful'),
                       'partial': sum(1 for r in rs if r['fb'] and r['fb']['feedback'] == 'partial_useful'),
                       'useless': sum(1 for r in rs if r['fb'] and r['fb']['feedback'] == 'not_useful'), 'acc': a,
                       'resolved': sum(r['resolved'] for r in rs), 'fixed': sum(1 for r in rs if r['resolution'] in ('Fixed', '已修复')),
                       'first_p50': mins(pq([r['t_first'] for r in rs if r['state'] == 'analyzed'], .5)),
                       'no_fb_list': [r['key'] for r in rs if r['commented'] and not r['fb']][:10],
                       'no_fb_n': sum(1 for r in rs if r['commented'] and not r['fb'])})
low_fb = [{'key': r['key'], 'domain': r['domain'] if not r['circle'] else '仅总结(circle)', 'fb': r['fb']['feedback'], 'score': r['fb']['partial_score'],
           'reason': ' / '.join(x for x in (r['fb']['reason_l1'], r['fb']['reason_l2']) if x), 'detail': (r['fb']['reason_detail'] or '')[:160],
           'user': r['fb']['user_name']} for r in rows if r['fb'] and r['fb']['feedback'] != 'useful']
fb_week = Counter(f['feedback'] for f in fb if f['created_at'] and WIN[0] <= str(f['created_at'])[:10] <= WIN[1])

out = {'window': WIN, 'generated_at': datetime.now().isoformat(timespec='minutes'),
       'data_note': {'bridge': len(rows), 'summary_online': sum(1 for r in rows if r['src'] == 'online'),
                     'summary_me_raw': sum(1 for r in rows if r['src'] == 'me_raw'), 'no_summary': sum(1 for r in rows if not r['has_s'])},
       'overview': {'total': len(rows), 'need': len(need), 'circle': sum(r['circle'] for r in rows), 'skip': sum(r['skip'] for r in rows),
                    'analyzed': sum(r['state'] == 'analyzed' for r in need), 'failed': sum(r['state'] == 'failed' for r in need),
                    'commented': sum(r['commented'] for r in need), 'flow_ok': flow_ok, 'squad': len(squad),
                    'fb_issues': sum(1 for r in rows if r['fb']), 'fb_week': dict(fb_week), 'resolved': sum(r['resolved'] for r in rows),
                    'fixed': sum(1 for r in rows if r['resolution'] in ('Fixed', '已修复'))},
       'volume': volume, 'timing_all': timing_all, 'timing_by_cat': timing_by_cat, 'stage': stage_stat,
       'checkpoints': checkpoints, 'retry': retry, 'anomalies': anomalies, 'exec_issues': exec_issues, 'usage': usage,
       'domains': domain_tbl, 'low_fb': low_fb,
       'fb_sources': {'vt_table': sum(1 for r in rows if r['fb'] and r['fb'].get('_src') == 'vt'), 'multica_only': len([k for k in fb_only_multica if k in {r['key'] for r in rows}])},
       'fbweek_by_cat': {c: {'n': len(L), 'useful': sum(x['feedback'] == 'useful' for x in L), 'partial': sum(x['feedback'] == 'partial_useful' for x in L),
                             'useless': sum(x['feedback'] == 'not_useful' for x in L),
                             'acc': round(100 * sum(1 if x['feedback'] == 'useful' else ((x['partial_score'] or 5) / 10 if x['feedback'] == 'partial_useful' else 0) for x in L) / len(L), 1) if len(L) >= 5 else None}
                         for c, L in fb_cat.items()}}
json.dump(out, open(f'{AIW}/metrics.json', 'w'), ensure_ascii=False, indent=1, default=str)
json.dump([{k: r[k] for k in ('key', 'entry', 'cat', 'domain', 'state', 'resolved', 'resolution', 'commented', 'url', 'single', 'circle', 'final', 'direct')}
           | {'fb': (r['fb'] or {}).get('feedback')} for r in rows], open(f'{AIW}/rows.json', 'w'), ensure_ascii=False, default=str)
if __name__ == '__main__':
    o = out
    print(json.dumps({k: o[k] for k in ('data_note', 'overview', 'timing_all', 'retry', 'anomalies')}, ensure_ascii=False, default=str))
    for c in checkpoints: print(c['name'], c['base'], c['ok'], c['rate'], c['reasons'][:3])
    for c, v in exec_issues.items(): print(c, v['issues'], v['events'], [(s['name'], s['issues']) for s in v['sub']])
    print(usage['skill_cover'], usage['no_skill_n'], usage['mcp'], usage['jira_mcp_setup_reads'])
    for d in domain_tbl: print(d['domain'], d['total'], d['analyzed'], d['fb'], d['acc'], d['resolved'], d['fixed'], d['first_p50'])
    print(timing_by_cat); print(stage_stat)
