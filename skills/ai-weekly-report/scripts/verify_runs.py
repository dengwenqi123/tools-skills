"""改动生效核对: 对比两批 run-messages 的行为指标 (改动前一批 vs 改动后、只取 cutoff 之后创建的 run).
用法: AIW_BEFORE=<raw 目录> AIW_AFTER=<raw 目录> AIW_CUT=2026-10-15T00:10 AIW_RUNTIMES=<runtimes.json> python verify_runs.py <输出.json>
"""
import os
import glob, json, re, sys
from collections import Counter, defaultdict

CUT = os.environ.get('AIW_CUT', '')
W = '/home/mnt/agent-workspaces'
SEARCH = re.compile(r'\bfind\b|grep\s+-[a-zA-Z]*[rR]|\brg\b')
JIRA_MCP = re.compile(r'^mcp__(mi-jira|mi-jira-phone|dfx-mi-jira)__')


def scope(cmd):
    paths = re.findall(r'(/home/mnt/agent-workspaces(?:/[\w.+-]+)*)', cmd)
    if not paths:
        return 'rel'
    d = min(len(p[len(W):].strip('/').split('/')) if p[len(W):].strip('/') else 0 for p in paths)
    return {0: 'ws_root', 1: 'product_root'}.get(d, 'subrepo')


def scan(dirpath, rt, cut=None):
    m = Counter(); ex = defaultdict(list); nodes = Counter(); runs = 0; keys = set()
    for f in sorted(glob.glob(f'{dirpath}/*.json')):
        d = json.load(open(f)); rmap = {r['id']: r for r in d['runs']}
        for tid, msgs in d['messages'].items():
            r = rmap.get(tid, {})
            if cut and (r.get('created_at') or '') < cut:
                continue
            runs += 1; keys.add(d['jira_key']); node = rt.get(r.get('runtime_id'), '?'); nodes[node] += 1
            for i, x in enumerate(msgs):
                if x['type'] == 'tool_use':
                    tool = (x.get('tool') or '').split()[0]
                    cmd = (x.get('input') or {}).get('command', '') or ''
                    res = next((y for y in msgs[i + 1:i + 3] if y['type'] == 'tool_result'), {})
                    out = (res.get('output') or '')[:4000]
                    if tool == 'Bash':
                        if SEARCH.search(cmd):
                            s = scope(cmd); m[f'search_{s}'] += 1
                            if 'timed out after 2m' in out: m[f'search_{s}_timeout'] += 1
                        if 'timed out after 2m' in out: m['bash_timeout'] += 1
                        if re.search(r'\brepo\s+list\b', cmd):
                            m['repo_list'] += 1
                            if 'REPO_TRACE=0' in cmd: m['repo_list_trace0'] += 1
                            if 'PermissionError' in out or 'TRACE_FILE' in out: m['repo_list_permerr'] += 1; ex['repo_list_permerr'].append(d['jira_key'])
                        if 'project.list' in cmd: m['project_list'] += 1
                        if re.search(r'\bgit\s+(-C\s+\S+\s+)?grep\b', cmd): m['git_grep'] += 1
                        if re.search(r'不是 git 仓库|not a git repository', out): m['not_git_repo'] += 1; ex['not_git_repo'].append(d['jira_key'])
                        if 'compile_kb.py' in cmd:
                            m['compile_kb'] += 1
                            if re.search(r'Permission|权限不够|Traceback', out): m['compile_kb_fail'] += 1; ex['compile_kb_fail'].append(d['jira_key'])
                        if re.search(r'mkdir[^\n]*agent-files', cmd) and re.search(r'权限不够|Permission denied', out):
                            m['agentfiles_perm'] += 1; ex['agentfiles_perm'].append(d['jira_key'])
                        for b in re.findall(r'(arm-none-eabi-[a-z0-9-]+|gdb-multiarch)(?::| )\s*(?:未找到命令|command not found|not found)', out):
                            m[f'missing_{b}'] += 1; ex[f'missing_{b}'].append((d['jira_key'], node))
                        if re.search(r'libpython3\.10\.so[^\n]*not found|error while loading shared libraries', out):
                            m['missing_lib'] += 1; ex['missing_lib'].append((d['jira_key'], node))
                    if JIRA_MCP.match(tool):
                        m['jira_mcp_call'] += 1
                        if re.search(r'tool_use_error|No such tool|MCP error|McpError|"isError":\s*true', out[:400]):
                            m['jira_mcp_err'] += 1; ex['jira_mcp_err'].append((d['jira_key'], out[:120]))
                    if tool.startswith('mcp__gdbmcp__'):
                        m['gdbmcp_call'] += 1
                        if re.search(r'tool_use_error|No such tool|MCP error', out[:300]): m['gdbmcp_err'] += 1
    return dict(runs=runs, issues=len(keys), nodes=dict(nodes), m=dict(m), ex={k: v[:6] for k, v in ex.items()})


rt = {r['id']: r['name'] for r in json.load(open(os.environ['AIW_RUNTIMES']))}
before = scan(os.environ['AIW_BEFORE'], rt)
after = scan(os.environ['AIW_AFTER'], rt, CUT)
json.dump({'before': before, 'after': after, 'cut': CUT}, open(sys.argv[1] if len(sys.argv) > 1 else 'verify_runs.json', 'w'), ensure_ascii=False, indent=1, default=str)
for name, s in (('W42', before), ('after', after)):
    print(name, 'runs', s['runs'], 'issues', s['issues'], s['nodes'])
    print('  ', {k: s['m'][k] for k in sorted(s['m'])})
print('after ex', json.dumps(after['ex'], ensure_ascii=False)[:1500])
