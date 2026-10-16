#!/usr/bin/env python3
"""拉取 crashlog agent 失败任务的 console 日志块并按 rules.json 归类。

输入: tasks.json —— 由 agent 通过 vela-mcp 查询得到的失败任务列表(JSON 数组),
      每项至少包含 node_name / log_file_id / update_date, 其余字段原样透传。
输出: result.json —— 每个任务的类别、命中规则、关键行, 以及聚合统计;
      blocks 目录下 <log_file_id>.log 为完整日志块。

只用标准库 + 系统 ssh。日志位于宿主机 /home/mnt/crash_logs/<node>/console_<date>.log,
跨天的文件被压成 .log.zip。每个容器串行处理任务, 所以按
"task begin" ~ "task end" 行区间截取即可拿到完整块(含无 log= 前缀的子进程输出)。
"""
import argparse
import collections
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys

LOG_ROOT = '/home/mnt/crash_logs'
SSH_USER = 'jenkins'
REPORT_URL = 'http://vela.pt.xiaomi.srv/vela_crash_report?id='

# 在远端执行的脚本: 读取指定 node/date 的 console 日志, 截取目标 log_file_id 的任务块。
REMOTE_SCRIPT = r'''
import json, os, re, sys, zipfile
REQ = json.loads(__REQ__)
ROOT = REQ["root"]
MARK = re.compile(r"\blog=(\d+) core=\S* name=\S* idx=\d+\] task (begin|end)\b")

def open_lines(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\n")
        return
    zpath = path + ".zip"
    if os.path.exists(zpath):
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                with z.open(name) as f:
                    for raw in f:
                        yield raw.decode("utf-8", "replace").rstrip("\n")
        return
    raise FileNotFoundError(path)

out = {}
for node, item in REQ["nodes"].items():
    wanted = set(str(i) for i in item["ids"])
    blocks = {}
    files = {}
    for date in sorted(item["dates"]):
        path = os.path.join(ROOT, node, "console_%s.log" % date)
        cur_id, cur = None, None
        try:
            for line in open_lines(path):
                m = MARK.search(line)
                if m and m.group(2) == "begin":
                    if cur_id is not None:
                        blocks[cur_id] = {"complete": False, "lines": cur, "date": date}
                    cur_id, cur = (m.group(1), [line]) if m.group(1) in wanted else (None, None)
                    continue
                if cur_id is not None:
                    cur.append(line)
                    if m and m.group(2) == "end" and m.group(1) == cur_id:
                        blocks[cur_id] = {"complete": True, "lines": cur, "date": date}
                        cur_id, cur = None, None
            if cur_id is not None:
                blocks[cur_id] = {"complete": False, "lines": cur, "date": date}
            files[date] = "ok"
        except FileNotFoundError:
            files[date] = "missing"
        except Exception as e:
            files[date] = "error: %s" % e
    out[node] = {"files": files, "blocks": blocks}
sys.stdout.write(json.dumps(out))
'''


def load_tasks(path):
    """支持 JSON 数组, 或直接粘贴 vela_sql_query 的表格文本(表头 | 分隔, 下一行为 ---)。"""
    with open(path, encoding='utf-8') as f:
        raw = f.read()
    if raw.lstrip().startswith('['):
        return json.loads(raw)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ' | ' in ln and i + 1 < len(lines) and set(lines[i + 1].strip()) <= {'-'}:
            header = [h.strip() for h in ln.split(' | ')]
            rows = []
            for row in lines[i + 2:]:
                if ' | ' not in row:
                    continue
                vals = [v.strip() for v in row.split(' | ')]
                if len(vals) == len(header):
                    rows.append(dict(zip(header, vals)))
            return rows
    raise ValueError(f'无法解析任务文件: {path}')


def host_of(node_name):
    return node_name.split('_', 1)[0]


def ssh_cmd(host, connect_timeout):
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
           '-o', f'ConnectTimeout={connect_timeout}']
    if host.startswith('mina'):
        cmd += ['-p', '24']
    cmd += [f'{SSH_USER}@{host}', 'python3', '-']
    return cmd


def to_date(value):
    if not value:
        return None
    s = str(value).replace('T', ' ')
    return dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S').date()


def fetch_host(host, node_req, connect_timeout, total_timeout):
    req = {'root': LOG_ROOT, 'nodes': node_req}
    script = REMOTE_SCRIPT.replace('__REQ__', repr(json.dumps(req)))
    try:
        p = subprocess.run(ssh_cmd(host, connect_timeout), input=script, capture_output=True,
                           text=True, timeout=total_timeout)
    except subprocess.TimeoutExpired:
        return host, None, f'ssh timeout after {total_timeout}s'
    if p.returncode != 0:
        return host, None, f'ssh rc={p.returncode}: {p.stderr.strip()[-300:]}'
    try:
        return host, json.loads(p.stdout), None
    except ValueError:
        return host, None, f'bad remote output: {p.stdout[-200:]!r} {p.stderr[-200:]!r}'


def load_rules(path):
    with open(path, encoding='utf-8') as f:
        rules = json.load(f)['rules']
    for r in rules:
        r['_re'] = [re.compile(p) for p in r['patterns']]
    return rules


def classify(text, rules):
    matched = [r['category'] for r in rules if any(rx.search(text) for rx in r['_re'])]
    return (matched[0] if matched else 'uncategorized'), matched


KEY_RE = re.compile(r'\b(ERROR|WARNING|CRITICAL)\b|Traceback|Error:|Exception|exit=|returncode=|unsupported|not found|\[subproc\]')


def key_lines(lines, limit):
    picked = [ln[:600] for ln in lines if KEY_RE.search(ln)]
    return picked[-limit:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tasks', required=True, help='失败任务 JSON 数组文件')
    ap.add_argument('--out', required=True, help='输出 result.json 路径')
    ap.add_argument('--blocks-dir', help='完整日志块输出目录(默认 <out 同目录>/blocks)')
    ap.add_argument('--rules', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rules.json'))
    ap.add_argument('--connect-timeout', type=int, default=15)
    ap.add_argument('--host-timeout', type=int, default=180, help='单台主机 ssh 总超时秒数')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--key-lines', type=int, default=25)
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    rules = load_rules(args.rules)
    blocks_dir = args.blocks_dir or os.path.join(os.path.dirname(os.path.abspath(args.out)), 'blocks')
    os.makedirs(blocks_dir, exist_ok=True)

    # host -> node -> {ids, dates}; 读 update_date 与 create_date 所在日期(任务可能跨零点)
    plan = collections.defaultdict(lambda: collections.defaultdict(lambda: {'ids': set(), 'dates': set()}))
    for t in tasks:
        node = t.get('node_name') or ''
        if not node or '_' not in node:
            continue
        item = plan[host_of(node)][node]
        item['ids'].add(str(t['log_file_id']))
        for k in ('update_date', 'create_date'):
            d = to_date(t.get(k))
            if d:
                item['dates'].add(d.isoformat())

    host_results, host_errors = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = []
        for host, nodes in plan.items():
            node_req = {n: {'ids': sorted(v['ids']), 'dates': sorted(v['dates'])} for n, v in nodes.items()}
            futs.append(ex.submit(fetch_host, host, node_req, args.connect_timeout, args.host_timeout))
        for fut in concurrent.futures.as_completed(futs):
            host, data, err = fut.result()
            if err:
                host_errors[host] = err
            else:
                host_results[host] = data

    out_tasks = []
    for t in tasks:
        r = dict(t)
        node = t.get('node_name') or ''
        host = host_of(node) if node else ''
        lid = str(t['log_file_id'])
        if not node or '_' not in node:
            r.update(category='log_unavailable', block_status='no_node_name', matched_rules=[])
        elif host in host_errors:
            r.update(category='log_unavailable', block_status='ssh_failed', matched_rules=[],
                     detail=host_errors[host])
        else:
            nd = host_results.get(host, {}).get(node, {})
            blk = nd.get('blocks', {}).get(lid)
            if not blk:
                r.update(category='log_unavailable', block_status='block_not_found', matched_rules=[],
                         detail=f"files={nd.get('files')}")
            else:
                lines = blk['lines']
                text = '\n'.join(lines)
                cat, matched = classify(text, rules)
                path = os.path.join(blocks_dir, f'{lid}.log')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text + '\n')
                r.update(category=cat, matched_rules=matched,
                         block_status='complete' if blk['complete'] else 'incomplete',
                         block_lines=len(lines), block_file=path,
                         key_lines=key_lines(lines, args.key_lines))
        out_tasks.append(r)

    def count(key):
        return dict(collections.Counter(str(x.get(key)) for x in out_tasks).most_common())

    by_cat = collections.defaultdict(lambda: {'tasks': 0, 'resources': set()})
    for x in out_tasks:
        c = by_cat[x['category']]
        c['tasks'] += 1
        if x.get('resource_id') is not None:
            c['resources'].add(x['resource_id'])
    summary = {
        'total_tasks': len(out_tasks),
        'total_resources': len({x.get('resource_id') for x in out_tasks if x.get('resource_id') is not None}),
        'by_category': {k: {'tasks': v['tasks'], 'resources': len(v['resources'])}
                        for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]['tasks'])},
        'by_task_name': count('task_name'),
        'by_product': count('product'),
        'by_branch': count('branch'),
        'by_node': count('node_name'),
    }
    result = {
        'generated_at': dt.datetime.now().isoformat(timespec='seconds'),
        'unreachable_hosts': host_errors,
        'summary': summary,
        'tasks': out_tasks,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # 报告附录表(直接拼到报告末尾)
    appendix = os.path.join(os.path.dirname(os.path.abspath(args.out)), 'appendix.md')
    with open(appendix, 'w', encoding='utf-8') as f:
        f.write('| update_date | node | task_name | status | product | branch | core | log_file_id | category | 报告 |\n')
        f.write('|---|---|---|---|---|---|---|---|---|---|\n')
        for x in sorted(out_tasks, key=lambda x: str(x.get('update_date')), reverse=True):
            rid = x.get('resource_id')
            f.write('| {} | {} | {} | {} | {} | {} | {} | {} | {} | [{}]({}{}) |\n'.format(
                str(x.get('update_date')).replace('T', ' '), x.get('node_name'), x.get('task_name'),
                x.get('task_status'), x.get('product'), x.get('branch'), x.get('core_name'),
                x.get('log_file_id'), x['category'], rid, REPORT_URL, rid))

    print(json.dumps({'unreachable_hosts': host_errors, 'summary': summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
