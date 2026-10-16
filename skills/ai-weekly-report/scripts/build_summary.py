"""用 VT issue_trace.builder (与线上采集器同一套纯函数) 把 me 上拉到的原始数据算成 summary 行."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, sys, glob, os
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../..')))
from application.issue_trace import builder

D = AIW
bridge = {r['jira_key']: r for r in (dict(zip([c['name'] for c in b['data']['columns']], row))
          for b in [json.load(open(f'{D}/db/bridge.json'))] for row in b['data']['rows'])}
agents = {a['id']: a['name'] for a in json.load(open(f'{D}/agents.json'))}
out, errs = [], []
for f in sorted(glob.glob(f'{D}/raw/*.json')):
    raw = json.load(open(f))
    k = raw['jira_key']
    br = bridge.get(k)
    if not br:
        continue   # 窗口外 (10-14 当天建单)
    try:
        tool = {tid: builder.tool_stats(m) for tid, m in raw['messages'].items()}
        s, spans = builder.build(br, raw['issue'] or {}, raw['runs'], raw['comments'], raw['usage'], agents, tool,
                                 now=datetime.now())
        s['_src'] = 'me_raw'
        out.append(s)
    except Exception as e:
        errs.append((k, repr(e)))
json.dump(out, open(f'{D}/db/raw_summary.json', 'w'), ensure_ascii=False, default=str)
print('built', len(out), 'errors', errs[:5])
print(sorted(out[0].keys()))
