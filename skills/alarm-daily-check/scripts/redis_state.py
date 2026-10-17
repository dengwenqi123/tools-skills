"""读取告警巡检所需的 Redis 状态（只读接口，MCP Token 鉴权，凭据取自 vela-mcp 配置）。

用法: python3 redis_state.py [YYYY-MM-DD]
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get('VELA_SERVICE_BASE', 'http://service.mios.xiaomi.srv')
URL = f'{BASE}/mios-sys/alarm/api/alarm/audit/redis_state'


def headers():
    env = json.load(open(os.path.expanduser('~/.kiro/settings/mcp.json')))['mcpServers']['vela-mcp'].get('env', {})
    return {'X-MCP-Username': env.get('VELA_MCP_USERNAME', ''), 'X-MCP-Token': env.get('VELA_MCP_TOKEN', '')}


def main():
    url = URL + (f'?date={sys.argv[1]}' if len(sys.argv) > 1 else '')
    req = urllib.request.Request(url, headers=headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(json.dumps(json.load(resp), ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as e:
        print(json.dumps({'http_status': e.code, 'body': e.read().decode('utf-8', 'replace')[:500]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
