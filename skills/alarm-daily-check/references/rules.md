# 分级口径与阈值

调阈值只改本文件。时间都相对运行时刻。

## P0：链路停摆
- 所有启用任务线的 `MAX(last_sync_time)` 超过 60 分钟（告警检测 job 没在跑）
- 一轮同步中所有任务线都失败：各 job_type 的 `failing = n`
- `card_ws` 线程未启用或连接报错（`[CardWS]` 日志）

## P1：真漏发 / 数据丢失
- `vela_autotest_alarm_record` 24h 内有 `notify_status` 不属于 sent / success / claimed 的记录
- `suppress` 中有 `send_failed` 或 `check_failed`
- CT 任务线 `consecutive_failures >= alert_threshold` 且 `alert_enabled=1`，未被人工标记处理，24h 内却没有 `sync_failure` 记录
- 过了 20 点，`callback_missing` 非空但 `callback_missing_daily_sent=false`
- 所有 CT 任务线都"有执行记录但 0 回调"
- 日志出现 `超过 24 小时, 丢弃`，或 `pending_callbacks.oldest_age_seconds > 20h`

## P2：局部异常
- 单条任务线 `last_success_time` 超过 60 分钟
- 单条 CT 任务线"有执行记录但 0 回调"
- ready/running 超过 6h，且 `start_time` 非空（收口不会处理）
- 同一 `{alarm_type}:{rule_id}` 的 `claim_blocked` 计数 ≥ 10，且对应记录认领超过 24h 仍未解决
- `[同步服务] 统计` 轮数少于 24h 理论轮数（约 57 轮）的 80%
- `[通知审计] 记录未发出计数失败` 日志出现（计数不可信）

## P3：信息
- 抑制原因分布、各链路吞吐
- 认领或解决后未填 `fix_plan`
- 文档与 `config/prod.py` 不一致
