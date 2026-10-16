"""两件事: ① 从 VelaAgent 评论里抽用户反馈 (未同步进 ai_feedback_records 的那部分) → db/multica_fb.json;
② (需先跑 analyze.py) 每个已分析量 ≥5 的垂域抽 5 单 (优先 Jira 已解决/有反馈) → review_pick.json + review_pack/<垂域>.json.

用法: python prep_review.py fb      # analyze 之前
      python prep_review.py pick    # analyze 之后, 产出 extra_keys.txt 供 jira_comments.py 补拉
      python prep_review.py pack    # jira_comments.py 之后
"""
import os; AIW = os.environ.get('AIW_WORK', '/tmp/aiweekly')   # 工作目录: 原始数据与中间产物
import glob, json, random, re, sys
from collections import defaultdict

step = sys.argv[1]
if step == 'fb':
    hits = {}
    for f in glob.glob(f'{AIW}/raw/*.json'):
        d = json.load(open(f))
        for c in d['comments']:
            m = re.search(r"评价[:：]\s*(有用|部分有用|无用)[；;]?\s*(?:反馈根因[:：])?(.*)", c['content'] or '', re.S)
            if m and c['author_type'] == 'member':
                hits[d['jira_key']] = {'feedback': {'有用': 'useful', '部分有用': 'partial_useful', '无用': 'not_useful'}[m.group(1)],
                                       'partial_score': None, 'reason_l1': None, 'reason_l2': None,
                                       'reason_detail': re.sub(r'\s+', ' ', m.group(2))[:300].strip("'\" "), 'user_name': '(VelaAgent 评论)',
                                       'created_at': c['created_at'][:19], 'issue_key': d['jira_key'], '_src': 'multica'}
    json.dump(hits, open(f'{AIW}/db/multica_fb.json', 'w'), ensure_ascii=False)
    print('multica feedback', len(hits))
elif step == 'pick':
    rows = json.load(open(f'{AIW}/rows.json'))
    jc = json.load(open(f'{AIW}/db/jira_comments.json')) if os.path.exists(f'{AIW}/db/jira_comments.json') else {}
    random.seed(42)
    pool = defaultdict(list)
    for r in rows:
        if r['state'] == 'analyzed' and not r['circle']:
            pool[r['domain']].append(r)
    pick = {}
    for d, L in pool.items():
        if len(L) < 5:
            continue
        pri = [r for r in L if r['resolved'] or r['fb']]; rest = [r for r in L if r not in pri]
        random.shuffle(pri); random.shuffle(rest)
        pick[d] = [r['key'] for r in (pri + rest)[:5]]
    json.dump(pick, open(f'{AIW}/review_pick.json', 'w'), ensure_ascii=False)
    open(f'{AIW}/extra_keys.txt', 'w').write(' '.join(k for L in pick.values() for k in L if k not in jc))
    print({d: len(L) for d, L in pick.items()})
elif step == 'pack':
    pick = json.load(open(f'{AIW}/review_pick.json')); jc = json.load(open(f'{AIW}/db/jira_comments.json'))
    fbm = json.load(open(f'{AIW}/db/multica_fb.json'))
    os.makedirs(f'{AIW}/review_pack', exist_ok=True); os.makedirs(f'{AIW}/review', exist_ok=True)
    for d, keys in pick.items():
        pack = []
        for k in keys:
            raw = json.load(open(f'{AIW}/raw/{k}.json'))
            cm = [c for c in raw['comments'] if c['author_type'] == 'agent' and not (c['content'] or '').startswith('[vela')]
            fin = [c for c in cm if re.search(r'(?m)^[\s>*#]*\[FINAL\s*#', c['content'] or '')]
            concl = (fin or [c for c in cm if len(c['content'] or '') > 400] or cm or [{'content': ''}])[-1]['content']
            j = jc.get(k, {})
            pack.append({'jira_key': k, 'domain': d, 'jira_summary': j.get('summary'), 'jira_status': j.get('status'),
                         'jira_resolution': j.get('resolution'), 'jira_assignee': j.get('assignee'),
                         'jira_human_comments': j.get('comments', []), 'user_feedback': fbm.get(k), 'ai_conclusion': concl[:4000]})
        json.dump(pack, open(f'{AIW}/review_pack/{d}.json', 'w'), ensure_ascii=False, indent=1)
        print(d, len(pack))
