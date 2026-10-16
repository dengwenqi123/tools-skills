# 优先级规则

`aggregate.py` 只给出“建议级别”。最终级别由 agent 看过样例和源码后定。调整阈值时只改下面的 json 块。

| 级别 | 定义 | 典型例子 |
|---|---|---|
| P0 | 核心链路整体不可用，或数据丢失、写入失败，或服务/调度器起不来 | 1054/1048/1406/1146 等 schema 不一致（**硬规则：直接定 P0**）、crash 资源分发全部失败、DB 连接池耗尽 |
| P1 | 单个功能持续失败，或高频、多节点出现，但不影响全局 | 某接口持续抛 TypeError、某定时任务每一轮都失败、非瞬时错误 24h 内 ≥ `p1_count` 次 |
| P2 | 外部依赖偶发失败，能自愈 | Jira/FDS/Gerrit/SSH 超时、5xx、带重试的网络错误 |
| P3 | 噪声 | 日志级别用错、预期内的业务校验失败被打成 ERROR、低频单次出现 |

人工调级要点：

- 次数少，但每次都代表一个用户请求失败（如 `views.py:wrapper` 返回 500、缺参数引发的 TypeError）→ 至少定 P1。
- 已知问题且 `last_seen` 早于修复生效时间 → 已经止血，在报告里放到“已修复”一栏，不写 P0/P1 修复方案。
- 瞬时错误如果对同一个固定目标（同一节点、同一仓库）一直 100% 失败 → 不算瞬时，升为 P1。
- 同一个根因分散在多个签名里时，合并成一条展示，级别取其中最高的。

```json
{
  "p1_count": 100,
  "p1_nodes": 3,
  "p1_nodes_min_count": 20,
  "p1_transient_count": 1000,
  "p2_count": 10,
  "warning_appendix_count": 500,
  "p0_keywords": ["Too many connections", "QueuePool limit", "Lost connection to MySQL", "Scheduler has been shut down", "MemoryError"],
  "p1_keywords": ["missing 1 required positional argument", "missing 2 required positional arguments", "服务器内部错误", "AttributeError", "KeyError", "TypeError"]
}
```
