"""列出窗口内 agent 调用指定 MCP 工具的全部 issue (从 raw run-messages 扫描), 输出 MD.
用法: AIW_WORK=... python mcp_calls.py <输出.md> [工具名前缀,...]   (默认三个 Jira 取单 MCP)
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import glob, json, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import bridge

TOOLS = tuple(sys.argv[2].split(',')) if len(sys.argv) > 2 else (
    'mcp__mi-jira-phone__jira_get_issue', 'mcp__mi-jira__jira_get_issue', 'mcp__dfx-mi-jira__getJiraIssue')
ag = {a['id']: a['name'] for a in json.load(open(f'{AIW}/agents.json'))}
bk = {b['jira_key']: b for b in bridge}
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
calls = []
for f in sorted(glob.glob(f'{AIW}/raw/*.json')):
    d = json.load(open(f)); runs = {r['id']: r for r in d['runs']}
    for tid, msgs in d['messages'].items():
        for i, m in enumerate(msgs):
            if m.get('type') != 'tool_use' or (m.get('tool') or '').split()[0] not in TOOLS:
                continue
            res = next((x for x in msgs[i + 1:i + 4] if x.get('type') == 'tool_result' and x.get('tool') == m['tool']), {})
            nxt = next((x for x in msgs[i + 1:] if x.get('type') == 'tool_use' and x.get('tool') != m['tool']), {})
            inp = m.get('input') or {}
            r = runs.get(tid, {}); b = bk.get(d['jira_key'], {})
            calls.append({'issue': d['jira_key'], 'tool': m['tool'].split()[0], 'queried': inp.get('issue_key') or inp.get('issueKeyOrId') or json.dumps(inp, ensure_ascii=False),
                          'agent': ag.get(r.get('agent_id'), '?'), 'at': (r.get('created_at') or '')[5:16].replace('T', ' '), 'seq': m['seq'], 'task': tid[:8],
                          'err': re.sub(r'\s+', ' ', (res.get('output') or '(无返回)')).strip('"')[:140],
                          'next': (nxt.get('tool') or '—'), 'module': b.get('module'), 'server': b.get('jira_server', 'n'), 'url': b.get('multica_issue_url') or ''})
json.dump(calls, open(f'{AIW}/mcp_jira_calls.json', 'w'), ensure_ascii=False, indent=1)
lk = lambda c: f"[{c['issue']}]({JIRA[c['server']]}{c['issue']}) · [VelaAgent]({c['url']})"
fail = [c for c in calls if 'tool_use_error' in c['err'] or 'No such tool' in c['err']]
o = [f"# Jira 取单 MCP 调用明细", '', f"> 窗口数据同 report.md；共 {len(calls)} 次调用，涉及 {len({c['issue'] for c in calls})} 单；失败 {len(fail)} 次。", '',
     '## 一、按工具汇总', '', '| 工具 | 调用 | 失败 | 涉及单数 | 错误信息 |', '|---|---|---|---|---|']
for t in TOOLS:
    L = [c for c in calls if c['tool'] == t]
    o.append(f"| `{t}` | {len(L)} | {sum(c in fail for c in L)} | {len({c['issue'] for c in L})} | {'；'.join(sorted({c['err'][:80] for c in L}))} |")
o += ['', '## 二、按 agent / 入口', '', '- agent：' + '、'.join(f'{a} {n}' for a, n in Counter(c['agent'] for c in calls).most_common()),
      '- 入口：' + '、'.join(f'{a} {n}' for a, n in Counter(c['module'] for c in calls).most_common()),
      '- 失败后的下一步工具：' + '、'.join(f'{a} {n}' for a, n in Counter(c['next'] for c in calls).most_common()), '',
      '## 三、逐次调用', '', '| Jira / VelaAgent | 工具 | 查询的 key | agent | run 创建 | task | seq | 返回 | 失败后下一步 |', '|---|---|---|---|---|---|---|---|---|']
for c in sorted(calls, key=lambda x: (x['issue'], x['seq'])):
    o.append(f"| {lk(c)} | `{c['tool'].replace('mcp__', '')}` | {c['queried']}{'（≠本单）' if c['queried'] != c['issue'] else ''} | {c['agent']} | {c['at']} | {c['task']} | {c['seq']} | {c['err']} | {c['next']} |")
open(sys.argv[1], 'w').write('\n'.join(o) + '\n')
print('written', sys.argv[1], len(calls), 'calls', len({c['issue'] for c in calls}), 'issues')
