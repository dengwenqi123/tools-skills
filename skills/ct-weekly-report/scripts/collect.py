"""CT 周报取数：只读查询 mios_db（走 vela-mcp 同一个 SQL HTTP 接口），结果写到 $CTW_WORK/data.json。

用法：
  python collect.py                         # 窗口 = 现在往前推 7 天
  python collect.py --start '2026-10-14 16:14:00' --end '2026-10-21 16:15:00'
上期窗口自动取本期之前、同样长度的一段，不重叠。
只发聚合查询：接口单次最多返回 1000 行，明细查询会被截断。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import httpx

API = 'http://service.mios.xiaomi.srv/mios-sys/sql/query'
FMT = '%Y-%m-%d %H:%M:%S'
REASON_CASE = "('testcase_vela','Test')"
REASON_SYS = "'可能是系统问题'"
STOP_HOURS = 24          # 窗口最后 24 小时没有新任务 = 停线
STOP_MIN_TASKS = 50      # 只对活跃任务线判停线（窗口内 ≥ 50 个任务），避免把本来就稀疏的线误判
QUEUE_AVG_MIN = 15       # 排队明细只列平均排队超过 15 分钟（或从未执行 > 5）的任务线
FAIL_TYPES = ('ct_failure', 'patch_failure', 'dashboard_task_fail_rate')


def _headers():
    cfg = json.load(open(os.path.expanduser('~/.kiro/settings/mcp.json')))
    env = cfg['mcpServers']['vela-mcp'].get('env', {})
    return {'X-MCP-Username': env.get('VELA_MCP_USERNAME', ''), 'X-MCP-Token': env.get('VELA_MCP_TOKEN', ''),
            'Content-Type': 'application/json'}


H = None


def num(v):
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f.is_integer() and '.' not in v else f
        except ValueError:
            return v
    return v


def q(sql):
    """返回 list[dict]；截断时报错退出，避免拿半截数据出报告。"""
    global H
    H = H or _headers()
    r = httpx.post(API, headers=H, json={'database': 'mios_db', 'sql': sql, 'limit': 1000}, timeout=120)
    if r.status_code != 200:
        sys.exit(f'SQL 接口 {r.status_code}: {r.text[:300]}\n{sql}')
    b = r.json()
    if b.get('code') != 0:
        sys.exit(f'SQL 失败: {b.get("message")}\n{sql}')
    d = b['data']
    if d.get('truncated'):
        sys.exit(f'结果被截断（>1000 行），请改成更粗的聚合:\n{sql}')
    cols = [c['name'] for c in d['columns']]
    return [{c: num(v) for c, v in zip(cols, row)} for row in d['rows']]


def ct_overall(s, e):
    return q(f"""
SELECT COUNT(*) n, SUM(start_time IS NOT NULL) started,
       SUM(TIMESTAMPDIFF(SECOND, create_time, start_time) > 3600) gt1h,
       SUM(start_time IS NULL AND state IN ('timeout','killed')) never_ran,
       SUM(state='failed') failed, SUM(state IN ('success','failed')) done,
       COUNT(DISTINCT product) products
FROM vela_autotest_execution_record
WHERE job_type='admission' AND create_time >= '{s}' AND create_time < '{e}'""")[0]


def fail_lines(s, e):
    rows = q(f"""
SELECT product p, branch br, task_key tk, COUNT(*) n, SUM(state='failed') f, SUM(state IN ('success','failed')) done,
       SUM(state IN ('timeout','killed')) unfin,
       SUM(state='failed' AND reason IN {REASON_CASE}) rcase,
       SUM(state='failed' AND reason={REASON_SYS}) rsys,
       SUM(state='failed' AND reason LIKE 'coral%') renv,
       SUM(state='failed' AND COALESCE(reason,'')='') rnone,
       SUM(state='failed' AND COALESCE(reason,'')<>'' AND reason NOT IN {REASON_CASE} AND reason<>{REASON_SYS}
           AND reason NOT LIKE 'coral%') rother,
       GROUP_CONCAT(DISTINCT CASE WHEN state='failed' THEN DATE_FORMAT(create_time,'%m-%d') END
                    ORDER BY create_time SEPARATOR '、') days,
       MAX(create_time) last_create
FROM vela_autotest_execution_record
WHERE job_type='admission' AND create_time >= '{s}' AND create_time < '{e}'
GROUP BY product, branch, task_key""")
    for r in rows:   # reason 为 NULL 时 SUM 可能整组为 NULL
        for k in ('f', 'done', 'unfin', 'rcase', 'rsys', 'renv', 'rnone', 'rother'):
            r[k] = r[k] or 0
    return rows


def alarms(s, e):
    # 产品取通知里的 product，失败率 / 同步类预警没有通知明细，用 task_key 反查执行记录
    return q(f"""
SELECT DATE_FORMAT(r.notify_time,'%m-%d') d, r.alarm_type t, r.branch br,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.alarm_data,'$.notifications[0].product')),''),
                (SELECT e.product FROM vela_autotest_execution_record e WHERE e.task_key=r.task_key LIMIT 1),
                r.task_key, '') p,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.alarm_data,'$.notifications[0].task_key')),''), r.task_key, '') tk,
       ru.notify_groups grp, COUNT(*) c, SUM(COALESCE(r.resolver,'')<>'') claimed,
       SUM(COALESCE(JSON_EXTRACT(r.alarm_data,'$.notify_count'),1)) nc
FROM vela_autotest_alarm_record r LEFT JOIN vela_autotest_alarm_rule ru ON ru.id=r.rule_id
WHERE r.notify_time >= '{s}' AND r.notify_time < '{e}'
GROUP BY d, t, br, p, tk, grp""")


def killed(s, e):
    return q(f"""
SELECT DATE_FORMAT(end_time,'%m-%d') d, product p, branch br, task_key tk, COUNT(*) c
FROM vela_autotest_execution_record
WHERE state='killed' AND job_type='daily' AND branch IN ('dev','trunk-26rc01')
  AND end_time >= '{s}' AND end_time < '{e}'
GROUP BY d, product, branch, task_key""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start')
    ap.add_argument('--end')
    a = ap.parse_args()
    end = datetime.strptime(a.end, FMT) if a.end else datetime.now().replace(microsecond=0)
    start = datetime.strptime(a.start, FMT) if a.start else end - timedelta(days=7)
    pstart = start - (end - start)
    S, E, PS = start.strftime(FMT), end.strftime(FMT), pstart.strftime(FMT)
    work = os.environ.get('CTW_WORK') or f'/tmp/ct-weekly/{end:%Y-%m-%d}'
    os.makedirs(work, exist_ok=True)

    cur_lines = fail_lines(S, E)
    stop_before = (end - timedelta(hours=STOP_HOURS)).strftime(FMT)
    stopped = {r['tk']: str(r['last_create'])[:16].replace('T', ' ') for r in cur_lines
               if r['n'] >= STOP_MIN_TASKS and str(r['last_create']).replace('T', ' ') < stop_before}
    al = alarms(S, E)
    queue_tks = {r['tk'] for r in al if r['t'] == 'queue_overflow' and r['tk']}

    lines = q(f"""
SELECT task_key tk, product p, branch br, COUNT(*) n, SUM(start_time IS NOT NULL) started,
       AVG(TIMESTAMPDIFF(SECOND, create_time, start_time))/60 avg_w,
       MAX(TIMESTAMPDIFF(SECOND, create_time, start_time))/60 max_w,
       SUM(TIMESTAMPDIFF(SECOND, create_time, start_time) > 3600) gt1h,
       SUM(start_time IS NULL AND state IN ('timeout','killed')) ns,
       AVG(CASE WHEN end_time > start_time THEN TIMESTAMPDIFF(SECOND, start_time, end_time) END)/60 run
FROM vela_autotest_execution_record
WHERE job_type='admission' AND create_time >= '{S}' AND create_time < '{E}'
GROUP BY task_key, product, branch""")
    queue = [r for r in lines if (r['avg_w'] or 0) > QUEUE_AVG_MIN or (r['ns'] or 0) > 5 or r['tk'] in queue_tks]
    for r in queue:   # 中位数：MySQL 5.7 没有窗口函数，按排序取中间一行
        k = int(r['started'] or 0)
        rows = q(f"""SELECT TIMESTAMPDIFF(SECOND, create_time, start_time)/60 w FROM vela_autotest_execution_record
WHERE task_key='{r['tk']}' AND create_time >= '{S}' AND create_time < '{E}' AND start_time IS NOT NULL
ORDER BY w LIMIT 1 OFFSET {max(0, (k - 1) // 2)}""") if k else []
        r['p50'] = rows[0]['w'] if rows else None
        r['stopped'] = stopped.get(r['tk'])
    tks = ','.join(f"'{r['tk']}'" for r in queue) or "''"
    queue_daily = q(f"""
SELECT task_key tk, DATE_FORMAT(create_time,'%m-%d') d, COUNT(*) n, SUM(start_time IS NOT NULL) started,
       AVG(TIMESTAMPDIFF(SECOND, create_time, start_time))/60 avg_w
FROM vela_autotest_execution_record
WHERE task_key IN ({tks}) AND create_time >= '{S}' AND create_time < '{E}'
GROUP BY task_key, d""")

    data = {
        'window': {'start': S, 'end': E, 'prev_start': PS, 'generated_at': datetime.now().strftime(FMT)},
        'ct': ct_overall(S, E), 'ct_prev': ct_overall(PS, S),
        'ct_days': q(f"""
SELECT DATE_FORMAT(create_time,'%m-%d') d, COUNT(*) n, SUM(state='failed') f, SUM(state IN ('success','failed')) done,
       COUNT(DISTINCT CASE WHEN state='failed' THEN CONCAT(product,'|',branch) END) nlines,
       SUM(TIMESTAMPDIFF(SECOND, create_time, start_time) > 3600) gt1h,
       SUM(start_time IS NULL AND state IN ('timeout','killed')) ns
FROM vela_autotest_execution_record
WHERE job_type='admission' AND create_time >= '{S}' AND create_time < '{E}' GROUP BY d ORDER BY d"""),
        'fail_lines': [r for r in cur_lines if r['f']],
        'fail_lines_prev': [r for r in fail_lines(PS, S) if r['f'] or r['done']],
        'stopped': stopped,
        'queue': queue, 'queue_daily': queue_daily,
        'alarms': al, 'alarms_prev': alarms(PS, S),
        'killed': killed(S, E), 'killed_prev': killed(PS, S),
    }
    path = os.path.join(work, 'data.json')
    json.dump(data, open(path, 'w'), ensure_ascii=False, indent=1, default=str)
    c = data['ct']
    print(f'窗口 {S} ~ {E}（上期 {PS} 起）→ {path}')
    print(f"CT {c['n']} 个，排队>1h {c['gt1h']}/{c['started']}，从未执行 {c['never_ran']}，失败 {c['failed']}/{c['done']}")
    print('排队预警', sum(r['c'] for r in al if r['t'] == 'queue_overflow'),
          '失败预警', sum(r['c'] for r in al if r['t'] in FAIL_TYPES), '停线', len(stopped))


if __name__ == '__main__':
    main()
