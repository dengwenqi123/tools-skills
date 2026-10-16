"""从 mios_db 只读导出周报所需表 (窗口按 bridge.created_at, 含两端), 并生成 me 机器拉原始数据用的 issue 清单.

用法: AIW_WORK=/tmp/aiweekly AIW_START=2026-10-07 AIW_END=2026-10-13 python export.py
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import json
from datetime import date, timedelta
from q import q

S, E = os.environ['AIW_START'], os.environ['AIW_END']
E1 = (date.fromisoformat(E) + timedelta(days=1)).isoformat()   # 右开
W = f"b.created_at >= '{S}' AND b.created_at < '{E1}'"
SQL = {
    'bridge': f"""SELECT b.id,b.module,b.jira_type,b.jira_key,b.jira_server,b.product,b.multica_issue_id,b.multica_issue_url,b.push_status,
        b.retry_count,b.last_error,b.analysis_state,b.has_feedback,b.feedback_rating,b.created_at,b.dedup_state,b.dedup_master_jira_key,
        b.analysis_retry_count,b.analysis_retry_history,b.domain,b.team,b.assignee,b.analyzed_at,b.jira_commented,b.triggered_at,
        b.finished_at,b.skip_reason,b.skip_analysis,b.title FROM multica_jira_bridge b WHERE {W}""",
    # 线上采集结果, 仅作 me 原始数据缺失时的兜底
    'summary': f"""SELECT s.jira_key,s.module,s.jira_type,s.product,s.issue_status,s.assigned_at,s.first_result_at,s.finished_at,
        s.t_first_result_s,s.t_agent_net_s,s.t_e2e_s,s.t_queue_s,s.stage_json,s.domain,s.domain_source,s.rounds,s.task_count,s.retry_count,
        s.tok_input,s.tok_output,s.err_platform,s.err_tool,s.err_data,s.err_flow,s.err_unclassified,s.err_label_json,s.ok_self,s.ok_flow,
        s.trigger_mode,s.route_confidence,s.generic_fallback,s.stuck_stage,s.analysis_outcome,s.final_outcome,s.experience_hit,
        s.skills_used,s.tool_calls,s.tool_err_json,s.finalized,s.collected_at
        FROM issue_trace_summary s JOIN multica_jira_bridge b ON b.jira_key=s.jira_key WHERE {W}""",
    'errors': f"""SELECT s.jira_key, LEFT(s.errors_json, 6000) errors_json FROM issue_trace_summary s
        JOIN multica_jira_bridge b ON b.jira_key=s.jira_key WHERE {W} AND (s.err_platform+s.err_tool+s.err_data+s.err_flow+s.err_unclassified)>0""",
    'feedback': f"""SELECT f.* FROM ai_feedback_records f WHERE f.issue_key IN (SELECT jira_key FROM multica_jira_bridge b WHERE {W})""",
    'fbweek': f"""SELECT f.issue_key, f.feedback, f.partial_score, f.reason_l1, f.reason_l2, LEFT(f.reason_detail,300) reason_detail, f.user_name,
        f.created_at, b.module bmodule, b.jira_type FROM ai_feedback_records f LEFT JOIN multica_jira_bridge b ON b.jira_key=f.issue_key
        WHERE f.created_at >= '{S}' AND f.created_at < '{E1}'""",
}
os.makedirs(f'{AIW}/db', exist_ok=True)
for name, sql in SQL.items():
    b = q(sql)
    d = b['data']
    assert not d.get('truncated'), f'{name} 被截断 (>1000 行), 需分批导出'
    json.dump(b, open(f'{AIW}/db/{name}.json', 'w'), ensure_ascii=False, default=str)
    print(name, d['row_count'])
cols = [c['name'] for c in json.load(open(f'{AIW}/db/bridge.json'))['data']['columns']]
rows = [dict(zip(cols, r)) for r in json.load(open(f'{AIW}/db/bridge.json'))['data']['rows']]
open(f'{AIW}/issues.lst', 'w').write('\n'.join(f"{r['jira_key']} {r['multica_issue_id']}" for r in rows if r['multica_issue_id']))
print('issues.lst', sum(1 for r in rows if r['multica_issue_id']))
