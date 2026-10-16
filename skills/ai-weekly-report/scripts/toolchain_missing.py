"""分析工具链缺失: 哪些 issue、在哪些 runtime 节点上、缺哪些二进制 / 共享库 → MD.
数据: raw/*.json (run-messages, run.runtime_id) + runtimes.json (me 上 `multica runtime list --output json`).
用法: AIW_WORK=... python toolchain_missing.py <输出.md>
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import glob, json, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import bridge

rt = {r['id']: r['name'] for r in json.load(open(f'{AIW}/runtimes.json'))}
ag = {a['id']: a['name'] for a in json.load(open(f'{AIW}/agents.json'))}
bk = {b['jira_key']: b for b in bridge}
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
BIN = r'((?:arm|aarch64|riscv\d*|x86_64)[\w]*-[\w-]*?(?:gdb|addr2line|objdump|readelf|nm|gcc|strings|size)|gdb-multiarch|(?<![\w-])addr2line|(?<![\w-])objdump|(?<![\w-])readelf|llvm-[a-z0-9]+)'
PAT2 = re.compile(r'(?:行 \d+: |bash: |sh: )' + BIN + r': (?:未找到命令|command not found)')
PAT3 = re.compile(BIN + r' not found')
LIB = re.compile(r'(lib[\w.+-]+\.so[\w.]*):? => not found|error while loading shared libraries: (lib[\w.+-]+\.so[\w.]*)')
PROBE = re.compile(r'which |command -v|type -p|\bfind |ls ')
try:
    flagged = set(json.load(open(f'{AIW}/toolchain23.json')))   # 报告「工具链缺失」口径 (规则分类) 的 23 单, 没有则为空
except Exception:
    flagged = set()

tasks_by_rt, iss_by_rt = Counter(), defaultdict(set)
miss = []   # 缺二进制
libs = []   # 缺共享库
for f in sorted(glob.glob(f'{AIW}/raw/*.json')):
    d = json.load(open(f)); runs = {r['id']: r for r in d['runs']}; k = d['jira_key']
    for r in d['runs']:
        tasks_by_rt[rt.get(r['runtime_id'], '?')] += 1; iss_by_rt[rt.get(r['runtime_id'], '?')].add(k)
    for tid, msgs in d['messages'].items():
        node = rt.get(runs.get(tid, {}).get('runtime_id'), '?'); agent = ag.get(runs.get(tid, {}).get('agent_id'), '?')
        for i, m in enumerate(msgs):
            if m['type'] != 'tool_result':
                continue
            o = (m.get('output') or '')[:4000]
            use = next((x for x in reversed(msgs[:i]) if x['type'] == 'tool_use'), {})
            cmd = re.sub(r'\s+', ' ', (use.get('input') or {}).get('command', ''))[:140]
            names = {x.group(1) for x in PAT2.finditer(o)} | {x.group(1) for x in PAT3.finditer(o)}
            if names:
                miss.append(dict(k=k, node=node, agent=agent, names=sorted(names), cmd=cmd, probe=bool(PROBE.search(cmd)), seq=m['seq'], task=tid[:8], out=re.sub(r'\s+', ' ', o)[:150]))
            ls = {(x.group(1) or x.group(2)) for x in LIB.finditer(o)}
            if ls:
                libs.append(dict(k=k, node=node, agent=agent, libs=sorted(ls), cmd=cmd, seq=m['seq'], task=tid[:8]))

ISS = lambda L: {e['k'] for e in L}
lk = lambda k: f"[{k}]({JIRA[bk[k]['jira_server']]}{k}) · [VelaAgent]({bk[k]['multica_issue_url']})"
nodes = sorted({e['node'] for e in miss + libs})
o = ['# 分析工具链缺失：涉及哪些问题、在哪些节点', '',
     f"> 窗口 {os.environ.get('AIW_START', '')} ~ {os.environ.get('AIW_END', '')}；数据：run-messages 中 tool_result 出现「X: 未找到命令」「X not found」或共享库 `=> not found`；节点 = 该 run 的 runtime（multica runtime list）。", '',
     '## 结论', '']
o.append(f"- 本周共 **{len(ISS(miss))} 单**在执行中遇到过工具链二进制找不到（{len(miss)} 次），另有 **{len(ISS(libs))} 单**遇到共享库找不到（{len(libs)} 次）。注意：其中很多是 agent 用 `which` / `find` 探测候选工具的输出（{sum(e['probe'] for e in miss)}/{len(miss)} 次是探测类命令），不代表任务因此失败；报告里「工具链缺失」那 23 单是更严格的口径（命令以非 0 退出且命中规则）。")
for n in nodes:
    M = [e for e in miss if e['node'] == n]; L = [e for e in libs if e['node'] == n]
    top = Counter(b for e in M for b in e['names']).most_common(4)
    o.append(f"- **{n}**：本周跑了 {len(iss_by_rt[n])} 单；缺二进制 {len(ISS(M))} 单（" + '、'.join(f'`{b}` {c}' for b, c in top) + f"）；缺共享库 {len(ISS(L))} 单" +
             ('（' + '、'.join(f'`{b}` {c}' for b, c in Counter(x for e in L for x in e['libs']).most_common(3)) + '）' if L else '') + '。')
o.append('- 没有出现缺失的节点：' + ('、'.join(n for n in sorted(iss_by_rt) if n not in nodes) or '无') + '（本周跑的单数分别是 ' + '、'.join(f'{n} {len(iss_by_rt[n])}' for n in sorted(iss_by_rt) if n not in nodes) + '）。')
o += ['', '## 按节点、按工具', '', '| 节点 | 缺失项 | 涉及单数 | 出现次数 | 其中探测类命令 |', '|---|---|---|---|---|']
agg = defaultdict(list)
for e in miss:
    for b in e['names']: agg[(e['node'], b)].append(e)
for (n, b), L in sorted(agg.items(), key=lambda x: (x[0][0], -len(ISS(x[1])))):
    o.append(f"| {n} | `{b}` | {len(ISS(L))} | {len(L)} | {sum(e['probe'] for e in L)} |")
lagg = defaultdict(list)
for e in libs:
    for b in e['libs']: lagg[(e['node'], b)].append(e)
for (n, b), L in sorted(lagg.items(), key=lambda x: (x[0][0], -len(ISS(x[1])))):
    o.append(f"| {n} | 共享库 `{b}` | {len(ISS(L))} | {len(L)} | — |")
o += ['', '## 证据样例', '']
seen = set()
for e in miss + [dict(x, names=x['libs'], out='（ldd/加载报错）', probe=False) for x in libs]:
    key = (e['node'], tuple(e['names']))
    if key in seen or len(seen) >= 8: continue
    seen.add(key)
    o.append(f"- {e['node']} · {e['agent']} · [{e['k']}]({JIRA[bk[e['k']]['jira_server']]}{e['k']}) task `{e['task']}` seq {e['seq']}：`{e['cmd']}` → {e['out']}")
o += ['', '## 涉及问题（缺二进制）', '', '| Jira / VelaAgent | 节点 | 缺失二进制 | 命令类型 | 报告「工具链缺失」23 单 |', '|---|---|---|---|---|']
byk = defaultdict(list)
for e in miss: byk[e['k']].append(e)
for k, L in sorted(byk.items(), key=lambda x: (sorted({e['node'] for e in x[1]}), x[0])):
    o.append(f"| {lk(k)} | {'、'.join(sorted({e['node'] for e in L}))} | {'、'.join(sorted({b for e in L for b in e['names']}))} | {'探测' if all(e['probe'] for e in L) else '实际调用'} | {'是' if k in flagged else ''} |")
o += ['', '## 涉及问题（缺共享库）', '', f"共 {len(ISS(libs))} 单，全部列出：", '']
o.append(' · '.join(lk(k) for k in sorted(ISS(libs))))
o += ['', '## 需要注意', '',
      '- **这里只能说明「节点上找不到」，不能说明「该装什么」**：agent 经常用裸命令名（`arm-none-eabi-addr2line`、`gdb-multiarch`）；同一批 run 里用 `/home/mnt/agent-workspaces/<产品>/prebuilts/...` 绝对路径调用往往能成功。所以修法有两条路：在节点装工具链，或让 skill / PATH 指向 prebuilts。选哪条要看你们对节点环境的约定，我没有登录节点核对已装软件。',
      '- 共享库缺失的命令几乎都是 `gdb`：prebuilts 里的 gdb 依赖 libpython3.10、libncurses5 / libtinfo5，节点上没有，所以 prebuilts 的 gdb 跑不起来；系统自带的 `/usr/bin/gdb` 不是 multiarch。',
      '- 节点名来自 run 的 runtime_id；同一台机器上有 Claude / Codex / Kiro 多个 runtime，装的是机器，不是 runtime。']
open(sys.argv[1], 'w').write('\n'.join(o) + '\n')
print(len(ISS(miss)), len(ISS(libs)), Counter(e['node'] for e in miss), Counter(e['node'] for e in libs))
