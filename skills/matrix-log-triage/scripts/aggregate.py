#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""matrix 线上 console 日志 ERROR 聚合。

用法:
    python3 aggregate.py --hours 24 --out-dir <工作目录>/out <工作目录>/logs/*/*.log
    python3 aggregate.py --since "2026-10-15 19:00" --until "2026-10-16 19:00" --out-dir ... files...

输入文件名约定: logs/<YYYY-MM-DD>/<pod>.log（fetch_logs.sh 的产物），
或 FDS 原始路径 .../<pod>/console_<date>.log。

规则 (详见 references/priority-rules.md):
- 只统计 loguru 级别字段为 ERROR / CRITICAL 的记录; INFO 行正文里出现 "ERROR:" 不算。
- 不以时间戳开头的行是上一条记录的续行 (Traceback / loguru diagnose 堆栈)。
- ticket_event 的 ERROR 与同节点同 tid 的业务 ERROR 合并 (reason 作为根因提示), 不重复计数。
- 签名 = 文件:函数:行号 + 归一化消息 + 归一化根因。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN_ISSUES_MD = os.path.join(HERE, "..", "references", "known-issues.md")
RULES_MD = os.path.join(HERE, "..", "references", "priority-rules.md")

REC_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} \[(?P<thread>\d+)\] (?P<level>[A-Z]+) "
    r"\[(?P<ctx>[^\]]*)\] (?P<file>[^\s:]+):(?P<func>[^\s:]+):(?P<line>\d+) - (?P<msg>.*)$"
)
TS_START_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
EXC_LINE_RE = re.compile(r"^\s*(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Timeout|Refused|Failure)):? ?(?P<rest>.*)$")
TID_RE = re.compile(r"tid=([0-9a-f]+)")
RID_RE = re.compile(r"rid=(\d+)")

NORMALIZERS = [
    # key=value 形式的参数值 (task_key / product / branch / node ...) 归一化; error/message/reason 保留内容
    (re.compile(r"\b(?!(?:error|message|reason|msg|错误)\b)([A-Za-z_]\w*)=(?!<)[^\s,，;)]+"), r"\1=<V>"),
    (re.compile(r"(节点|产品|分支|仓库) [A-Za-z0-9_.\-/]+"), r"\1 <V>"),
    (re.compile(r"(?<=[:：] )[A-Za-z][A-Za-z0-9_.\-/]*[_\-/][A-Za-z0-9_.\-/]*"), "<V>"),
    (re.compile(r"https?://\S+"), "<URL>"),
    (re.compile(r"/(?:home|tmp|usr|mnt|data|var)/\S+"), "<PATH>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{7,}\b", re.I), "<HEX>"),
    (re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b"), "<JIRA>"),
    (re.compile(r"\b\d{4}[-.]\d\d[-.]\d\d[ T_-]\d\d[:.]\d\d[:.]\d\d\b"), "<TIME>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<N>"),
    (re.compile(r"'[^']{40,}'"), "'<STR>'"),
    (re.compile(r"\s+"), " "),
]

SCHEMA_CODES = ("1054", "1048", "1406", "1146", "1364", "1265")
TRANSIENT_PAT = re.compile(
    r"timed? ?out|timeout|超时|ConnectionError|Connection (?:reset|refused|aborted)|"
    r"RemoteDisconnected|SSH|\b50[234]\b|Max retries|Temporary failure|BrokenPipe|reset by peer",
    re.I,
)


def normalize(s, limit=200):
    s = s or ""
    for pat, rep in NORMALIZERS:
        s = pat.sub(rep, s)
    return s.strip()[:limit]


def load_json_block(md_path, default):
    """读取 md 中第一个 ```json 代码块。"""
    try:
        with open(md_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return default
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.S)
    return json.loads(m.group(1)) if m else default


def node_of(path):
    base = os.path.basename(path)
    if base.startswith("console_"):
        return os.path.basename(os.path.dirname(path))
    return re.sub(r"(_\d{4}-\d\d-\d\d)?\.log$", "", base)


def iter_records(path):
    """逐条产出 loguru 记录; ERROR/CRITICAL 的续行并入 extra。"""
    cur = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = REC_RE.match(line)
            if m:
                if cur:
                    yield cur
                cur = m.groupdict()
                cur["extra"] = []
                continue
            if TS_START_RE.match(line):
                # 时间戳开头但非 loguru 格式 (第三方库直接输出), 跳过
                if cur:
                    yield cur
                cur = None
                continue
            if cur is not None and cur["level"] in ("ERROR", "CRITICAL") and len(cur["extra"]) < 400:
                cur["extra"].append(line)
    if cur:
        yield cur


def exc_hint(extra):
    """续行里最后一条异常行 (Traceback 末行)。"""
    hint = None
    for ln in extra:
        s = ln.lstrip()
        if s.startswith(("File ", "│", "└", "->", "^")):
            continue
        if EXC_LINE_RE.match(ln):
            hint = ln.strip()
    return hint


def parse_ticket_event(msg):
    idx = msg.find("{")
    if idx < 0:
        return None
    try:
        return json.loads(msg[idx:])
    except ValueError:
        ev = {}
        for k in ("event", "stage", "reason", "outcome"):
            mm = re.search(r'"%s":"((?:[^"\\]|\\.)*)"' % k, msg)
            if mm:
                ev[k] = mm.group(1)
        return ev or None


def first_line(s):
    return (s or "").split("\\n")[0].split("\n")[0]


def suggest_priority(sig, rules):
    text = " ".join([sig["message"], sig.get("root_hint") or ""] + [x["raw"] for x in sig.get("samples", [])])
    if "Unknown column" in text or any(re.search(r"\(%s," % c, text) for c in SCHEMA_CODES):
        return "P0", "schema 不一致 (硬规则)"
    for kw in rules.get("p0_keywords", []):
        if kw.lower() in text.lower():
            return "P0", "命中 P0 关键词: %s" % kw
    transient = bool(TRANSIENT_PAT.search(text))
    if not transient:
        if sig["count"] >= rules.get("p1_count", 100):
            return "P1", "非瞬时错误, 次数 >= %d" % rules.get("p1_count", 100)
        if sig["node_count"] >= rules.get("p1_nodes", 3) and sig["count"] >= rules.get("p1_nodes_min_count", 20):
            return "P1", "非瞬时错误, 波及 >= %d 节点" % rules.get("p1_nodes", 3)
        for kw in rules.get("p1_keywords", []):
            if kw.lower() in text.lower():
                return "P1", "命中 P1 关键词: %s" % kw
    else:
        if sig["count"] >= rules.get("p1_transient_count", 1000):
            return "P1", "瞬时错误但量极大 (>= %d)" % rules.get("p1_transient_count", 1000)
        return "P2", "外部依赖瞬时错误"
    if sig["count"] >= rules.get("p2_count", 10):
        return "P2", "持续出现 (>= %d)" % rules.get("p2_count", 10)
    return "P3", "低频"


def match_known(sig, known):
    blob = "\n".join([
        sig["location"], sig["message"], sig.get("root_hint") or "",
        sig["samples"][0]["raw"] if sig["samples"] else "",
    ])
    for ki in known:
        try:
            if re.search(ki["pattern"], blob):
                return ki
        except re.error:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--since", help="YYYY-MM-DD HH:MM")
    ap.add_argument("--until", help="YYYY-MM-DD HH:MM, 默认当前时间")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--top", type=int, default=60, help="Markdown 中最多列出的签名数")
    args = ap.parse_args()

    until = dt.datetime.strptime(args.until, "%Y-%m-%d %H:%M") if args.until else dt.datetime.now()
    since = dt.datetime.strptime(args.since, "%Y-%m-%d %H:%M") if args.since else until - dt.timedelta(hours=args.hours)
    s_since, s_until = since.strftime("%Y-%m-%d %H:%M:%S"), until.strftime("%Y-%m-%d %H:%M:%S")

    rules = load_json_block(RULES_MD, {})
    known = load_json_block(KNOWN_ISSUES_MD, [])

    errors = []
    tickets = defaultdict(list)  # (node, tid) -> ticket_event ERROR 记录
    warn_counter = Counter()
    commits = defaultdict(Counter)
    scanned = {}

    for path in args.files:
        node = node_of(path)
        n_err = 0
        for rec in iter_records(path):
            if not (s_since <= rec["ts"] <= s_until):
                continue
            mc = re.search(r'"commit":"([0-9a-f]+)"', rec["msg"])
            if mc:
                commits[node][mc.group(1)] += 1
            lvl = rec["level"]
            if lvl == "WARNING":
                warn_counter[("%s:%s:%s" % (rec["file"], rec["func"], rec["line"]), normalize(rec["msg"], 120))] += 1
                continue
            if lvl not in ("ERROR", "CRITICAL"):
                continue
            n_err += 1
            rec["node"] = node
            mt = TID_RE.search(rec["ctx"])
            mr = RID_RE.search(rec["ctx"])
            rec["tid"] = mt.group(1) if mt else None
            rec["rid"] = mr.group(1) if mr else None
            if rec["file"] == "ticket_event.py" and "[ticket_event]" in rec["msg"]:
                rec["ticket"] = parse_ticket_event(rec["msg"]) or {}
                if rec["tid"]:
                    tickets[(node, rec["tid"])].append(rec)
                    continue
            errors.append(rec)
        scanned[node + "@" + os.path.basename(os.path.dirname(path))] = {"file": path, "errors_in_window": n_err}

    # ticket_event 合并: 同 (node, tid) 已有业务 ERROR 的折叠; 否则独立成条
    primary_keys = {(e["node"], e["tid"]) for e in errors if e["tid"]}
    ticket_reason = {}
    for key, evs in tickets.items():
        reason = first_line((evs[0].get("ticket") or {}).get("reason"))
        if reason:
            ticket_reason[key] = reason
        if key not in primary_keys:
            errors.extend(evs)

    sigs = {}
    for e in errors:
        loc = "%s:%s:%s" % (e["file"], e["func"], e["line"])
        if e.get("ticket") is not None:
            t = e["ticket"]
            msg = "[ticket_event] event=%s stage=%s" % (t.get("event"), t.get("stage"))
            root = first_line(t.get("reason"))
        else:
            msg = e["msg"]
            root = exc_hint(e["extra"]) or ticket_reason.get((e["node"], e["tid"]))
        key = (loc, normalize(msg), normalize(root or "", 160))
        s = sigs.get(key)
        if not s:
            s = sigs[key] = {
                "location": loc, "message": normalize(msg),
                "root_hint": root[:300] if root else None,
                "count": 0, "nodes": Counter(),
                "first_seen": e["ts"], "last_seen": e["ts"],
                "hourly": Counter(), "samples": [],
            }
        s["count"] += 1
        s["nodes"][e["node"]] += 1
        s["first_seen"] = min(s["first_seen"], e["ts"])
        s["last_seen"] = max(s["last_seen"], e["ts"])
        s["hourly"][e["ts"][:13]] += 1
        if len(s["samples"]) < 2:
            s["samples"].append({
                "ts": e["ts"], "node": e["node"], "tid": e["tid"], "rid": e["rid"],
                "raw": e["msg"][:600],
                "traceback_tail": [x for x in e["extra"] if x.strip()][-12:],
            })

    out = []
    for s in sigs.values():
        nodes = s.pop("nodes")
        s["node_count"] = len(nodes)
        s["nodes"] = dict(nodes)
        s["node_types"] = sorted({"schedule" if "-schedule-" in n else "web" for n in nodes})
        s["hourly"] = dict(sorted(s["hourly"].items()))
        s["priority"], s["priority_reason"] = suggest_priority(s, rules)
        s["known_issue"] = match_known(s, known)
        out.append(s)
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    out.sort(key=lambda x: (rank[x["priority"]], -x["count"]))
    for i, s in enumerate(out, 1):
        s["id"] = "E%02d" % i

    warn_th = rules.get("warning_appendix_count", 500)
    warns = [{"location": k[0], "message": k[1], "count": c} for k, c in warn_counter.most_common() if c >= warn_th]
    deploy = {n: dict(c) for n, c in commits.items()}

    os.makedirs(args.out_dir, exist_ok=True)
    result = {
        "window": {"since": s_since, "until": s_until},
        "scanned": scanned,
        "deploy_commits": deploy,
        "total_error_records": sum(s["count"] for s in out),
        "signatures": out,
        "warnings_over_threshold": warns,
    }
    jpath = os.path.join(args.out_dir, "aggregate.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    all_commits = sorted({c for d in deploy.values() for c in d})
    lines = [
        "# matrix 日志 ERROR 聚合 (%s ~ %s)" % (s_since, s_until),
        "",
        "扫描文件 %d 个，窗口内 ERROR 记录 %d 条（ticket_event 已按 tid 合并），签名 %d 类。部署 commit: %s"
        % (len(scanned), result["total_error_records"], len(out), ", ".join(all_commits) or "未知"),
        "",
        "| ID | 建议级别 | 次数 | 节点数 | 类型 | 位置 | 消息 ⟶ 根因 | 首次 | 末次 | 已知问题 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in out[: args.top]:
        desc = s["message"][:90]
        if s["root_hint"]:
            desc += " ⟶ " + normalize(s["root_hint"], 90)
        ki = s["known_issue"]
        lines.append("| %s | %s | %d | %d | %s | `%s` | %s | %s | %s | %s |" % (
            s["id"], s["priority"], s["count"], s["node_count"], "/".join(s["node_types"]),
            s["location"], desc.replace("|", "\\|"), s["first_seen"][5:16], s["last_seen"][5:16],
            ("%s(%s)" % (ki["id"], ki["status"])) if ki else "新"))
    if len(out) > args.top:
        lines += ["", "（另有 %d 类签名未列出，见 aggregate.json）" % (len(out) - args.top)]
    if warns:
        lines += ["", "## 附录: WARNING 超阈值 (>= %d)" % warn_th, "", "| 次数 | 位置 | 消息 |", "|---|---|---|"]
        for w in warns[:20]:
            lines.append("| %d | `%s` | %s |" % (w["count"], w["location"], w["message"][:100].replace("|", "\\|")))
    mpath = os.path.join(args.out_dir, "aggregate.md")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(mpath)
    print(jpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
