"""用 VT 自带 JiraClient (CAS + token) 只读查解决状态."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../..'))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(AIW)   # cookie 文件落在临时目录
from jira_api.jira_client import JiraClient
from config import setting
from load import bridge
out, errs = {}, []
for srv, url in (('n', setting.host_url), ('phone', setting.phone_host_url)):
    try:
        j = JiraClient(url).get_entry()
    except Exception as e:
        errs.append((srv, 'login', repr(e)[:200])); continue
    keys = sorted({b['jira_key'] for b in bridge if b['jira_server'] == srv and b['jira_key']})
    for i in range(0, len(keys), 80):
        try:
            for it in j.search_issues(f'key in ({",".join(keys[i:i+80])})', maxResults=100, validate_query=False,
                                      fields='status,resolution,resolutiondate,assignee'):
                f = it.fields
                out[it.key] = {'status': f.status.name, 'category': f.status.statusCategory.key,
                               'resolution': getattr(f.resolution, 'name', None), 'resolved_at': f.resolutiondate,
                               'assignee': getattr(f.assignee, 'displayName', None)}
        except Exception as e:
            errs.append((srv, i, repr(e)[:200]))
json.dump(out, open(f'{AIW}/db/jira_live.json', 'w'), ensure_ascii=False)
print('got', len(out), 'errs', errs[:3])
