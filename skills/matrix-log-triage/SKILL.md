---
name: matrix-log-triage
description: 巡检 vela-tool-service（mios-system-service）线上 matrix console 日志，汇总最近 24 小时的 ERROR，按 P0~P3 分级，并给 P0/P1 出带源码定位的修复方案。触发词：查 matrix 日志、线上日志巡检、线上 ERROR 汇总、最近 24 小时报错、日志分级、/matrix-log-triage。
---

# matrix 线上日志 ERROR 巡检

可以重复执行。默认时间窗口是从运行时刻往前滚动 24 小时。用户也可以自己指定，例如 `48 小时`、`2026-10-15 00:00 ~ 2026-10-16 00:00`。
本 skill 只负责诊断和给出修复方案：**不改代码、不执行 DDL、不提交**。是否修复由用户看完报告后决定。

## 背景事实（无需再查）

- 日志格式（loguru）：`YYYY-MM-DD HH:MM:SS,mmm [线程] LEVEL [tid=.. rid=..|-] 文件:函数:行号 - 消息`。不以时间戳开头的行是上一条记录的续行（Traceback 或 SQL）。
- `vela_read_matrix_log` / `vela_search_matrix_log` 的 keyword 是**子串匹配**。INFO 行里出现的 `check_log=ERROR: ...` 也会被匹配到，所以统计必须看级别字段。搜索时用 `] ERROR [` 做关键词。
- `vela_search_matrix_log` 会把命中的**完整日志**上传到 FDS 桶 `vela-crash-log`，并返回链接，格式是 `https://cnbj1-fds.api.xiaomi.net/vela-crash-log/matrix-log/<date>/<pod>/console_<date>.log`。下载不需要鉴权。
- 节点名里带 `-schedule-` 的是调度节点，跑定时任务；其余是 Web 节点。pod 会重建，所以每天的节点列表都不一样。
- `ticket_event.py:_emit` 的 ERROR 是 crash 分发事件。它和同一 tid 的业务 ERROR 是成对出现的。日志里的 `env.commit` 是线上部署的 commit，各节点可能不同，**不一定在 dev 分支上**。
- 线上数据库用 `database='mios_db'`。vela-mcp 只读，也没有 PROCESS 权限，所以查不了 `innodb_trx`。

## 工作流

工作目录：`/tmp/matrix-log-triage/<YYYYMMDD_HHMM>/`。报告：`/tmp/matrix-log-report-<YYYYMMDD>.md`。

### 1. 取日志（窗口跨几天就查几天）

对窗口覆盖的每个日期，调用一次：

```
vela_search_matrix_log(keyword="] ERROR [", date=<YYYY-MM-DD>, type="console", max_matches=1)
```

把返回结果里所有 `FDS 日志下载链接` 原样写入 `<工作目录>/links.txt`，然后执行：

```bash
bash ~/.claude/skills/matrix-log-triage/scripts/fetch_logs.sh <工作目录>/links.txt <工作目录>/logs
```

**回退**：如果某个链接下载失败（`failed.txt` 非空）或者上传报 `upload_error`，就对这个节点改用 `vela_read_matrix_log(node, date, type="console", keyword="] ERROR [", limit=5000, offset=...)` 分页读取，把结果写到 `logs/<date>/<pod>.log` 再聚合。分页读出来的只有 ERROR 首行，没有 Traceback，报告里要注明。如果 `tail` 只返回 1 行（已知的假阴性），不能据此认为没有错误。

### 2. 聚合

```bash
python3 ~/.claude/skills/matrix-log-triage/scripts/aggregate.py --hours 24 \
    --out-dir <工作目录>/out <工作目录>/logs/*/*.log
# 指定窗口: --since "YYYY-MM-DD HH:MM" --until "YYYY-MM-DD HH:MM"
```

产物：
- `aggregate.md`：签名总表，含建议级别、次数、节点、首末次时间、已知问题匹配，附录列出超阈值的 WARNING。
- `aggregate.json`：完整数据，每个签名带 `hourly` 分布，以及 2 条样例（原文、tid、rid、Traceback 末尾）。

### 3. 定级（agent 判断）

脚本给出的级别只是建议，要按 `references/priority-rules.md` 的人工调级要点修正：

- 位置不同但根因相同的签名合并成一条，例如 `_fetch_dag_page` 和 `_sync_one_config`、`process_tar_file` 和 `db_transaction`。
- 看 `hourly`：每个小时都有，说明是持续故障；只集中在一段时间，说明是事件型。再对比 `last_seen` 和当前时间，判断问题是否已经停止。
- 对照 `references/known-issues.md`：
  - 标为 `fixed` 但 `last_seen > fixed_at` 的，说明复发了。
  - 标为 `fixed_pending_deploy` 的，用 `git merge-base --is-ancestor <fix> <env.commit>` 确认修复有没有上线。

### 4. P0/P1 根因与修复方案

对每个 P0/P1 问题：

1. 用 graphify 定位：`get_node(<函数名>)` 拿到 `src` 和 `loc`。如果不在 vela-tool-service 工作区，用任意一个 clone 执行 `git fetch origin dev`，再用 `git show origin/dev:<path>` 读源码。
2. 结合样例的 Traceback 和 SQL 确认根因。涉及 schema 的问题，用 `vela_sql_describe` 或 `show tables like` 对照线上实际结构，再到 `migrations/sql/` 里查有没有对应的迁移。
3. 必要时用 `vela_sql_query` 核实数据状态，例如配置是否启用、某行是否存在。
4. 输出：根因、`文件:行号`、改法（给出最小 diff 思路）、验证方式（用什么日志关键词或 SQL 确认问题消失）。需要 DDL 的写明“需 DBA 执行”。

### 5. 报告

写入 `/tmp/matrix-log-report-<YYYYMMDD>.md`，对话里也输出同样的内容：

```markdown
# 线上日志 ERROR 巡检 <since> ~ <until>
概览：扫描节点 N（web x / schedule y），ERROR 记录 M 条，合并后问题 K 个；部署 commit: ...
## P0
| # | 问题 | 次数/节点 | 时间分布 | 状态 |   ← 状态：新 / 已知未修复(Kxxx) / 已修复待部署 / 已止血
### P0-1 <标题>
根因 / 位置 / 修复方案 / 验证
## P1  (同上)
## P2  (表格即可，一行一个：问题、次数、建议)
## P3  (表格即可)
## 附录：WARNING 超阈值、取数缺口（回退节点、下载失败）
```

### 6. 更新已知问题库

把本轮确认了根因的新问题追加到 `references/known-issues.md`：`pattern` 用能稳定命中的位置或文本正则，`status=open`。已修复的条目改为 `fixed` 并填写 `fixed_at`。

## 禁止

- 不要根据 `vela_read_matrix_log` 返回 0 命中就断言“无 ERROR”，先确认文件大小和扫描是否完整。
- 不要执行 ALTER、INSERT 等写操作，不要改业务代码。修复要等用户确认后，另走开发和提交流程。
- 报告里不要贴 FDS 签名参数、Token 等敏感串。图片链接里的 `Signature=***` 保持脱敏后的样子。
