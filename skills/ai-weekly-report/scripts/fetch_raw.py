#!/usr/bin/env python3
"""复用 bug_agents sync/telemetry/collect.py 的 multica CLI 封装, 拉 issue 原始数据留档 (只读)."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.environ.get('BUG_AGENTS', str(Path.home() / 'data/work/bug_agents')), 'sync/telemetry'))
import collect as C  # run_multica / parse_json_output / fetch_issue / fetch_comments

OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)

def one(line):
    key, iid = line.split()
    p = OUT / f'{key}.json'
    if p.exists():
        return key, 'cached'
    runs = C.parse_json_output(C.run_multica(['issue', 'runs', iid])) or []
    msgs = {}
    for r in runs:
        if r.get('status') in ('completed', 'failed'):
            msgs[r['id']] = C.parse_json_output(C.run_multica(['issue', 'run-messages', r['id']])) or []
    raw = {'jira_key': key, 'issue': C.fetch_issue(iid), 'comments': C.fetch_comments(iid), 'runs': runs,
           'usage': C.parse_json_output(C.run_multica(['issue', 'usage', iid])) or {}, 'messages': msgs}
    p.write_text(json.dumps(raw, ensure_ascii=False))
    return key, f"runs={len(runs)} cmts={len(raw['comments'])}"

lines = [l for l in Path(sys.argv[1]).read_text().split('\n') if l.strip()]
with ThreadPoolExecutor(6) as ex:
    for k, s in ex.map(one, lines):
        print(k, s, flush=True)
