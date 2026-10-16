"""只读查询 mios_db: 走 vela-mcp 同一个 HTTP 接口, 结果落 JSON 文件 (不进对话上下文)."""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, os, sys, httpx
env = json.load(open(os.path.expanduser('~/.kiro/settings/mcp.json')))['mcpServers']['vela-mcp'].get('env', {})
H = {'X-MCP-Username': env.get('VELA_MCP_USERNAME', ''), 'X-MCP-Token': env.get('VELA_MCP_TOKEN', ''), 'Content-Type': 'application/json'}

def q(sql, limit=1000):
    r = httpx.post('http://service.mios.xiaomi.srv/mios-sys/sql/query', headers=H,
                   json={'database': 'mios_db', 'sql': sql, 'limit': limit}, timeout=60)
    r.raise_for_status()
    return r.json()

if __name__ == '__main__':
    name, sql = sys.argv[1], sys.argv[2]
    b = q(sql)
    json.dump(b, open(f'{AIW}/db/{name}.json', 'w'), ensure_ascii=False, default=str)
    d = b.get('data', b)
    print(name, 'keys=', list(b.keys())[:6], 'rows=', len(d.get('rows', [])) if isinstance(d, dict) else '?', 'trunc=', d.get('truncated') if isinstance(d, dict) else '?')
