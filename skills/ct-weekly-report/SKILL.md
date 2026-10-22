---
name: ct-weekly-report
description: 生成 CT（准入测试）周报：最近 7 天各产品 / 分支的排队预警次数与排队时长、CT 失败率（含剔除停线后的对照）、CT 失败预警、Daily 被终止、停线识别、平台同步与回调缺失，产出单文件 HTML（docs/reports/ct-weekly-<日期>.html），顶部是给老板和周会看的要点。用户说「CT 周报」「生成本周 CT 周报」「跑 CT 周报」「本周排队 / 失败预警统计」「自动化测试框架开发小组群告警汇总」「CT 告警周统计」时使用；即使只问「这周 CT 排队 / 失败情况怎么样」也应该用这个 skill 出数，不要手写 SQL 临时拼。
---

# CT 周报

一次执行 = 取数 → 回捞日志 → 写建议 → 渲染 → 汇报。全程只读：只查 mios_db 和线上日志，不写库、不发飞书。
术语见仓库根 `CONTEXT.md`「预警次数」「预警大类」「排队时长」「问题类型」等条目。

## 统计口径（不要改，改了跨周就没法比）

- 窗口：执行时往前推 7 天（滚动），`[start, end)`。上期 = 紧挨着本期之前、同样长度的一段，不重叠。
- CT = 执行记录 `job_type='admission'`，按 `create_time` 归属窗口，按执行次数计。
- 排队时长 = `start_time − create_time`，只算已开始执行的任务；报平均值和中位数（中位数更抗长尾，上会时用「一半任务超过 X 小时」的说法）。
- 从未执行 = 没有 `start_time` 且状态为 timeout / killed（排队 6 小时无进展被收口），不进排队时长。
- CT 失败率 = 失败 / （成功 + 失败）；超时、被取消不算失败也不进分母。另给「剔除本期停线任务线后」的对照，两期都剔同一批线，避免停线造成失败率的假下降。
- 停线 = 窗口内 ≥ 50 个任务、但窗口最后 24 小时没有新任务的 CT 任务线。
- 排队预警 = `vela_autotest_alarm_record.alarm_type='queue_overflow'`，按产品 + 分支（任务线）计，一条记录算一次。
- CT 失败预警 = ct_failure + patch_failure + dashboard_task_fail_rate。
- Daily 被终止 = `job_type='daily'` 且 `state='killed'`，限 dev / trunk-26rc01，按 `end_time` 归属，按任务数计。
- 失败原因来自日志自动分析的 `reason`，是规则推断，不是根因；报告里叫「未定位（疑似系统）」而不是「系统问题」。

**只写实测数字。** 不要在报告或建议里放推算值（例如「缺 N 台设备」），用户明确拒绝过；需要表达资源不足时，用预警次数和排队时长说话。提到产品时一律带分支。

## 第 0 步：环境

- 在 vela-tool-service 仓库根执行，用 `.venv/bin/python`（需要 httpx）。
- `~/.kiro/settings/mcp.json` 里有 vela-mcp 的 `VELA_MCP_USERNAME / VELA_MCP_TOKEN`，脚本用它调同一个只读 SQL 接口。
- 回调缺失日报要用 vela-mcp 的 `vela_search_matrix_log` 工具（脚本调不到日志）。

## 第 1 步：取数

```bash
S=.kiro/skills/ct-weekly-report/scripts; PY=.venv/bin/python
$PY $S/collect.py                                   # 默认：现在往前推 7 天
# 指定窗口：$PY $S/collect.py --start '2026-10-14 16:14:00' --end '2026-10-21 16:15:00'
```

输出 `/tmp/ct-weekly/<结束日期>/data.json`（`CTW_WORK` 可覆盖），并打印 CT 总数、排队 > 1h、失败、排队预警、失败预警、停线条数。后续命令需要 `export CTW_WORK=<这个目录>`。

## 第 2 步：回捞回调缺失日报

对窗口内每一天调 `vela_search_matrix_log(keyword='已发送回调缺失日报', date=YYYY-MM-DD, type='console')`，可并行。命中行形如 `已发送回调缺失日报: 591 条`，只取时间在窗口内的。结果写进 `insights.json` 的 `callback_missing`，格式 `[["10-14", 591], ...]`；整段没查（工具不可用）就写 `null`，报告会标「数据缺失」。日志可能有漏，报告里按下限说明。

## 第 3 步：写建议（insights.json）

```bash
$PY $S/render.py --issues-only     # 打印脚本自动识别的问题：queue / stopped / fail / killed / unclaimed
```

对每个问题写一句建议，存 `$CTW_WORK/insights.json`：

```json
{"advice": {"queue": "...", "stopped": "确认是主动关闭还是故障；恢复前先补设备", "fail": "...", "killed": "...", "unclaimed": "..."},
 "callback_missing": [["10-14", 591], ["10-17", 281]]}
```

写法：一句话、可执行、指明谁来做；引用数字必须来自 issues 输出或 data.json。没有把握的建议就写「待负责人确认」，不要编原因。停线原因脚本查不到，要在汇报时向用户提问。

## 第 4 步：渲染

```bash
$PY $S/render.py                   # → docs/reports/ct-weekly-<结束日期>.html
```

渲染前会自检（失败数 = 每日失败之和 = 各原因之和；预警矩阵 = 每日合计），不通过直接报错，先查数据别绕过。HTML 是纯静态 + 内联 SVG，可直接发给别人或贴到飞书。

页面结构：本周要点（排队预警按产品 + 分支带排队时长 / CT 失败率 / CT 失败预警 → 4 个指标卡 → 问题与建议）→ 排队 → CT 失败 → 预警矩阵 → 平台问题 → 数据说明。

## 第 5 步：汇报

对话里只回：文件路径 + 顶部三条要点的数字 + 需要用户确认的事（例如停线原因）。不要贴整份报告。

## 已知坑

- SQL 接口单次最多 1000 行，`collect.py` 截断时直接退出；新增查询只做聚合，别拉明细。
- 线上 MySQL 是 5.7：没有窗口函数和 `JSON_TABLE`，中位数靠 `ORDER BY ... LIMIT 1 OFFSET n`。
- 别用 `lines` 之类的保留字做列别名，接口会回 400。
- `reason` 为 NULL 时 `reason IN (...)` 是 NULL，整组 SUM 会变 NULL，脚本已统一补 0。
- 执行记录同步有延迟：同一窗口隔几小时重跑，窗口末尾的数会多出几个（例：10-14~10-21 窗口当天 354 次失败，隔天重跑 359 次）。对外的数以报告生成时为准，报告头写了生成时间。
- `.venv` 是 Python 3.11，f-string 表达式里不能有反斜杠。

## 回归检查

改脚本后用固定窗口跑一遍，核对：排队预警 69、CT 失败预警 8、停线 4 条（p65 / p67 的 dev 与 trunk-26rc01）、从未执行 1114、排队 > 1h 3842。失败数会因同步延迟略涨（≥ 354）。

```bash
CTW_WORK=/tmp/ct-weekly/regress $PY $S/collect.py --start '2026-10-14 16:14:00' --end '2026-10-21 16:15:00'
```
