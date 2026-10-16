---
name: crash-task-failure-report
description: 分析 crashlog 解析 agent（application/crashlog/parse_client）近 24 小时失败的解析任务（vela_crash_analyze_task 中 task_status=error/timeout），登录对应解析机读取 console 日志，按规则归类根因，输出带报告链接的 Markdown 根因报告。触发：crash 解析失败日报、解析任务失败分析、analyze_task error 统计、coredump_task 失败根因、parse_client 失败报告、/crash-task-failure-report。
---

# crash 解析任务失败根因报告

可重复执行。默认窗口为运行时刻往前 24 小时（滚动），用户可指定其他窗口（如 `48 hour`、`2026-10-15 00:00 ~ 2026-10-16 00:00`）。

## 背景事实（无需再查）

- agent 部署：`application/crashlog/deploy/deploy_crashlog_agent.sh`，容器 env `NODE_NAME=<主机>_<序号>`，序号不补零。
- 登录：`node_name` 下划线前是主机。`mina*` → `ssh -p 24 jenkins@minaXX`；`vela*` → `ssh jenkins@velaXX`。
- 日志：宿主机 `/home/mnt/crash_logs/<node_name>/console_YYYY-MM-DD.log`，跨天后变成 `.log.zip`，保留 30 天。工作目录在每轮被清空，**失败现场只剩 console 日志**。
- 每个任务在日志里是 `[task=<resource_id> log=<log_file_id> core=.. name=.. idx=..] task begin` 到 `... task end` 的连续区间；中间 `[subproc]` / `cmd execute error` 等子进程输出不带 `log=` 前缀，必须按区间截取，不能 grep。
- 失败任务的 `task_result` 为空，根因只能从日志获取。
- 关联：`analyze_task.log_file_id → vela_crash_log_file.id → .resource_id → vela_crash_resource.id`。
- 报告链接：`http://vela.pt.xiaomi.srv/vela_crash_report?id=<resource_id>`。

## 工作流

`<SKILL_DIR>` = 本 SKILL.md 所在目录。工作目录：`reports/crash_task_failures/<YYYY-MM-DD_HHMM>/`（相对当前工作区；在 vela-tool-service 中 `reports/` 已 gitignore，其他仓库注意别提交）。报告：`reports/crash_task_failures/<YYYY-MM-DD_HHMM>.md`。

代码位置分析需要 vela-tool-service 仓库（`application/crashlog/parse_client`）；当前工作区不是该仓库时，先问用户本地 clone 路径。

### 1. 查失败任务（vela-mcp, `database='mios_db'`）

```sql
select t.node_name, t.log_file_id, t.task_name, t.task_status, t.create_date, t.update_date,
       f.resource_id, f.core_name, f.platform, f.gdb_tools, r.product, r.branch
from vela_crash_analyze_task t
join vela_crash_log_file f on f.id = t.log_file_id
join vela_crash_resource r on r.id = f.resource_id
where t.task_type = 'parse' and t.task_status in ('error', 'timeout')
  and t.update_date >= now() - interval 24 hour
order by t.update_date desc
```

`limit` 传 1000。若被截断，按 `update_date` 分段再查。把 MCP 返回的表格文本**原样**写入 `<工作目录>/tasks.txt`（脚本可直接解析表格，也接受 JSON 数组）。

### 2. 查同窗口总量（算失败率）

```sql
select task_name, task_status, count(*) c from vela_crash_analyze_task
where task_type = 'parse' and update_date >= now() - interval 24 hour
group by task_name, task_status
```

### 3. 拉日志并归类

```bash
python3 <SKILL_DIR>/scripts/fetch_task_logs.py \
  --tasks reports/crash_task_failures/<ts>/tasks.txt \
  --out   reports/crash_task_failures/<ts>/result.json
```

- 按主机并行 ssh，单主机一次连接；在远端用 python3 截取任务块（支持 `.zip`），不拷整份日志。
- 输出：`result.json`（每任务的 `category` / `matched_rules` / `key_lines` / `block_file`，以及 `summary` 聚合与 `unreachable_hosts`），`blocks/<log_file_id>.log` 为完整日志块，`appendix.md` 为报告附录明细表（直接拼到报告末尾）。
- 规则在 `rules.json`，按顺序匹配，第一条命中为主类别。连不上或找不到块的记为 `log_unavailable`（`block_status` 说明原因：`ssh_failed` / `block_not_found` / `no_node_name`）。

### 4. 根因分析（agent 完成）

1. 每个类别至少读 1~2 个 `blocks/*.log` 全文，确认规则归类正确，找出**真正**的根因（症状类如 `coredump_empty` 要往前看 `[subproc]` 和 `returncode`）。
2. 对 `uncategorized` 逐个读日志归类；能稳定识别的，在报告里给出新增规则建议（category + 正则），经用户同意后再改 `rules.json`。
3. 交叉维度找集中点：看 `summary.by_product / by_branch / by_node`。怀疑某维度时用 SQL 对比同窗口成功任务（如 `binary r.product` 区分大小写、按 core_name、gdb_tools、commit_id）。
4. 每个类别定位到 `application/crashlog/parse_client` 中的代码位置（先用 graphify MCP 定位，再读源码确认），给修复建议。**不自动改代码**。
5. 分析过程中发现的与根因无关的 bug 记入"附带发现"。

### 5. 写报告

```markdown
# crash 解析任务失败根因报告（<窗口起> ~ <窗口止>）

## 总览
- 失败任务 N 条 / 涉及 resource M 个；不可达主机：...
| task_name | 失败(error/timeout) | 总数 | 失败率 |
- product / branch 分布（Top）

## 根因分类
### 1. <category> — <title>（任务 x / resource y）
- 根因：...（引用关键日志行）
- 代码位置：parse_client/xxx.py:Lnn
- 修复建议：...
- 关键样本（按 resource 去重，最多 3 个）：
  - [resource_id](http://vela.pt.xiaomi.srv/vela_crash_report?id=<resource_id>) product / branch / core / node / log_file_id

## 规则建议（可选）
## 附带发现
## 附录：失败任务明细
| update_date | node | task_name | status | product | branch | core | category | 报告 |
```

完成后向用户输出：报告路径 + 3~5 句结论（主要根因、占比、建议优先修哪条）。

## 注意

- vela-mcp 只读；本 skill 只读线上库、只读解析机日志，不改机器上任何东西。
- 某主机 ssh 失败只影响该主机任务，报告头部列出；不要因此中断。
- 窗口跨零点时脚本会同时读 `create_date`/`update_date` 所在两天的文件。
