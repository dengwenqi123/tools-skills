"""coverity-fixer 的每个分析是否都提交了 Gerrit patch → MD.
证据来源 (每单四路, 互相印证): ① VT 的 ai_generated_patches 表; ② agent 评论里的 Gerrit 链接; ③ run-messages 里 git push / gerrit query 的返回;
④ agent 写的 issue metadata pr_url. 需先跑 export.py + 拉原始数据 + 查过 ai_generated_patches (本脚本会自查).
用法: AIW_WORK=... python coverity_patches.py <输出.md>
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load import bridge, tab
from q import q

cov = [b for b in bridge if b['module'] == 'jql' and b['jira_type'] == 'coverity']
keys = [b['jira_key'] for b in cov]
IN = ','.join(f"'{k}'" for k in keys)
cols = 'issue_key,source_module,push_status,gerrit_change_number,gerrit_url,gerrit_status,CAST(gerrit_merged_at AS CHAR) merged_at'
rows = q(f'SELECT {cols} FROM ai_generated_patches WHERE issue_key IN ({IN})')['data']
dbp = defaultdict(list)
for r in rows['rows']:
    d = dict(zip([c['name'] for c in rows['columns']], r)); dbp[d['issue_key']].append(d)

G = re.compile(r'https?://gerrit\.[a-z.]+\.mioffice\.cn/c/[^\s)\]>"\'`，。]+/\+/(\d+)')
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
ag = {a['id']: a['name'] for a in json.load(open(f'{AIW}/agents.json'))}
out_rows = []
for b in cov:
    k = b['jira_key']; d = json.load(open(f'{AIW}/raw/{k}.json'))
    cmt_urls = []
    for c in d['comments']:
        if c['author_type'] == 'agent':
            cmt_urls += [m.group(0) for m in G.finditer(c['content'] or '')]
    pr_meta, pushed, rejected, cid = [], 0, 0, None
    for msgs in d['messages'].values():
        for i, m in enumerate(msgs):
            if m['type'] == 'tool_use':
                cmd = (m.get('input') or {}).get('command', '') or ''
                mm = re.search(r'metadata set .*--key pr_url --value "?(https?://\S+?)"?(?:\s|$)', cmd)
                if mm: pr_meta.append(mm.group(0).split('--value')[1].strip(' "'))
                if re.search(r'git push\b.*refs/for/', cmd):
                    res = next((x for x in msgs[i + 1:i + 3] if x['type'] == 'tool_result'), {})
                    o = res.get('output') or ''
                    if re.search(r'Create Change|remote rejected|error: branch', o): rejected += 1
                    elif re.search(r'Processing changes.*(new|updated): \d', o, re.S): pushed += 1
    for c in d['comments']:
        mm = re.search(r'CID\s*(\d{6,8})', c['content'] or '')
        if mm and c['author_type'] == 'agent': cid = cid or mm.group(1)
    last = next((c for c in reversed(d['comments']) if c['author_type'] == 'agent'), {})
    claim = '提交 Gerrit' if re.search(r'已修复并提交|提交 ?Gerrit|gerrit 链接', last.get('content') or '', re.I) else (
        'push 被拒' if re.search(r'push.*拒绝|Create Change', last.get('content') or '') else '—')
    fixer_runs = [r for r in d['runs'] if ag.get(r['agent_id']) == 'coverity-fixer']
    p = dbp.get(k, [])
    cmt_nums = {G.search(u).group(1) for u in cmt_urls}
    meta_nums = {m.group(1) for u in pr_meta if (m := G.search(u))}
    db_nums = {str(x['gerrit_change_number']) for x in p}
    any_patch = bool(p or cmt_nums or meta_nums or pushed)
    if p and cmt_nums & db_nums: cat = 'A 有 patch，DB 已收录，评论带链接'
    elif p: cat = 'B 有 patch，DB 已收录，但 agent 评论没带 Gerrit 链接'
    elif any_patch: cat = 'C 有 patch，但 DB 没收录'
    elif rejected: cat = 'D 无 patch：push 被 Gerrit 拒绝'
    else: cat = 'E 无 patch：其他'
    nums = sorted(db_nums | cmt_nums | meta_nums)
    url = (p[0]['gerrit_url'] if p else (cmt_urls[0] if cmt_urls else (pr_meta[0] if pr_meta else '')))
    out_rows.append(dict(k=k, srv=b['jira_server'], prod=b['product'], cid=cid, cat=cat, nums=nums, url=url, st=(p[0]['gerrit_status'] or '未知') if p else '—',
                         db=len(p), cmt=sorted(cmt_nums), meta=sorted(meta_nums), pushed=pushed, rejected=rejected, runs=len(d['runs']), fruns=len(fixer_runs),
                         fail=sum(r['status'] == 'failed' for r in fixer_runs), claim=claim, va=b['multica_issue_url'], mod=Counter(x['source_module'] for x in p)))

cnt = Counter(r['cat'] for r in out_rows)
n = len(out_rows)
has = sum(1 for r in out_rows if r['cat'][0] in 'ABC')
o = ['# Coverity 问题（coverity-fixer）是否都提交了 Gerrit patch', '',
     f"> 窗口 {os.environ.get('AIW_START', '')} ~ {os.environ.get('AIW_END', '')}；范围：jql 入口、jira_type=coverity，共 {n} 单（单 Agent coverity-fixer，不经小队分诊）。",
     '', '## 结论', '',
     f"- **{has} / {n} 单有 Gerrit patch（{round(100 * has / n)}%）**，{n - has} 单没有。",
     f"- 没有 patch 的 {n - has} 单：" + ('；'.join(f"{r['k']}（{r['cat'][2:]}）" for r in out_rows if r['cat'][0] in 'DE') or '无') + '。',
     f"- 所有 patch 的 Gerrit 状态：" + '、'.join(f'{s} {c}' for s, c in Counter(r['st'] for r in out_rows if r['cat'][0] in 'AB').most_common()) + '（没有一个已合入）。', '',
     '## 分类', '', '| 类别 | 单数 | 含义 |', '|---|---|---|']
mean = {'A': '证据最全：DB 有记录、agent 评论里带了 Gerrit 链接', 'B': 'patch 确实在 Gerrit 上，但 agent 评论没写链接（工作约定要求把 Gerrit URL 补进 Jira 评论）',
        'C': 'agent 提交了 patch（有 push 返回或 pr_url），但 VT 的 ai_generated_patches 没收录', 'D': '修复已在本地提交，push 到 Gerrit 被拒绝', 'E': '没有任何 patch 证据'}
for c, v in sorted(cnt.items()):
    o.append(f'| {c} | {v} | {mean[c[0]]} |')
o += ['', '## 逐单明细', '', '| Jira | VelaAgent | 产品 | CID | 类别 | Gerrit change | Gerrit 状态 | DB 收录 | 评论带链接 | push 成功/被拒 | agent 末条声明 | coverity-fixer run（失败） |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
for r in sorted(out_rows, key=lambda x: (x['cat'], x['k'])):
    link = ' / '.join(f"[{x}]({r['url'].rsplit('/', 1)[0]}/{x})" if r['url'] else x for x in r['nums']) or '—'
    o.append(f"| [{r['k']}]({JIRA[r['srv']]}{r['k']}) | [VelaAgent]({r['va']}) | {r['prod'] or '—'} | {r['cid'] or '—'} | {r['cat'][0]} | {link} | {r['st']} | {'是' if r['db'] else '否'} | {'是' if r['cmt'] else '否'} | {r['pushed']}/{r['rejected']} | {r['claim']} | {r['fruns']}（{r['fail']}） |")
multi = [r for r in out_rows if len(r['nums']) > 1]
o += ['', '## 需要注意', '']
o += [f"- 同一单出现多个 Gerrit change：" + '；'.join(f"{r['k']} {', '.join(r['nums'])}" for r in multi) + '（可能是重提或多文件拆分，未逐个核对）。'] if multi else []
o += ['- Gerrit 域名有两个：`gerrit.pt.mioffice.cn`（change 号 1049 万以上）和 `gerrit.odm.mioffice.cn`（change 号 663 万左右，VELAFORCAR 的 infineon / nuttx 仓库）。',
      '- 「patch 存在」的判断：DB 有记录，或 agent 评论 / issue metadata（pr_url）里有 Gerrit 链接，或 git push 返回里有 new / updated。三者任一成立就算有 patch；没有逐个去 Gerrit 上核对 change 是否仍然存在。',
      '- 「是否合理」没有评价：patch 的修复内容是否正确（比如 VELAFORCAR-21164 负责人已回复「误报」，AI 仍提交了 patch）不在本表范围。']
open(sys.argv[1], 'w').write('\n'.join(o) + '\n')
print(dict(cnt), has, n)
for r in out_rows:
    if r['cat'][0] in 'BCDE': print(r['k'], r['cat'], r['nums'], r['rejected'], r['claim'])
