#!/usr/bin/env python3
"""
Gerrit relation chain 批量打分（REST API 版，仅依赖 python3 标准库）。

从指定 change 出发，沿 relation chain（上游 + 下游）遍历，收集所有仍为
OPEN(NEW) 的 change，对其当前 patchset 一次性提交多个 label 投票。
遇到 MERGED / ABANDONED 的 change 不再沿它继续扩展。

默认只打印计划（dry-run），加 --apply 才真正打分。

凭据只从环境变量读取：
  GERRIT_HTTP_USER       Gerrit 用户名（如 miot-robot）
  GERRIT_HTTP_PASSWORD   该用户在 Gerrit Settings -> HTTP Credentials 生成的密码
  GERRIT_URL             可选，默认 https://gerrit.pt.mioffice.cn

退出码：0 成功；1 参数/查询/打分失败；2 环境变量未设置；3 认证失败
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://gerrit.pt.mioffice.cn"
DEFAULT_LABELS = "Vela-AutoTest=+1,Verified=+1,Code-Review=+2"
MAX_CHANGES = 200  # 防止异常数据导致无限遍历

ENV_HELP = """\
错误: 未设置 Gerrit HTTP 凭据环境变量: {missing}

请先在 shell 配置中设置（例如追加到 ~/.bashrc 或 ~/.zshrc，然后重新打开终端或 source）:

    export GERRIT_HTTP_USER='miot-robot'
    export GERRIT_HTTP_PASSWORD='<Gerrit HTTP 密码>'
    # 可选，默认 {default_url}
    # export GERRIT_URL='{default_url}'

HTTP 密码获取方式: 用该账号登录 Gerrit -> Settings -> HTTP Credentials -> Generate New Password。
注意该账号需要具备所打 label（如 Verified / Vela-AutoTest / Code-Review+2）的权限。"""


class GerritError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class Gerrit:
    def __init__(self, base_url, user, password, timeout=30):
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.timeout = timeout

    def _request(self, method, path, body=None):
        url = f"{self.base}/a{path}"
        data = None
        headers = {"Authorization": self.auth, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            raise GerritError(f"HTTP {e.code} {method} {path}: {detail}", e.code)
        except urllib.error.URLError as e:
            raise GerritError(f"无法连接 {self.base}: {e.reason}")
        # Gerrit JSON 响应带 XSSI 前缀 )]}'
        if raw.startswith(")]}'"):
            raw = raw[4:]
        return json.loads(raw) if raw.strip() else None

    def change(self, number):
        return self._request("GET", f"/changes/{number}?o=CURRENT_REVISION")

    def related(self, number):
        data = self._request("GET", f"/changes/{number}/revisions/current/related")
        return (data or {}).get("changes", [])

    def review(self, project, number, revision, labels, message=None):
        cid = urllib.parse.quote(f"{project}~{number}", safe="")
        body = {"labels": labels}
        if message:
            body["message"] = message
        return self._request("POST", f"/changes/{cid}/revisions/{revision}/review", body)


def parse_change(arg):
    """支持: 10466025 / 10466025,3 / https://gerrit.../c/proj/+/10466025[/3]"""
    arg = arg.strip()
    m = re.fullmatch(r"(\d+)(?:,\d+)?", arg)
    if m:
        return m.group(1)
    m = re.search(r"/\+/(\d+)", arg) or re.search(r"[/#](\d{3,})(?:/\d+)?/?$", arg)
    if m:
        return m.group(1)
    raise ValueError(f"无法识别的 change: {arg}")


def parse_labels(spec):
    labels = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        m = re.fullmatch(r"([A-Za-z0-9_-]+)=([+-]?\d+)", item)
        if not m:
            raise ValueError(f"label 格式错误: {item}（应为 Name=+N）")
        labels[m.group(1)] = int(m.group(2))
    if not labels:
        raise ValueError("labels 不能为空")
    return labels


def collect(gerrit, root, follow_chain):
    """BFS 遍历 relation chain，返回 (open_changes, skipped)。"""
    infos, order = {}, []
    queue, seen = [root], {root}
    while queue:
        if len(infos) >= MAX_CHANGES:
            print(f"警告: 已达上限 {MAX_CHANGES} 个 change，停止遍历", file=sys.stderr)
            break
        num = queue.pop(0)
        info = gerrit.change(num)
        infos[num] = info
        order.append(num)
        if not follow_chain or info.get("status") != "NEW":
            continue
        for rel in gerrit.related(num):
            n = str(rel.get("_change_number", ""))
            if n and n not in seen:
                seen.add(n)
                queue.append(n)

    open_changes, skipped = [], []
    for num in order:
        info = infos[num]
        rev = info.get("current_revision")
        ps = (info.get("revisions") or {}).get(rev, {}).get("_number")
        item = {
            "number": num,
            "patchset": ps,
            "revision": rev,
            "project": info.get("project"),
            "branch": info.get("branch"),
            "status": info.get("status"),
            "subject": info.get("subject", ""),
        }
        (open_changes if item["status"] == "NEW" else skipped).append(item)
    return open_changes, skipped


def fmt_labels(labels):
    return ", ".join(f"{k}={v:+d}" for k, v in labels.items())


def main():
    ap = argparse.ArgumentParser(description="Gerrit relation chain 批量打分（REST API）")
    ap.add_argument("change", help="change 号、change,patchset 或 Gerrit change URL")
    ap.add_argument("--apply", action="store_true", help="真正打分（默认仅 dry-run 打印计划）")
    ap.add_argument("--no-chain", action="store_true", help="只处理该 change 本身，不遍历 relation chain")
    ap.add_argument("--labels", default=DEFAULT_LABELS, help=f"要打的 label，默认 {DEFAULT_LABELS}")
    ap.add_argument("--message", default=None, help="随投票附带的评论（可选）")
    args = ap.parse_args()

    user = os.environ.get("GERRIT_HTTP_USER", "").strip()
    password = os.environ.get("GERRIT_HTTP_PASSWORD", "").strip()
    missing = [n for n, v in (("GERRIT_HTTP_USER", user), ("GERRIT_HTTP_PASSWORD", password)) if not v]
    if missing:
        print(ENV_HELP.format(missing=", ".join(missing), default_url=DEFAULT_URL), file=sys.stderr)
        return 2

    try:
        root = parse_change(args.change)
        labels = parse_labels(args.labels)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    gerrit = Gerrit(os.environ.get("GERRIT_URL", DEFAULT_URL), user, password)

    scope = "单个 change" if args.no_chain else "relation chain"
    print(f"正在从 change {root} 收集 {scope} ...", file=sys.stderr)
    try:
        open_changes, skipped = collect(gerrit, root, not args.no_chain)
    except GerritError as e:
        if e.status == 401:
            print("错误: Gerrit 认证失败（401）。请检查 GERRIT_HTTP_USER / GERRIT_HTTP_PASSWORD "
                  "是否正确，HTTP 密码是否已在 Gerrit 中重新生成过。", file=sys.stderr)
            return 3
        print(f"错误: 查询失败: {e}", file=sys.stderr)
        return 1

    print(f"\n计划打分: {fmt_labels(labels)}")
    print(f"OPEN patch 共 {len(open_changes)} 个:")
    for c in open_changes:
        print(f"  {c['number']},{c['patchset']}  [{c['project']} @ {c['branch']}]  {c['subject']}")
    if skipped:
        print(f"跳过（非 OPEN）{len(skipped)} 个:")
        for c in skipped:
            print(f"  {c['number']}  {c['status']}  {c['subject']}")

    if not open_changes:
        print("\n没有需要打分的 OPEN change。", file=sys.stderr)
        return 1

    if not args.apply:
        print("\n(dry-run：未实际打分。确认无误后加 --apply 执行)")
        return 0

    failures = []
    for c in open_changes:
        tag = f"{c['number']},{c['patchset']}"
        try:
            gerrit.review(c["project"], c["number"], c["revision"], labels, args.message)
            print(f"[OK]   {tag}  {fmt_labels(labels)}")
        except GerritError as e:
            failures.append(tag)
            hint = "（该账号可能没有此 label 的打分权限）" if e.status == 403 else ""
            print(f"[FAIL] {tag}  {e}{hint}", file=sys.stderr)

    print(f"\n完成: 成功 {len(open_changes) - len(failures)} 个，失败 {len(failures)} 个")
    if failures:
        print("失败列表: " + " ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
