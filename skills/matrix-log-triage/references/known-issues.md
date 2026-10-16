# 已知问题库

`aggregate.py` 用 `pattern`（Python 正则）去匹配签名的位置、消息、根因和样例原文，命中的错误会在报告里标出对应的已知问题。每次巡检确认了新的根因，就在这里追加一条。修复生效后，把 `status` 改成 `fixed`，并补上修复方式（commit 或 DDL）和生效时间。

`status` 取值：

- `open`：已知，还没修。
- `fixed_pending_deploy`：修复已合入 dev，但线上还没部署。日志里 `env.commit` 是线上部署的 commit，用 `git merge-base --is-ancestor <fix_commit> <deploy_commit>` 判断修复是否已包含在内。
- `fixed`：已修复且已生效。如果窗口内的 `last_seen` 晚于 `fixed_at`，说明问题复发，要改回 `open`。

```json
[
  {
    "id": "K001",
    "title": "vela_crash_resource_description.jira_source 列缺失 (1054)",
    "pattern": "jira_source",
    "status": "fixed",
    "fix": "线上已补列 jira_source VARCHAR(16)（2026-10-16 用 vela_sql_describe 核实）；migrations/ 下无对应 SQL，建议补迁移脚本",
    "fixed_at": "2026-10-16 11:00",
    "note": "补列前 process_tar_file 查描述表全部报 1054，crash 资源分发失败"
  },
  {
    "id": "K002",
    "title": "NuttX Sync 自愈失败：上游已跟踪 .claude/skills，但 git clean 排除了 .claude",
    "pattern": "nuttx_sync_service.py:pull_single_repo",
    "status": "open",
    "fix": "application/crashlog/nuttx_sync_service.py recover_cmd 中 `git clean -fd -e .claude` 去掉 -e .claude（或改为只排除本地私有子目录）；feb946fb 引入",
    "fixed_at": null,
    "note": "dev-system / rebase_dev-system_trunk-26rc01 两个 nuttx 仓库每轮同步都失败，报错 '未跟踪的文件将会因为检出操作而被覆盖: .claude/skills/nuttx-review/...'"
  },
  {
    "id": "K003",
    "title": "feedback_daily_summary_log 表不存在 (1146)",
    "pattern": "feedback_daily_summary_log",
    "status": "open",
    "fix": "线上执行 migrations/sql/2026-05-14-feedback-daily-summary-up.sql（需 DBA）",
    "fixed_at": null,
    "note": "反馈日报每天调度一次即失败，日报从未发出"
  },
  {
    "id": "K004",
    "title": "autotest 同步: 已下线 task_key 的 sync_config 仍 enabled，dag/list 持续 500",
    "pattern": "(?s)autotest_sync_service\\.py:(_fetch_dag_page|_sync_unfinished_records|_sync_one_config|reconcile_stale_tasks).*(dag/list|Internal Server Error)",
    "status": "fixed",
    "fix": "2026-10-16 17:24~17:49 启用配置由 259 降到 203（失效配置已清理）；代码侧 refresh_sync_configs 未自动停用失效 task_key，可能复发",
    "fixed_at": "2026-10-16 17:49",
    "note": "约 56 个 task_key 每轮 state=ready 补扫都 500，每次重试 3 次 × 3 条 ERROR"
  },
  {
    "id": "K005",
    "title": "1205 Lock wait timeout：test_issues / test_case_failures / vela_autotest_execution_record 插入超时",
    "pattern": "1205, 'Lock wait timeout",
    "status": "open",
    "fix": "根因待定位（需 DBA 抓 innodb_trx / data_lock_waits，vela-mcp 无 PROCESS 权限）",
    "fixed_at": null,
    "note": "集中在 schedule 节点，06~09 点高峰"
  }
]
```
