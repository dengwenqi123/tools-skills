import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../..'))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(AIW)
from jira import JIRA
from jira_api.jira_client import JiraClient
from config import setting
from load import bridge
c = JiraClient(setting.host_url)   # 复用 CAS SSO cookie
out = json.load(open('db/jira_live.json')); errs = []
try:
    j = JIRA(setting.phone_host_url, options=c.get_cookies(), token_auth=setting.phone_token)
    keys = sorted({b['jira_key'] for b in bridge if b['jira_server'] == 'phone'})
    for i in range(0, len(keys), 80):
        for it in j.search_issues(f'key in ({",".join(keys[i:i+80])})', maxResults=100, validate_query=False,
                                  fields='status,resolution,resolutiondate,assignee'):
            f = it.fields
            out[it.key] = {'status': f.status.name, 'category': f.status.statusCategory.key,
                           'resolution': getattr(f.resolution, 'name', None), 'resolved_at': f.resolutiondate,
                           'assignee': getattr(f.assignee, 'displayName', None)}
except Exception as e:
    errs.append(repr(e)[:300])
json.dump(out, open('db/jira_live.json', 'w'), ensure_ascii=False)
print('total', len(out), errs)
