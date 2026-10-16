"""拉已解决单的 Jira 人工评论 + 解决信息 (只读), 供 AI 复核对照."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../..'))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(AIW)
from jira import JIRA
from jira_api.jira_client import JiraClient
from config import setting
from load import bridge
live = json.load(open('db/jira_live.json'))
keys = [k for k, v in live.items() if v.get('resolution') or v.get('category') == 'done'] + sys.argv[1:]
srv = {b['jira_key']: b['jira_server'] for b in bridge}
c = JiraClient(setting.host_url)
cli = {'n': c.get_entry(), 'phone': JIRA(setting.phone_host_url, options=c.get_cookies(), token_auth=setting.phone_token)}
out = json.load(open('db/jira_comments.json')) if os.path.exists('db/jira_comments.json') else {}
for k in keys:
    if k in out: continue
    try:
        it = cli[srv[k]].issue(k, fields='summary,status,resolution,comment,assignee,fixVersions')
        cm = [{'author': getattr(x.author, 'displayName', ''), 'at': x.created[:16], 'body': x.body[:1500]}
              for x in it.fields.comment.comments if '[AI]' not in x.body[:40] and 'mios-assistant' not in getattr(x.author, 'name', '')]
        out[k] = {'summary': it.fields.summary, 'status': it.fields.status.name, 'resolution': getattr(it.fields.resolution, 'name', None),
                  'assignee': getattr(it.fields.assignee, 'displayName', None), 'comments': cm[-8:]}
    except Exception as e:
        out[k] = {'error': repr(e)[:200]}
json.dump(out, open('db/jira_comments.json', 'w'), ensure_ascii=False)
print(len(out), sum('error' in v for v in out.values()))
