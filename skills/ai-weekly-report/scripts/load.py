"""读 export.py / build_summary.py 的产物. summary 以 me 原始数据重算为准, 线上采集结果只做兜底."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json
from collections import Counter  # noqa: F401  (供调用方 from load import *)


def tab(name):
    b = json.load(open(f'{AIW}/db/{name}.json'))['data']
    cols = [c['name'] for c in b['columns']]
    return [dict(zip(cols, r)) for r in b['rows']]


def J(v, d=None):
    if v in (None, ''):
        return d
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return d


bridge = tab('bridge'); online = tab('summary'); fb = tab('feedback')
_raw = f'{AIW}/db/raw_summary.json'
gap = json.load(open(_raw)) if os.path.exists(_raw) else []
for s in online:
    s['_src'] = 'online'
summ = {s['jira_key']: s for s in online}
for s in gap:
    summ[s['jira_key']] = s   # me 原始数据重算 (最新), 覆盖线上停采前的旧汇总
