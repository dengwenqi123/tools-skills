---
name: ai-weekly-report
description: vela-tools → VelaAgent AI 分析的可重复工作流：出周报（分析量与耗时、9 个链路检查点、5 类执行问题、按垂域的准确率/反馈/已解决、AI 复核）或任意时间窗的复核报告（源码核对准确率），产出 report.md + 单文件 report.html；再把问题拆成逐单明细、写入 tools-bug-list 缺陷库、推动修复并核对改动是否生效。用户说「AI 周报」「跑周报」「隔夜 / 昨晚到今天的分析准不准」「检查改动是否生效」「问题入缺陷库」时使用。
---

# AI 分析周报与问题闭环

一轮完整工作分五步：**出报告 → 拆明细 → 入缺陷库 → 修复上线 → 核对生效**。每一步都可以单独跑。
术语见仓库根 `CONTEXT.md`「AI 分析周报」「垂域指标看板」；链路基线见 `plan_design/vela-tools到VelaAgent完整调用链梳理.md`。

**默认只读**：不写 mios_db、不改 multica、不改 Jira。会写线上数据或节点环境的步骤（同步 agent、改 ACL、装工具、补发 Jira 报告）必须先告诉用户影响、拿到确认再做。

## 开工前（每次都做）

1. 先提问对齐，给推荐答案，用户常回「采纳默认」。至少确认：时间窗口与归属字段、产物位置、准确率口径（反馈 / AI 复核 / 源码核对）、是否抽样。
2. 环境检查：
   - 本机 VT 仓库 `.venv`；`~/.kiro/settings/mcp.json` 里有 vela-mcp 的 `VELA_MCP_USERNAME/TOKEN`（`q.py` 走同一 SQL 接口，结果落文件）。
   - `ssh dengwenqi@me` 免密，me 上 `multica` 登录的是内网 `http://vela.agent.xiaomi.srv`（workspace `vela`），bug_agents 在 `~/data/work/bug_agents`（`BUG_AGENTS` 可覆盖）。本机 multica 是公网登录，不能用。
   - Jira 只能本机走 VT 的 `JiraClient`（CAS cookie）；在 me 上直连会 302 到 CAS。
3. 线上采集器是否在跑：`SELECT MAX(collected_at) FROM issue_trace_summary`。停采也不影响出报告（summary 统一用原始数据重算），但要写进「数据缺口」。

## 第 1 步：出报告

```bash
S=.kiro/skills/ai-weekly-report/scripts; PY=.venv/bin/python
export AIW_WORK=/tmp/aiweekly AIW_START=2026-10-07 AIW_END=2026-10-13   # 窗口含两端，按 bridge.created_at；也可写到小时 '2026-10-14 18:00:00'
$PY $S/export.py                                    # mios_db → $AIW_WORK/db/*.json + issues.lst
ssh dengwenqi@me 'mkdir -p ~/tmp/aiweekly' && scp $S/fetch_raw.py $AIW_WORK/issues.lst dengwenqi@me:tmp/aiweekly/
ssh dengwenqi@me 'cd ~/tmp/aiweekly && multica agent list --output json > agents.json && multica runtime list --output json > runtimes.json && python3 fetch_raw.py issues.lst raw > fetch.log 2>&1; tar czf raw.tgz raw agents.json runtimes.json'
scp dengwenqi@me:tmp/aiweekly/raw.tgz $AIW_WORK/ && (cd $AIW_WORK && tar xzf raw.tgz)
DISABLE_APSCHEDULER=true $PY $S/build_summary.py    # 用 VT issue_trace.builder 重算 summary
$PY $S/jira_status.py && $PY $S/jira_phone.py       # Jira 实时状态（jira.n + jira-phone）
$PY $S/prep_review.py fb                            # VelaAgent 评论里的用户反馈
$PY $S/analyze.py                                   # → metrics.json / rows.json，并打印要点
```

**准确率复核**（二选一，报告会按 verdict 自动切换显示口径）：
- **周报（有反馈、有已解决单）**：`prep_review.py pick` → `jira_comments.py ${=KEYS}` → `prep_review.py pack`，每个分析量 ≥5 的垂域抽 5 单，交 2 个子 agent 对照人工依据判 `一致/部分一致/不一致/无法判定`。
- **短窗口（隔夜，几乎没有反馈）**：全部单都复核，判 `源码核对-成立/存疑/不成立/无法核对`。子 agent 到 vela16 读 AI 引用的 1–3 处源码；禁止在根目录做 find / grep -r，先 `REPO_TRACE=0 repo list -p` 定位子仓库；行号对不上按版本差异处理。复核 prompt 参照 `reports/ai-weekly/2026-10-15-overnight/` 那一次。
- 结果按垂域写到 `$AIW_WORK/review/<垂域>.json`。

**写结论** `$AIW_WORK/findings.json`（`headline` / `process` / `exec_notes` 5 类 / `review_note` / `gaps` / `actions`）。数字只引用 metrics.json；每条流程异常都要抽 2–3 单看原始评论和 Jira 实际内容后再下结论。

```bash
AIW_TITLE='AI 分析报告（隔夜）' AIW_WINDOW_LABEL='2026-10-14 18:00 ~ 2026-10-15 07:30' \
  $PY $S/render.py reports/ai-weekly/<YYYY-Www 或 日期-描述>/       # 周报可省略两个变量
```
HTML 每个看板右上角有 💡 说明，文字来自 `metric_doc.py`；口径一改，这里要同步改。渲染后用无头 Chrome 截图检查一次。

## 第 2 步：拆明细（用户追问「是哪些单」时）

| 需求 | 脚本 | 产出 |
|---|---|---|
| 流程异常逐单时间线（越过分诊、有 FINAL 却判失败） | `anomaly_list.py <out.md>` | anomalies.md |
| 某个 MCP 工具的逐次调用 | `mcp_calls.py <out.md> [工具名,...]` | mcp-jira-calls.md |
| 命令超时逐单（区分扫源码树 / 其他命令） | `timeout_issues.py <out.md>` | timeout-issues.md |
| 工具链缺失按节点、按二进制 | `toolchain_missing.py <out.md>` | toolchain-missing.md |
| coverity 是否都提交了 Gerrit patch | `coverity_patches.py <out.md>` | coverity-gerrit-patches.md |

需要节点信息的脚本依赖 `$AIW_WORK/runtimes.json`。拆明细时先读报错原文再归类，规则分类的结果只能作为线索。

## 第 3 步：入缺陷库

`export_bugs.py /Users/dengwenqi/work/tools-bug-list <周标识> <报告目录>`，写入：
- `AI分析/测试数据/<周标识或日期>/`：报告、明细、data/。
- `AI分析/缺陷/<发现日期>-<描述>.md`，并更新 `README.md` 索引。缺陷正文在脚本的 `BUGS` 里手写，每份写清现象、已核实的事实、未核实或推测、排查方向、全部涉及单据、证据级别。
- 已有缺陷再次出现时，在原文件末尾追加「<日期> 新增单据」，不新建文件。
- 同一天重跑会覆盖同名文件。tools-bug-list 不自动提交。

## 第 4 步：修复上线

| 改动位置 | 怎么提交 | 怎么生效 |
|---|---|---|
| vela-tool-service（VT） | Gerrit：`git push origin HEAD:refs/for/dev`，提交格式：summary ≤48 字 / `VELAPLATFO-89716` / 正文 / Change-Id / Signed-off-by | 部署 `mios-system-service` 的 **Web 节点和调度节点**（Job11 回写在调度节点，A 路径提单在 Web 节点）；旧数据不会自动修正 |
| bug_agents 提示词（`sync/templates/`） | GitLab：从最新 `origin/main` 开分支，`git push -o merge_request.create -o merge_request.target=main` | 合入后在 me 上同步线上 agent（见下） |
| `vela/.claude` skill | Gerrit：`refs/for/trunk`，commit-msg hook 用 `scp -O` 或 HTTPS 下载 | 节点上的 `/home/mnt/agent-workspaces/.claude` 不会自动拉取，需要手动 pull（先确认工作区里未提交改动的归属） |
| 节点环境（工具、ACL） | 直接登录节点改 | 先 `sudo -n true` 探权限；装之前看 `apt/history.log`；改 ACL 先 `getfacl -R -p` 备份；NFS 权限要在导出机 vela16 上改 |

**同步线上 agent**（me：`~/data/va-sync/bug_agents`，配置文件 `sync/config.prod-me.yaml`）：
1. 先把 `config.prod-me.yaml` 和仓库 `origin/main:sync/config.yaml` 的受管字段（mcp_config、model、overrides）对齐。这个文件不在 git 里，不会自动跟上。
2. `multica agent list --output json > backup-<ts>.json` 备份。
3. 用 `scan.load_config` + `scan.scan_domains` + 线上 agent UUID 渲染，逐个 agent 比对 instructions **和** mcp_config，确认只多出这次的改动，再执行 `python3 sync/sync_squad.py --config sync/config.prod-me.yaml`。
4. 同步后再拉一次 agent 列表，和备份逐字段比对；不该变的字段变了，就用备份写回（mcp_config 用 `--mcp-config-file`，临时文件 0600、用完删除）。

## 第 5 步：核对生效

- **静态**：节点上工具和软链是否在、目录能否写；线上 agent 的 instructions 和 mcp_config 内容是否正确；MR / Gerrit 是否已合入；节点上 skill 仓库的 HEAD 是否已经拉到新提交。
- **行为**：拉改动后新跑的 run（`fetch_raw.py`，放到另一个 raw 目录），和改动前对比：
  ```bash
  AIW_BEFORE=/tmp/aiweekly/raw AIW_AFTER=/tmp/aiweekly/raw_v AIW_CUT=2026-10-15T00:10 \
    AIW_RUNTIMES=/tmp/aiweekly/runtimes.json python3 $S/verify_runs.py out.json
  ```
  比较 repo list / git grep 用法、根目录搜索与超时、工具缺失、MCP 报错、compile_kb 失败等指标。
- 样本少时只说明「有没有在起作用」，不下定量结论；结果写成 `changes-verification-<日期>.md`，同时拷一份到 tools-bug-list。

## 口径

- 需主动分析 = 非 circle 且非 skip_analysis。小队模式 = 需主动分析、且不是 coverity（单 Agent），也不是按路由配置直派 solver 的单（如 testcase → vtf-solver，判定方式是首个 task 就是 solver）。后两类不过分诊，不算异常。
- 链路完整走通 = 已分析 + 有垂域 + 有 solve + 有 FINAL + 已回写。
- 准确率只指反馈口径（VT 表与 VelaAgent 评论里的「评价：」合并），<5 条显示样本不足。AI 复核正确率 =（一致 + 0.5 × 部分一致）÷ 可判定数；源码核对成立率 = 成立 ÷（成立 + 存疑 + 不成立），存疑不折半。三种口径分开展示，不能互相替代。
- 已解决 = Jira resolution 非空或状态类别为 done。
- 执行问题 5 类是对 tool_result 文本做规则推断，页面上标「疑似」；分类名必须和规则实际匹配的内容一致。

## 坑（踩过的）

- **台账显示成功不等于结论正确**：VT 曾把 router 的中间评论当成结论写进 Jira（VELAPLATFO-98221）；也出现过写到错误的 Jira 实例（P65ZK-1204）。判断前要去 Jira 和 VelaAgent 核对实际内容。
- **线上 summary 停采前留下的是中间态**（如 task_count=1），会误报「未接诊却已分析」，所以统一用原始数据重算。
- **结论要用全量数据核对**，不能凭样例下结论：例如「FINAL 都晚于判失败」，全量核对后只有一半如此。
- **先读报错原文再归因**：`tool_use_error` 正文是 `No such tool available`，说明工具没注册，不是鉴权问题；`不是 git 仓库` 的原因是 `.repo/projects` 权限 700，不是源码缺 `.git`。
- **`repo` 命令必须加 `REPO_TRACE=0`**：除 vela16 外，节点都写不了 `.repo/TRACE_FILE`，不加就静默失败。验证命令时要同时看退出码和 stdout。
- **zsh 不会按空格拆分变量**：传多个 key 用 `${=KEYS}`。
- **`fetch_raw.py` 有缓存**：`raw/<key>.json` 已存在就跳过，跨周复用工作目录前先清空 raw/。
- **`q.py` 单次最多返回 1000 行**：`export.py` 遇到截断会报错，量大时分天导出；部分列组合查询会报 500，拆成几次查询。
- **agent 反复改状态失败的根源在 daemon**（`server/internal/daemon/execenv/runtime_config.go`）：它注入的通用流程要求改状态，但 Jira 同步过来的 issue 是只读的。查提示词来源时，sync 模板和 daemon 注入的内容都要看。
- **jira-phone 项目只维护一份表**：`application/crashlog/services/jira_project_router.PHONE_JIRA_PREFIXES`，其他地方都从它派生。
