"""命令超时类问题 (analyze.py 的「命令超时」细分) 逐单清单 → MD, 带命令类型 / 节点 / agent.
命令类型是对 cmd 文本的规则判断: 含 find / grep -r / rg 的算「扫源码树」, 其余算「其他命令」; 退出码 143/144/124 但没有 "timed out after 2m" 的算「被终止」.
cmd 在 errors_json 里被截断, 所以「扫源码树」是下限.
用法: AIW_WORK=... AIW_START=.. AIW_END=.. python timeout_issues.py <输出.md>   (需 runtimes.json / raw/)
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import glob, json, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A
from load import bridge

SUBNAME = '命令超时 (2min 上限 / 全树 find/grep)'
rt = {r['id']: r['name'] for r in json.load(open(f'{AIW}/runtimes.json'))}
tnode = {}
for f in glob.glob(f'{AIW}/raw/*.json'):
    for r in json.load(open(f))['runs']:
        tnode[r['id']] = rt.get(r['runtime_id'], '?')
srv = {b['jira_key']: b['jira_server'] for b in bridge}
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
rows = {r['key']: r for r in A.rows}
SEARCH = re.compile(r'\bfind\b|grep\s+-[a-zA-Z]*[rR]|\brg\b|\bgrep\s+--recursive')
ev = []
for r in A.rows:
    for e in r['errs']:
        if A.classify(e) == '环境' and A.sub_of('环境', e) == SUBNAME:
            c = e.get('cmd') or ''; o = e.get('snippet') or ''
            two = bool(re.search(r'timed out after 2m', o))
            kind = '扫源码树 (find / grep -r)' if SEARCH.search(c) else ('其他命令' if two else '被终止 (143/144/124，无 2 分钟超时提示)')
            ev.append(dict(k=r['key'], kind=kind, node=tnode.get(e.get('task_id'), '?'), agent=e.get('agent') or '?', cmd=re.sub(r'\s+', ' ', c)[:100]))

by = defaultdict(list)
for e in ev:
    by[e['k']].append(e)
KINDS = ['扫源码树 (find / grep -r)', '其他命令', '被终止 (143/144/124，无 2 分钟超时提示)']
o = ['# 命令超时类问题：逐单清单', '',
     f"> 窗口 {os.environ.get('AIW_START', '')} ~ {os.environ.get('AIW_END', '')}。「命令超时」= 命令输出里有 `Command timed out after 2m 0s`，或以退出码 143 / 144 / 124 结束。", '',
     '## 结论', '',
     f"- 共 **{len(by)} 单**、{len(ev)} 次命令超时。**不是每次都是全树 find/grep**：下面按命令内容分类，扫源码树的有 {len({e['k'] for e in ev if e['kind'] == KINDS[0]})} 单（这是下限，命令文本被截断），其余是别的命令超时。"]
for kd in KINDS:
    L = [e for e in ev if e['kind'] == kd]
    o.append(f"  - {kd}：{len({e['k'] for e in L})} 单 / {len(L)} 次")
o.append('- 按节点（次数）：' + '、'.join(f'{n} {c}' for n, c in Counter(e['node'] for e in ev).most_common()))
o.append('- 按 agent（次数）：' + '、'.join(f'{n} {c}' for n, c in Counter(e['agent'] for e in ev).most_common(6)))
o += ['', '## 命令样例', '']
for kd in KINDS:
    for e in [x for x in ev if x['kind'] == kd][:3]:
        o.append(f"- {kd} · {e['node']} · {e['agent']} · {e['k']}：`{e['cmd']}`")
o += ['', f'## 全部 {len(by)} 单', '', '| Jira | VelaAgent | 入口 | 超时次数 | 命令类型 | 节点 | agent |', '|---|---|---|---|---|---|---|']
for k, L in sorted(by.items()):
    r = rows[k]
    o.append(f"| [{k}]({JIRA[srv[k]]}{k}) | [VelaAgent]({r['url'] or ''}) | {r['entry']} | {len(L)} | "
             f"{'、'.join(sorted({e['kind'].split(' ')[0] for e in L}))} | {'、'.join(sorted({e['node'].replace('Claude ', '') for e in L}))} | {'、'.join(sorted({e['agent'] for e in L}))} |")
open(sys.argv[1], 'w').write('\n'.join(o) + '\n')
print(len(by), len(ev), Counter(e['kind'] for e in ev), {k: len({e['k'] for e in ev if e['kind'] == k}) for k in KINDS})
