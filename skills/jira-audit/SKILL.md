---
name: jira-audit
description: 检查过去 24 小时自动提单是否有问题：crash / testcase 是否漏提单（复用线上 ticket_audit 对账结果并现查复核）、jira-phone 与 jira.n 上是否有主单被 dup 超过 3 次、同一 crash 签名是否重复开单，并用 matrix 线上日志补根因，输出分级 Markdown 报告。触发：检查提单、jira 审计、漏提单、未提单、提单准确性、dup 检查、dup 超过 3、重复开单、/jira-audit。
---

# 提单审计（漏提单 + dup 堆积 + 重复开单）

只读：只用 vela-mcp 查 `mios_db`（只读）、只读 Jira、只读 matrix 日志。不改数据、不改 Jira、不改代码。
默认窗口：运行时刻往前 24 小时（滚动）；用户指定窗口时替换下文所有 `INTERVAL 24 HOUR`。

## 背景事实（无需再查）

- 漏提单判定**不要自己写口径**。线上已有对账系统 `application/ticket_audit/`，每小时跑，结果在 `ticket_audit_record`：
  - `crash_expected.py` 复刻了业务出单决策（`create_jira_status=1`、monkey_event_rule、双单拆分、jira / tools_jira 两列、48h 年龄守卫、A/B 路径）。`crash_product` 表与"该不该提单"无关，不要拿来当白名单。
  - autotest 从 `vela_autotest_task_summary.is_create_jira='Y'` 出发，比对 `test_case_failures` / `test_issues`，含排除类、合成用例、TestLibuvNet 归一。
- 场景含义（`card.py`）：A2 卡在待发送 / A3 已发出未提单 / A4 只提了部分用例 / A5 JIRA 已建未落库 / A6 流程卡在半路；C1 漏单 / C2 下游未回调 / C3 分析状态 error / C4 建单失败 / C6 两条路径都没走到 / C7 少单；G1 链路停摆 / G2 对账未运行。
- `entity_id`：crash 为 `vela_crash_resource.id`；autotest(ct_task) 为 `ct_task_id`；pending_write 为 `pending_failure_writes.id`。
- `level`（`grading.py`）：P0 仅 pipeline；open 超 24h 自动升一级；同模块同 `locate_category` ≥3 条升 P1；`业务规则跳过` 降 P3。`expire_stale`：`first_seen_at` 超 7 天的 open 自动置 expired。
- 定位字段：`locate_status`(pending/done)、`locate_category`、`locate_confidence`(高/中/低)、`locate_reason`、`locate_evidence`。
- dup 关系：`vela_platform_bug_dup(issue_key, duplicates, duplicated_by, create_time)`，`duplicates` 是主单。按 `duplicates` 聚合。
- Jira 实例：设备项目（P65ZK / Q65ZH / Q61NHQ / P62LC / O61N / MIWEARRTOS 等）在 jira-phone；VELAPLATFO 在 jira.n。
  - 首选核实源 `vela_effect_jira_basic`（两个实例都同步，含 `issue_status / assignee / summary / issue_links / updated`，`updated` 一般滞后几分钟）。
  - jira-phone 实时核实用 `mcp__dfx-jira__getJiraIssue`：`fields.issuelinks` 中 `type.name=Duplicate` 且有 `inwardIssue` 的即"被 dup"。dfx-jira 查 VELAPLATFO 返回 404。
  - vela-jira MCP 没有读 issue / JQL 工具（且曾出现 token 失效），VELAPLATFO 只用 `vela_effect_jira_basic` 核实，报告中注明"未实时核实"。
- crash 签名：`vela_crash_resource.comment_id` 与 `crash_issue_record.crash_group_key` 基本为空，签名用 `vela_crash_analysis_record.crash_id`。
- `vela_crash_resource.create_time` 无单独索引，按时间直接查会 `query_timeout`；必须先加 id 下界（见 SQL 4）。
- 报告链接：crash `http://vela.pt.xiaomi.srv/vela_crash_report?id=<resource_id>`；jira-phone `https://jira-phone.mioffice.cn/browse/<KEY>`；jira.n `https://jira.n.xiaomi.com/browse/<KEY>`。

## 工作流

所有 SQL 走 `mcp__vela-mcp__vela_sql_query(database='mios_db', limit=1000)`。互不依赖的查询并行发起。

### 1. 审计系统自检（先跑，决定结果可不可信）

```sql
-- 1a 最近一次对账时间（超过 2h 没有新 last_seen 说明对账可能停了，同时看 pipeline G1/G2）
SELECT MAX(last_seen_at) last_run FROM ticket_audit_record;

-- 1b open 记录总览
SELECT module, scenario, level, locate_status, locate_category, COUNT(*) c,
       MIN(first_seen_at) first_min, MAX(last_seen_at) last_max,
       SUM(last_seen_at < NOW() - INTERVAL 24 HOUR) stale
FROM ticket_audit_record WHERE status = 'open'
GROUP BY module, scenario, level, locate_status, locate_category
ORDER BY module, scenario, c DESC;
```

疑点（列入报告"审计系统自检"一节，只给计数和推断，不改代码）：
- `stale > 0`：open 但 24h 未再被扫到。通常是资源超出对账窗口不再检查，也未判 recovered；需抽样（SQL 3）确认是否已补单。
- `locate_status='pending'` 且 `first_seen_at` 超 2h：定位任务积压。
- pipeline 有 open 的 G1/G2：整条对账链路异常，报告置顶。

### 2. 过去 24h 的问题清单

```sql
SELECT id, module, scenario, level, entity_type, entity_id, locate_status, locate_category,
       locate_confidence, LEFT(locate_reason, 200) reason,
       JSON_UNQUOTE(JSON_EXTRACT(detail, '$.product')) product,
       JSON_UNQUOTE(JSON_EXTRACT(detail, '$.branch'))  branch,
       first_seen_at, last_seen_at
FROM ticket_audit_record
WHERE status = 'open' AND last_seen_at >= NOW() - INTERVAL 24 HOUR
ORDER BY FIELD(level, 'P0', 'P1', 'P2', 'P3'), module, scenario, first_seen_at;
```

A3 往往上百条：报告中按 `(scenario, locate_category, product, branch)` 聚合计数，每组最多列 3 个样本。crash 条目量小，逐条列出。

### 3. 现查复核（防止审计结果过时）

- crash 条目：
  ```sql
  SELECT id, product, branch, status, analysis_status, create_jira_status, jira, tools_jira,
         LEFT(remark, 120) remark, create_time, update_time
  FROM vela_crash_resource WHERE id IN (<entity_id 列表>);
  ```
  `jira` / `tools_jira` 已有真实单号（非空且不为 `-`）→ 标"已补单（审计未回收）"，计入自检；值为 `-` → 已尝试建单但 JIRA 未返回单号。
- autotest(ct_task) 条目（每组样本即可）：
  ```sql
  SELECT ct_task_id, COUNT(*) filed, GROUP_CONCAT(DISTINCT jira_issue) jiras
  FROM test_case_failures WHERE ct_task_id IN (<ct_task_id 列表>) GROUP BY ct_task_id;
  ```
  另查 `test_issues`（同样按 `ct_task_id`）；任一有记录 → "已补单"。

### 4. 重复开单（同一 crash 签名对应多个单）

```sql
SELECT a.crash_id, a.branch, COUNT(DISTINCT a.issue_key) n,
       GROUP_CONCAT(DISTINCT a.issue_key) issues, COUNT(*) resources
FROM vela_crash_analysis_record a
WHERE a.create_time >= NOW() - INTERVAL 24 HOUR
  AND a.issue_key IS NOT NULL AND a.issue_key NOT IN ('', '-')
GROUP BY a.crash_id, a.branch HAVING n > 1 ORDER BY n DESC;
```

注意区分：不同项目各一单（如 P65ZK + Q65ZH，同分支多产品）可能是按产品分单的设计，标"跨项目"；**同一项目内**多个单才是疑似重复开单，按 n 排序列出，n ≥ 3 的取 1~2 个单用 Jira 看 summary/backtrace 是否一致。

若需要回到 resource（例如找报告链接），id 下界写法：
```sql
SELECT ... FROM vela_crash_resource
WHERE id > (SELECT MAX(id) - 300000 FROM vela_crash_resource)
  AND create_time >= NOW() - INTERVAL 24 HOUR AND ...;
```
（每天约 8~9 万条，30 万覆盖约 3 天；窗口更长时按比例放大。）

### 5. dup 堆积（主单被 dup 超过 3 个）

```sql
SELECT d.duplicates main_key, COUNT(DISTINCT d.issue_key) total,
       COUNT(DISTINCT CASE WHEN d.create_time >= NOW() - INTERVAL 24 HOUR THEN d.issue_key END) new_24h,
       GROUP_CONCAT(DISTINCT d.issue_key ORDER BY d.issue_key) dup_keys, MAX(d.create_time) last_dup
FROM vela_platform_bug_dup d
WHERE d.duplicates IS NOT NULL AND d.duplicates <> ''
  AND d.duplicates IN (SELECT duplicates FROM vela_platform_bug_dup
                       WHERE create_time >= NOW() - INTERVAL 24 HOUR AND duplicates <> '')
GROUP BY d.duplicates HAVING total > 3
ORDER BY new_24h DESC, total DESC;
```

对每个命中主单核实（见背景事实"Jira 实例"）：

```sql
SELECT issue_key, issue_status, assignee, updated, LEFT(summary, 80) summary
FROM vela_effect_jira_basic WHERE issue_key IN (<主单 + dup_keys>);
```

jira-phone 主单再调一次 `getJiraIssue` 数 `issuelinks` 中的 Duplicate（与 DB 的 total 对比，不一致标"DB 同步延迟"）。记录：主单状态、指派人、被 dup 单的状态分布。
判断 dup 原因（只看单子内容）：
- 被 dup 单 summary / backtrace 与主单一致 → 相似度归并本该直接追加到主单却新开了单，是**提单去重问题**；
- 主单 Resolved/Closed 后仍持续被 dup 或 reopen → 修复未生效或未合入该分支；
- 被 dup 单 backtrace 明显不同 → **误 dup**，提示人工拆分。

### 6. 根因补查（matrix 线上日志）

只对以下条目做：`locate_status='pending'`、`locate_confidence='低'`、`locate_category='无法判断'`，以及第 3 步复核后仍未提单的 C1/C4/C7。每类最多 5 个样本。

1. `mcp__vela-mcp__vela_search_matrix_log(keyword=<resource_id 或 ct_task_id>, date=<first_seen 当天>, type='console')`，最近 3 个自然日各查一次（含当天）。
2. 命中后用 `vela_read_matrix_log(node, date, type='console', keyword=<id>)` 读上下文，再用 `ERROR` / `Traceback` / `Exception` + 时间点附近排查建单失败原因（JIRA 接口报错、锁、MQ、超时）。调度相关先看带 `-schedule-` 的节点。
3. **假阴性**：`vela_read_matrix_log` 对部分大文件只回最后 1 行；0 命中不代表无日志，报告中标"日志检索未命中（不可信）"，不要据此下结论。

### 7. 报告

直接输出在对话里（如用户要求落盘，写 `reports/jira_audit/<YYYY-MM-DD_HHMM>.md`，`reports/` 已 gitignore）。分级：

| 级别 | 内容 |
|---|---|
| P0 | 对账链路异常（G1/G2）；复核后确认漏提的 crash（C1/C4/C7 且非"业务规则跳过"）；dup 堆积（total > 3） |
| P1 | testcase 漏提（A3/A4/A5）；流程卡住（A2/A6/C2/C3/C6） |
| P2 | 疑似重复开单；审计系统自检疑点；`业务规则跳过` 类计数 |

```markdown
# 提单审计报告（<窗口起> ~ <窗口止>）

## 结论
- 3~5 句：漏提多少、主要根因、dup 堆积哪几个主单、最该先处理什么。

## 审计系统自检
- 最近一次对账：... ；open 总数 ...；24h 未再扫到 ...；定位积压 ...

## P0
### crash 漏提
| 场景 | resource | product / branch | 复核结果 | 定位结论(置信度) | 日志根因 |
### dup 堆积
| 主单 | 实例 | 状态 / 指派人 | 累计 dup(DB/Jira) | 24h 新增 | 原因判断 |

## P1
### testcase 漏提（按 场景 × 定位类别 × product × branch 聚合）
| 场景 | 定位类别 | product / branch | 条数 | 样本 ct_task_id |
### 流程卡住
...

## P2
### 疑似重复开单
| crash_id | branch | 单号 | 同项目/跨项目 | 判断 |
### 业务规则跳过计数

## 附录：明细
```

每条都附链接（crash 报告 / Jira）。完成后向用户给 3~5 句结论，并说明哪些结论是现查确认的、哪些只来自审计记录或日志未命中。

## 注意

- 漏提单的判定以 `ticket_audit` 为准；发现它误报 / 漏报时，在报告"审计系统自检"里写明样本和推断，**不要**在本 skill 里另起口径，也不要改代码（需要改 checker 时另起需求）。
- dup 检查目前只在本 skill 里做，ticket_audit 尚无对应 checker。
- MCP 查询超时：先加 id 下界或缩小时间段分段查，不要反复重试同一条 SQL。
