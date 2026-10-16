"""把周报测试数据与发现的缺陷写入 tools-bug-list 仓库 (不 commit / 不 push).
用法: AIW_WORK=... AIW_START=.. AIW_END=.. python export_bugs.py <tools-bug-list 路径> <周标识 如 2026-W42> <报告目录>
布局: AI分析/测试数据/<周>/ (报告+明细+指标)   AI分析/缺陷/<日期>-<描述>.md (每个缺陷一份, 含全部涉及单据)
缺陷正文在 BUGS 里手写, 只写已核实的事实, 推测单列; 涉及单据由数据自动填充.
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json, re, shutil, sys
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A   # 复用同一套指标/分类 (issue_cat 保存了每类问题涉及的 key)
from load import bridge, J

REPO, WEEK, REPORT = sys.argv[1], sys.argv[2], sys.argv[3]
TODAY = date.today().isoformat()
W = f'{A.WIN[0]} ~ {A.WIN[1]}'
rows = {r['key']: r for r in json.load(open(f'{AIW}/rows.json'))}
bk = {b['jira_key']: b for b in bridge}
JIRA = {'n': 'https://jira.n.xiaomi.com/browse/', 'phone': 'https://jira-phone.mioffice.cn/browse/'}
anom = A.out['anomalies']
sub = lambda cat, name: sorted(A.issue_cat[cat]['sub'][name]['issues'])
mcp = json.load(open(f'{AIW}/mcp_jira_calls.json'))
fb_have = {f['issue_key'] for f in A.fb}
fb_only = sorted(k for k in json.load(open(f'{AIW}/db/multica_fb.json')) if k in rows and k not in fb_have)
no_wb = sorted(k for k, r in rows.items() if r['state'] == 'analyzed' and not r['commented'] and not r['circle'])
FINAL = re.compile(r'(?m)^[\s>*#]*\[FINAL\s*#')


def comments(k):
    return json.load(open(f'{AIW}/raw/{k}.json'))['comments']


# B 细分: 有无 ROUTE REQUEST
Bk = anom['B intake 越过分诊/求解自行出结论']['keys']
B1 = sorted(k for k in Bk if any('ROUTE REQUEST' in (c['content'] or '')[:40] for c in comments(k)))
B3 = sorted(set(Bk) - set(B1))
# C 细分: FINAL 早于/晚于判失败
Ck = anom['C 已出 FINAL 却被判失败(未回写)']['keys']
C_early, C_late = [], []
for k in Ck:
    h = max(x['at'] for x in J(bk[k]['analysis_retry_history'], []))
    f = max(c['created_at'] for c in comments(k) if FINAL.search(c['content'] or ''))
    (C_early if f[:19] < h else C_late).append(k)
C_early.sort(); C_late.sort()
mcp_keys = sorted({c['issue'] for c in mcp})
assert (len(B1), len(B3), len(C_early), len(C_late), len(no_wb), len(fb_only), len(mcp_keys)) == (42, 48, 12, 11, 42, 51, 17) \
    or os.environ.get('SKIP_ASSERT'), (len(B1), len(B3), len(C_early), len(C_late), len(no_wb), len(fb_only), len(mcp_keys))

# (文件名描述, 标题, 模块, 现象, 已核实, 推测/未核实, 建议, keys, 证据级别)
BUGS = [
    ('intake发ROUTE-REQUEST后router未执行', 'intake 已发 ROUTE REQUEST，router 始终没有产出 DECISION，最终由 intake 自行出结论', 'VelaAgent 小队编排',
     'ROUTE REQUEST 发出后没有 bug-router 的 run；VT 约 3 小时后发「请继续」评论，intake 被二次唤醒后跳过分诊 / 求解直接出结论。后果：垂域缺失（记为未路由）、没有 router / solver 的 task。',
     ['逐单时间线（见 `测试数据/%s/anomalies.md`）：第一轮 intake run 已 completed，评论区有 [ROUTE REQUEST #1]，没有 bug-router 的 run，也没有 [ROUTING_DECISION]。' % WEEK,
      '这些单的评论里都没有对 bug-router 的 mention（`mention://agent/c88fd942…`）。'],
     ['intake 发 ROUTE REQUEST 时没带 @bug-router 是最直接的解释，但这里只检查了评论文本，没有核对 intake 实际执行的发评论命令。'],
     ['核对 intake 发 ROUTE REQUEST 的命令是否带 mention；挑 1–2 单复现。', 'VT 的「请继续」评论加闸门：已有 ROUTE REQUEST 且无 DECISION 时，不应让 intake 直接出结论。'],
     B1, '时间线逐单核对'),
    ('intake未发ROUTE-REQUEST直接出结论', 'intake 首轮结束时没有任何 marker，二次唤醒后跳过分诊 / 求解直接出结论', 'VelaAgent 小队编排',
     '首轮 intake run completed 但评论区没有 [ROUTE REQUEST]；VT 发「请继续」后 intake 直接写结论。crashlog 44 单、autotest 4 单。同样导致垂域缺失、无 [FINAL] marker。',
     ['逐单时间线：首轮 intake run completed 且无任何 marker 评论；「请继续」之后出现 intake 的长评论。', '48 单里有 35 单能看到这条 intake 的无 marker 长评论（>200 字）。'],
     ['另 13 单没有观察到长评论，结论写在哪里没查。', '首轮 intake 为什么空转（没发 ROUTE REQUEST 就结束）没有查 run-messages。'],
     ['抽 2–3 单看首轮 intake 的 run-messages，确认为什么没发 ROUTE REQUEST。', '同上：「请继续」闸门。'],
     B3, '时间线逐单核对（首轮空转原因未查）'),
    ('已出FINAL但poller未认判失败', '评论区已有 [FINAL]，bridge 仍判 analyzing 失败，结论未回写 Jira（FINAL 早于判失败）', 'vela-tools poller / multica_bridge',
     'bridge 用完 4 次重试判 failed，但 [FINAL] 的创建时间早于判失败时间，且最后一个 run 为 completed。全部是 crashlog。',
     ['12 单逐一比对：FINAL 创建时间 < analysis_retry_history 最后一次时间。', '这些 FINAL 都是子评论（parent_id 非空），与正常 analyzed 单的形态一致。'],
     ['`_has_real_conclusion` / `_is_real_conclusion` 为什么没有认这条结论没有查，可能与触发评论子树、「请继续」评论重发覆盖 trigger_comment_id 有关（代码注释里提到过 WDUFI-23）。'],
     ['读 `multica_bridge/poller.py: _has_real_conclusion` 和 `jira_comment._latest_agent_root_cause`，用这 12 单做回放测试。'],
     C_early, '时间先后已核实；poller 为何不认未查'),
    ('FINAL晚于重试耗尽判失败', '链路超过重试窗口：[FINAL] 在 bridge 判失败之后才出现', 'vela-tools poller / VelaAgent 排队',
     'bridge 重试 4 次后判 failed，之后 [FINAL] 才产出，结论没有回写 Jira。全部是 crashlog。',
     ['11 单逐一比对：FINAL 创建时间 > 判失败时间。', '这 11 单的重试历史里都出现过 queued_expired（排队过期见另一份缺陷）。'],
     ['判失败后晚到的 FINAL 是否应被补回写，没有现成机制，未查代码。'],
     ['判失败后仍持续监听该 issue，晚到的 FINAL 补回写；或延长 analyzing 超时。'],
     C_late, '时间先后已核实'),
    ('已分析但未回写Jira', 'bridge 状态 analyzed，但 jira_commented=0，AI 分析报告没有回写 Jira', 'vela-tools poller / jira_comment',
     '本周 42 单已分析未回写：jira-phone 25 单（Q65ZH / Q61NHQ / MIWEARRTOS 等），jira.n 17 单（其中 autotest 12 单）。',
     ['数据来自 multica_jira_bridge：analysis_state=analyzed 且 jira_commented=0。'],
     ['回写失败原因没有查（Job11 日志 / write_jira_comment_enabled 开关 / jira-phone 鉴权均未核对）。'],
     ['查 Job11 对这些 key 的日志；jira-phone 与 jira.n 的回写路径分别核对。'],
     no_wb, '台账数据；失败原因未查'),
    ('Jira取单MCP未注册进agent会话', 'mi-jira / mi-jira-phone / dfx-mi-jira 取单 MCP 在 agent 会话中不存在（No such tool available）', 'VelaAgent runtime MCP 配置',
     'agent 调用这三个工具共 21 次，全部返回 `No such tool available`，随后改走 Bash（11 次）、ListMcpResourcesTool、WebFetch 等兜底；bug-intake 12 次、coverity-fixer 9 次。',
     ['21 次调用的返回正文逐条核对，均为 `<tool_use_error>Error: No such tool available: …`，即工具未注册，不是鉴权失败。', '逐次明细见 `测试数据/%s/mcp-jira-calls.md`。' % WEEK, '同期 gdbmcp 调用 523 次仅失败 1 次，说明 MCP 机制本身可用。'],
     ['jira-mcp-setup skill 被 119 单读取，疑似与此有关（agent 找不到 Jira MCP 去读安装 skill），未核对。', '哪些 runtime 机器没加载这些 MCP 没有查。'],
     ['查 runtime 的 MCP 配置（.claude.json / mcp 注册）是否包含这三个 server；按 task 的 runtime 对照。'],
     mcp_keys, '返回正文逐条核对'),
    ('任务排队过期taskexpiredinqueue', '任务在队列中过期（task expired in queue），触发大量重跑', 'VelaAgent 平台 / runtime 容量',
     '199 单出现排队过期，bridge 记录 queued_expired 重跑 434 次、analyzing_timeout 278 次；排队时长 P90 约 9.7 小时。',
     ['run 错误字段与 bridge.analysis_retry_history 中均出现 queued_expired / `task expired in queue`。'],
     ['过期原因是 runtime 容量不足还是任务被反复重发堆积，没有区分。'],
     ['按 runtime 统计排队时长与并发；确认过期阈值；VT 重跑是否在放大排队。'],
     sub('环境', '排队过期 task expired in queue'), 'run 错误字段'),
    ('命令超时全树find-grep', 'Bash 2 分钟上限下，agent 在全树 find / grep 源码时频繁超时', 'VelaAgent runtime / skill 提示',
     '207 单出现 `Command timed out after 2m 0s` 或退出码 143/144，典型命令是对 `/home/mnt/agent-workspaces` 全树 find / grep。',
     ['抽样命令：`find /home/mnt/agent-workspaces -name ...`、`grep -rn ... /home/mnt/agent-workspaces/p65`，返回 Exit code 143 + Command timed out。'],
     ['207 为规则分类的结果（带「疑似」），其中混有 timeout 命令自身超时。'],
     ['skill 里给出常用源码路径，避免全树搜索；或建索引 / 调大超时。'],
     sub('环境', '命令超时 (2min 上限 / 全树 find/grep)'), '抽样核实；全量为规则分类'),
    ('源码树无git历史', 'agent-workspaces 下的源码目录不是 git 仓库，git log / blame 不可用', 'VelaAgent runtime 环境',
     '92 单出现 `fatal: 不是 git 仓库` 或 git 命令退出码 128。',
     ['抽样：`/home/mnt/agent-workspaces/vela` 下 `ls -la .git` 报无此文件；另有 `fatal: 不是 git 仓库（或者直至挂载点 /home/mnt 的任何父目录）`。'],
     ['全部 92 单是否都由「非 git 仓库」导致未逐个核实（规则含 `fatal:` 与 Exit code 128 两类）。'],
     ['源码树补 .git，或提供 git 只读镜像。'],
     sub('代码下载', '源码树非 git 仓库 / git 历史不可用'), '抽样核实；全量为规则分类'),
    ('multica-CLI无权访问或issue未指派小队', 'agent 调用 multica issue status / squad activity 报「无权访问」或「issue is not assigned to a squad」', 'VelaAgent 权限 / 编排',
     '165 单。典型：`multica issue status <id> in_progress` → Exit code 3「无权访问该资源」；`multica squad activity` → Exit code 5「issue is not assigned to a squad」。',
     ['抽样返回正文如上，来自 bug-intake。'],
     ['权限不足与 issue 未指派小队是两个原因，165 单没有拆开；是否影响最终结果未查。'],
     ['拆分两类报错；确认 intake 使用的身份对这些 issue 的权限。'],
     sub('工具', 'multica CLI 无权限 / issue 未指派小队'), '抽样核实；全量为规则分类'),
    ('agent-files目录权限不足', 'agent 在 /home/mnt/agent-files 下创建目录报「权限不够」，sudo 需要密码', 'runtime NFS 权限',
     '16 单。典型：`mkdir -p /home/mnt/agent-files/<JIRA>/` → 无法创建目录，权限不够。',
     ['抽样命令与返回如上。'],
     ['与仓库 AGENTS.md 记载的 NFS ACL mask 被压成 r-x 的症状一致，但本次没有在机器上用 getfacl 核对。'],
     ['在 vela16 上 `getfacl` 检查目标目录；按 AGENTS.md 的修复步骤处理。'],
     sub('环境', 'agent-files 目录权限不足 (NFS ACL)'), '抽样核实；根因未验证'),
    ('经验库目录只读', 'agent 无法写入 .claude/experience-base（目录只读）', 'runtime 权限',
     '17 单。`ls -ld` 显示 `dr-xr-xr-x … experience-base`；`touch` 返回无法 touch。',
     ['抽样：Q61NHQ-27146 中 `dr-xr-xr-x 5 1001 1001 … experience-base`；VELAPLATFO-97710 中 touch 失败。'],
     ['`agent-workspace`（单数）与 `agent-workspaces`（复数）两个目录都出现过，是否同一份数据未查。'],
     ['确认沉淀 Agent 对经验库的写权限设计。'],
     sub('环境', '经验库目录只读'), '抽样核实'),
    ('分析工具链缺失', 'runtime 缺少 gdb-multiarch / arm-none-eabi-addr2line 等工具', 'runtime 环境',
     '23 单出现 `未找到命令` / 退出码 127。',
     ['抽样：`arm-none-eabi-addr2line: 未找到命令`、`gdb-multiarch: 未找到命令`、`/usr/bin/gdb` 存在但无 multiarch。'],
     ['部分机器可能在 prebuilts 下有同名工具，路径不一致，未逐机核对。'],
     ['统一 runtime 工具链或在 skill 中固定使用 prebuilts 路径。'],
     sub('环境', '工具链缺失 (addr2line / gdb-multiarch)'), '抽样核实'),
    ('skill文件不存在', 'agent 读取 SKILL.md 时文件不存在（bug-router / bug-analyze）', 'skill 同步',
     '2 单：`/home/mnt/agent-workspaces/.claude/skills/bug-router/SKILL.md`、`…/bug-analyze/SKILL.md` 返回没有那个文件或目录。',
     ['两条命令与返回正文已核对。'],
     ['是 skill 未同步到该 runtime，还是 skill 已改名，没有查。'],
     ['核对 skill git 同步（cron pull）在各 runtime 的状态。'],
     sub('skill', 'skill 文件不存在 (SKILL.md 缺失)'), '返回正文逐条核对'),
    ('反馈未同步到ai_feedback_records', 'VelaAgent 评论里的用户反馈没有同步进 ai_feedback_records', 'vela-tools Job14 feedback_collector',
     '本周 55 单有反馈，其中 51 单只存在于 VelaAgent 评论（「评价：有用 / 部分有用 / 无用」），ai_feedback_records 里没有。基于该表的准确率会严重低估反馈量。',
     ['对比：评论里能解析出「评价：」的 issue 与 ai_feedback_records.issue_key 的差集 = 51 单。'],
     ['未核对 Job14 的运行日志，不清楚是没跑、跑了但过滤掉，还是评论格式不被识别。'],
     ['查 `multica_bridge/feedback_collector.py` 的采集条件与日志；评论格式「评价：…」是否纳入解析。'],
     fb_only, '集合差已核实；原因未查'),
]
BUGS.append(('线上采集器停采distill_gerrit缺列', 'issue_trace 采集器从 10-12 22:39 起停采，线上 issue_trace_summary 缺 distill_gerrit 列', 'vela-tools issue_trace / 迁移',
             '线上 `issue_trace_summary` 缺少 `distill_gerrit` 列（迁移 `2026-10-12-domain-metrics-distill-gerrit-up.sql` 未执行），采集器 UPSERT 与看板读取报 1054；summary 最新 assigned_at 停在 10-12 22:39。',
             ['information_schema 查询确认线上表没有该列；MAX(assigned_at)/MAX(collected_at) 分别停在 10-12 22:39 / 10-13 22:50。'],
             ['采集器的具体报错日志没有查，「报 1054」是由缺列推断。'],
             ['在 mios_db 执行迁移补列；采集器会自行补回数据。'],
             [], '表结构已核实；报错日志未查'))


def link(k):
    r = rows.get(k, {})
    s = bk.get(k, {}).get('jira_server', 'n')
    return f"[{k}]({JIRA[s]}{k})", f"[VelaAgent]({r.get('url') or bk.get(k, {}).get('multica_issue_url') or ''})"


def table(keys):
    if not keys:
        return '（无）'
    o = ['| Jira | VelaAgent | 入口/类型 | 垂域 | bridge 状态 |', '|---|---|---|---|---|']
    for k in keys:
        a, b = link(k); r = rows.get(k, {})
        o.append(f"| {a} | {b} | {r.get('entry', '—')}/{r.get('cat', '—')} | {r.get('domain', '—')} | {r.get('state', '—')} |")
    return '\n'.join(o)


bug_dir = os.path.join(REPO, 'AI分析', '缺陷'); data_dir = os.path.join(REPO, 'AI分析', '测试数据', WEEK)
os.makedirs(bug_dir, exist_ok=True); os.makedirs(data_dir, exist_ok=True)
index = []
for slug, title, mod, phenom, ok, guess, advice, keys, level in BUGS:
    fn = f'{TODAY}-{slug}.md'
    body = [f'# {title}', '', f'- 发现日期：{TODAY}', f'- 数据窗口：{W}（周报 {WEEK}）', f'- 所属模块：{mod}', f'- 涉及单据：{len(keys)}', f'- 证据级别：{level}', '- 状态：待研发确认', '',
            '## 现象', '', phenom, '', '## 已核实的事实', ''] + [f'- {x}' for x in ok] + ['', '## 未核实 / 推测', ''] + [f'- {x}' for x in guess] + ['', '## 建议排查方向', ''] + [f'- {x}' for x in advice] \
           + ['', '## 数据来源', '', f'- 周报与明细：`AI分析/测试数据/{WEEK}/`', '- 数据口径：bridge 台账 + me 机器 multica CLI 原始数据（run-messages / 评论）按 VT 采集器同一 builder 重算；错误类别为规则推断（疑似）', '',
              f'## 涉及单据（{len(keys)}）', '', table(keys), '']
    open(os.path.join(bug_dir, fn), 'w').write('\n'.join(body))
    index.append((fn, title, mod, len(keys), level))
open(os.path.join(bug_dir, 'README.md'), 'w').write('# AI 分析缺陷\n\n命名：`<发现日期>-<缺陷描述>.md`；每份含现象、已核实的事实、未核实/推测、排查方向和全部涉及单据。\n\n'
    + '| 文件 | 缺陷 | 模块 | 单数 | 证据级别 |\n|---|---|---|---|---|\n' + '\n'.join(f'| [{f}]({f}) | {t} | {m} | {n} | {l} |' for f, t, m, n, l in index) + '\n')
for f in ('report.md', 'report.html', 'anomalies.md', 'mcp-jira-calls.md'):
    shutil.copy(os.path.join(REPORT, f), data_dir)
shutil.copytree(os.path.join(REPORT, 'data'), os.path.join(data_dir, 'data'), dirs_exist_ok=True)
print('bugs', len(index), '->', bug_dir); print('data ->', data_dir)
