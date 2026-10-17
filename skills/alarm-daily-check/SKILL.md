---
name: alarm-daily-check
description: 每日巡检 vela-tool-service 告警链路（application/alarm）：CT 数据同步与珊瑚海回调、规则告警、CT 单任务失败率预警、同步失败预警与回调缺失日报、僵尸任务收口。检查任务与告警状态、每个检查点是否收到信息、最近 24 小时同步异常、"达到条件但未通知"的漏发与抑制分布，输出 P0~P3 分级 Markdown 报告。触发：告警巡检、跑告警巡检、alarm 巡检、CT 同步巡检、告警漏发、通知漏发、同步异常排查、/alarm-daily-check。
---

# 告警链路每日巡检

只读：用 vela-mcp 查 `mios_db`、读 matrix 日志，调用一个只读 HTTP 接口取 Redis 状态。不改数据、不改代码、不发飞书。
默认窗口：运行时刻往前 24 小时（滚动）。用户指定窗口时，替换下文所有 `INTERVAL 24 HOUR`；Redis 状态按自然日取，窗口跨两天时两天都取。
分级阈值见 `references/rules.md`，调阈值只改那份文件。

## 背景事实（无需再查）

- 调度（`config/prod.py` JOBS，调度节点 `mios-system-service-schedule-*` 上运行）：
  - `check_autotest_alarms` 每 25 分钟：Step1 先跑 CT 同步 `sync_execution_records`，再检测 queue_overflow / patch_failure / execution_timeout / ct_failure / task killed，最后合并发飞书。同步不单独调度。
  - `check_ct_task_fail_rate` 每 15 分钟，只在 9–21 点发送。
  - `reconcile_stale_autotest_tasks` 每 30 分钟：`start_time` 为空且派发超 6h 的 ready/running 置 timeout。
  - `feishu_card_ws_loop` 常驻线程，接收卡片"认领 / 解决"回调。
- 数据流：珊瑚海回调 `POST /velatask/test_run_callback` → `apply_callback`（只写终态；写失败暂存 Redis `autotest_sync:pending_callbacks`，下轮重放，超 24h 丢弃）。拉取同步补扫 ready/running，结束超 25 分钟仍无回调的计为"回调缺失"，暂存 `sync_alert:missing:{YYYYMMDD}`，20 点后第一轮发日报。
- 表：
  - `vela_autotest_sync_config`：每条任务线的同步状态（`sync_status / last_sync_time / last_success_time / consecutive_failures / last_error / alert_threshold`）。只有最新状态，没有历史。
  - `vela_autotest_execution_record`：执行记录（id = 珊瑚海 dagId）。
  - `vela_autotest_alarm_record`：告警记录。`notify_status` 取值：规则类 `pending → sent / failed`，被认领后 `claimed`；`sync_failure` 和 `dashboard_task_fail_rate` 只在发送成功后写入，值为 `success`。sent / success / claimed 都算已发出。
  - `vela_autotest_alarm_rule`：status='active' 的规则参与检测。
- 未发出计数（Redis hash `alarm:suppress:{YYYYMMDD}`，field `{alarm_type}:{rule_id}:{reason}`）：
  - 设计内抑制：`out_of_window` 不在通知时段、`interval` 通知间隔未到、`claim_blocked` 1 小时内已被认领、`resolved_recently` 刚解决过。
  - 真漏发：`send_failed` 发送失败、`check_failed` 失败率检测异常（`dashboard_task_fail_rate`）。
  - `alarm_type=sync_missing_daily` 指回调缺失日报。
  - 这个计数从 commit "alarm: count suppressed and failed notifications" 上线后才有数据。接口返回空不代表没有抑制，先确认该 commit 已部署。
- matrix 日志前缀：`[调度器]`、`[同步服务]`、`[回调同步]`、`[同步预警]`、`[通知服务]`、`[告警服务]`、`[单任务失败率预警]`、`[收口]`、`[CardWS]`、`[通知审计]`。

## 工作流

SQL 一律用 `vela_sql_query(database='mios_db', limit=1000)`。互不依赖的步骤并行发起。

### 1. Redis 状态（只读接口）

```bash
python3 <skill_dir>/scripts/redis_state.py            # 今天
python3 <skill_dir>/scripts/redis_state.py 2026-10-16 # 指定日期；窗口跨天时两天都取
```

返回 `suppress`（未发出计数）、`callback_missing`（当天回调缺失暂存）、`callback_missing_daily_sent`、`pending_callbacks.length / oldest_age_seconds`。
接口报 401：`~/.kiro/settings/mcp.json` 里 vela-mcp 的 `VELA_MCP_USERNAME / VELA_MCP_TOKEN` 失效。报 404：接口所在 commit 还没部署。两种情况都在报告"数据源"一节注明，相关检查点标"无法确认"，不要当成正常。

### 2. 同步状态（检查点 2）

```sql
SELECT job_type, COUNT(*) n,
       MAX(last_sync_time) last_sync, MIN(last_success_time) oldest_success,
       SUM(consecutive_failures > 0) failing,
       SUM(consecutive_failures >= alert_threshold) over_threshold,
       SUM(last_success_time < NOW() - INTERVAL 60 MINUTE) stale60
FROM vela_autotest_sync_config WHERE enabled = 1 GROUP BY job_type;

-- 有异常的任务线明细
SELECT task_key, job_type, branch, sync_status, last_sync_time, last_success_time,
       consecutive_failures, alert_threshold, alert_enabled, LEFT(last_error, 300) err
FROM vela_autotest_sync_config
WHERE enabled = 1 AND (consecutive_failures > 0 OR last_success_time < NOW() - INTERVAL 60 MINUTE
                       OR sync_status = 'syncing' AND last_sync_time < NOW() - INTERVAL 30 MINUTE)
ORDER BY consecutive_failures DESC;
```

- 全部任务线的 `MAX(last_sync_time)` 超过 60 分钟：告警检测 job 没在跑（检查点 3，P0）。
- `sync_status='syncing'` 且 `last_sync_time` 超过 30 分钟：同步线程卡死或进程被杀。
- 同步异常没有历史表，最近 24h 的异常以第 6 步日志为准，这里只看当前状态。

### 3. 执行记录与回调（检查点 1）

```sql
SELECT job_type, COUNT(*) total,
       SUM(state IN ('success','failed','killed','timeout','skipped','dispatch_failed')) terminal,
       SUM(state IN ('ready','running')) unfinished,
       SUM(state IN ('ready','running') AND dispatch_time < NOW() - INTERVAL 6 HOUR) stale6h,
       MAX(updated_at) last_update
FROM vela_autotest_execution_record
WHERE created_at >= NOW() - INTERVAL 24 HOUR GROUP BY job_type;

-- CT 按任务线：24h 内终态条数，用来和回调缺失比对
SELECT task_key, COUNT(*) terminal
FROM vela_autotest_execution_record
WHERE job_type = 'admission' AND created_at >= NOW() - INTERVAL 24 HOUR
  AND state IN ('success','failed','killed','timeout','skipped','dispatch_failed')
GROUP BY task_key;
```

- 用第 1 步的 `callback_missing` 按 task_key 计数。某条 CT 任务线 24h 内有终态记录，且全部出现在回调缺失里：判定为"有执行记录但 0 回调"。所有 CT 任务线都这样：回调整体中断（P1）。
- 超过 6h 的 ready/running（`stale6h`）：收口只处理 `start_time` 为空的记录；已开始但卡住的不会被收口，在报告中单独列出（P2），附 5 个样本 id。
- `pending_callbacks.oldest_age_seconds` 接近 86400：即将被丢弃（P1）。

### 4. 告警记录与通知状态（检查点 4）

```sql
SELECT alarm_type, notify_status, COUNT(*) c, MIN(created_at) first_at, MAX(created_at) last_at
FROM vela_autotest_alarm_record
WHERE created_at >= NOW() - INTERVAL 24 HOUR
GROUP BY alarm_type, notify_status ORDER BY alarm_type;

-- 真漏发：有记录但没发成功
SELECT id, rule_id, alarm_type, task_key, branch, notify_status, notify_time, created_at
FROM vela_autotest_alarm_record
WHERE created_at >= NOW() - INTERVAL 24 HOUR
  AND notify_status NOT IN ('sent', 'success', 'claimed');

-- 认领后长期未解决 / 已解决未填修复方案
SELECT id, alarm_type, task_key, resolver, resolve_time, fix_plan IS NULL no_plan, created_at
FROM vela_autotest_alarm_record
WHERE created_at >= NOW() - INTERVAL 7 DAY
  AND (notify_status = 'claimed' AND resolve_time < NOW() - INTERVAL 24 HOUR AND fix_plan IS NULL)
ORDER BY id DESC LIMIT 50;

-- 规则清单（看规则是否被停用、时段配置）
SELECT id, rule_type, status, threshold, notify_interval, notify_start_hour, notify_end_hour, urgent_level
FROM vela_autotest_alarm_rule ORDER BY id;
```

### 5. 达到条件但未通知

不重算规则。证据来源只有三类：

1. 第 4 步中 `notify_status` 不属于 sent / success / claimed 的记录。
2. 第 1 步 `suppress` 中 reason 为 `send_failed` / `check_failed` 的计数。
3. 第 6 步的发送失败 ERROR 日志。

另外有两项需要交叉核对：

- CT 任务线 `consecutive_failures >= alert_threshold` 且 `alert_enabled=1`，但 24h 内没有对应 task_key 的 `sync_failure` 记录，`suppress` 里也没有 `sync_failure:0:send_failed`：疑似同步失败预警漏发。需排除"已人工标记处理"的情况：查该 task_key 最新一条 `sync_failure` 记录的 `resolver` 和 `resolve_time`。
- 当前时间已过 20 点、`callback_missing` 非空，但 `callback_missing_daily_sent=false`：回调缺失日报没有发出。

设计内抑制只给分布：按 `alarm_type × reason` 汇总计数。同一 `{alarm_type}:{rule_id}` 的 `claim_blocked` 计数很高，且对应记录认领超过 24h 仍未解决，说明认领屏蔽可能被滥用（P2）。

### 6. matrix 日志（同步异常 + 发送失败 + 线程存活）

按用户规则：最近 3 个自然日，先 `vela_list_matrix_log_nodes` 找出调度节点（名字带 `-schedule-`），再用 `vela_list_matrix_log_files` 确认 console 文件不是 0B，然后并行发起 `vela_read_matrix_log(type=console)`：

| 关键词 | 用途 |
|---|---|
| `[同步服务] 统计` | 每轮汇总。24h 应有约 57 轮（每 25 分钟一轮），明显偏少说明 job 漏跑 |
| `同步 task_key 失败` / `API 返回失败` / `API 请求异常` | 最近 24h 的同步异常 |
| `暂存回调失败` / `超过 24 小时, 丢弃` | 回调丢失 |
| `发送通知失败` / `发送同步失败卡片失败` / `发送回调缺失日报失败` / `[单任务失败率预警] 处理失败` | 发送失败 |
| `[CardWS]` | 卡片回调线程。出现"未启用"或连接报错即异常 |
| `[收口]` | 收口是否在跑，每次处理多少条 |
| `[通知审计] 记录未发出计数失败` | Redis 写入失败，此时第 1 步的计数不可信 |

假阴性：`vela_read_matrix_log` 对部分大文件只能读出最后 1 行。0 命中不代表没有日志，报告中标"日志检索未命中（不可信）"。需要确认时用仓库里的 `scripts/verify_matrix_log_jfs.py` 直接读卷内文件。

检查点 5（card_ws）：日志里没有异常，且 24h 内有通过卡片认领的记录（`resolver` 非空、`notify_status='claimed'`），判为正常；24h 内没有任何认领时标"无法确认"，不判异常。

### 7. 报告

写到 `/tmp/alarm-daily-check/<YYYY-MM-DD>.md`，结构如下：

```markdown
# 告警链路巡检（<窗口起> ~ <窗口止>）

## 结论
<3~5 句：链路是否通、有无真漏发、最需要处理的 1~2 件事>

## 数据源
<Redis 接口 / SQL / 日志各自是否可用；不可用的检查点列出>

## 链路检查点
| 检查点 | 最近时间 | 24h 量 | 状态 | 说明 |
（回调到达 / 拉取同步 / 告警检测 job / 记录与通知 / card_ws 共 5 行；状态 = 正常 / 异常 / 无法确认）

## P0
## P1
## P2
（每条：现象、证据（SQL 结果 / 日志行 / Redis 计数）、源码定位 `文件:行号`、修复建议；P0/P1 必须有定位与建议）

## P3 信息
### 抑制原因分布（alarm_type × reason）
### 各链路吞吐（执行记录 / 告警记录 / 发送卡片数）
### 已解决未填 fix_plan

## 附录：明细
```

完成后在对话里给 3~5 句结论，并说明哪些结论有 SQL 或接口证据，哪些只来自日志或"无法确认"。

## 注意

- 不发飞书、不改数据、不改代码。发现 checker 本身需要改时，在报告里提建议，另起需求。
- 告警逻辑以源码为准，阈值以 `references/rules.md` 为准。文档与代码不一致时，以 `config/prod.py` 和源码为准，并在报告里指出。
- 构建告警链路（`check_build_alarms`、`send_daily_build_summary`）不在本期范围，它的发送结果不落库。
