import json,os,sqlite3
import pytest
from pathlib import Path
from app.core.db import insert
from collections import Counter
from datetime import date,timedelta
from test_route_contrasts import seed_route_fixture,CPP,MOVE,GEO,data,P
from app.core.db import transaction,run
from app.services.engineering_materials import import_materials

ROOT=Path(__file__).resolve().parents[2]

def approved_snapshot(db):
    from contextlib import closing
    with closing(sqlite3.connect((ROOT/'runtime-data/zhixing.db').as_uri()+'?mode=ro',uri=True)) as src:
        src.row_factory=sqlite3.Row
        cfg=src.execute("select * from model_configs where status='active' order by created_at desc limit 1").fetchone()
        assert cfg and cfg['model']=='qwen-plus' and cfg['endpoint'].rstrip('/')=='https://dashscope.aliyuncs.com/compatible-mode/v1' and cfg['secret_env']=='DASHSCOPE_API_KEY'
        rep=src.execute('select * from model_quality_reports where model_config_id=? order by created_at desc limit 1',(cfg['id'],)).fetchone()
        assert rep and rep['review_status']=='approved' and rep['passed_count']==rep['case_count']==60
        insert(db,'model_configs',**dict(cfg))
        # External reviewer identity is not imported into the disposable student fixture.
        insert(db,'model_quality_reports',**{**dict(rep),'reviewer_id':None})

@pytest.mark.parametrize('student,weak',[('student',MOVE),('student2',CPP)])
def test_opposite_diagnoses_schedule_opposite_skills(clients,monkeypatch,student,weak):
    partial_live=os.getenv('ZHIXING_DAILY_TRIAL')=='partial-live'
    live=os.getenv('ZHIXING_DAILY_TRIAL')=='live'

    seed_route_fixture()
    import_materials()
    bank=data('original-foundations-bank.json')['questions']
    answers={q['skill_id']:int(q['answer']) for q in bank if q['phase']=='diagnostic'}
    with transaction() as db:
        run(db,"UPDATE resources SET status='published' WHERE scope='school'")
        if live or partial_live:
            approved_snapshot(db)
            run(db,"UPDATE resources SET cloud_allowed=1 WHERE scope='school'")
        # Separate practice items are used as second diagnostic probes ONLY in this fixture.
        for q in bank:
            if q['phase']!='practice' or q['skill_id'] not in (CPP,MOVE,GEO):continue
            k=q['id'];answers[k]=int(q['answer'])
            run(db,"INSERT INTO learning_questions VALUES (:k,:c,'teacher','now')",k=k,c=q['course_id'])
            run(db,"INSERT INTO learning_question_versions(id,question_id,course_id,skill_id,version,phase,prompt,options,answer,explanation,source,status,created_at) VALUES (:k,:k,:c,:s,1,'diagnostic',:p,:o,:a,:e,'fixture','published','now')",k=k,c=q['course_id'],s=q['skill_id'],p=q['prompt'],o=json.dumps(q['options']),a=q['answer'],e=q['explanation'])
    monkeypatch.setattr('app.services.engineering_goals.model_call',lambda *args:({'route':[{'id':k,'reason':'fixture'} for k in [CPP,MOVE]],'gaps':[]},None))
    s=clients[student]
    budget=s.get(P+'/learners/me/budget').json()
    assert s.patch(P+'/learners/me/budget',json=dict(daily_minutes=25,weekly_minutes=50,days=list(range(7)),version=budget['version'])).status_code==200
    r=s.post(P+'/goals/course-route-preview',json={'text':'用C++编写二维点平移函数','consent':True}).json()
    g=s.post(P+'/engineering-goals/proposals/'+r['proposal_id']+'/confirm',json=dict(confirmed=True,due_date=str(date.today()+timedelta(days=30)),weekly_minutes=50)).json()['id']
    d=s.post(P+'/goals/'+g+'/diagnoses').json()
    assert len(d['questions'])==6
    payload={q['id']:str((answers[q['id']]+int(q['skill']=='catalog:'+weak))%len(q['options'])) for q in d['questions']}
    assert s.post(P+'/diagnoses/'+d['id']+'/submit',json=dict(answers=payload,minutes=5)).status_code==200
    j=s.post(P+'/goals/'+g+'/planning-runs?engine='+('cloud' if live else 'local'),headers={'Idempotency-Key':'daily-'+student}).json()
    v=s.get(P+'/runs/'+j['run_id']).json()
    target=ROOT/'runtime-data'/('daily-trial-'+student+'.json')
    if live:target.write_text(json.dumps({'skill':weak,'run':v},ensure_ascii=False,indent=2),encoding='utf8')
    assert v['status']=='succeeded',v.get('error')
    path=s.get(P+'/paths/'+v['result_id']).json()
    if live:
        target.write_text(json.dumps({'skill':weak,'run':v,'preview':path},ensure_ascii=False,indent=2),encoding='utf8')
    assert [t['skill_id'] for t in path['validation']['execution']]==['catalog:'+weak]
    weeks=Counter();days=Counter()
    for row in path['validation']['schedule']:
        dt=date.fromisoformat(row['date']);days[dt]+=row['minutes'];weeks[dt.isocalendar()[:2]]+=row['minutes']
    tasks=path['validation']['execution']
    assert all(d.get('activity') and d.get('deliverable') for t in tasks for d in t['dates'])
    assert sum(days.values())==sum(t['minutes'] for t in tasks)>0
    if live:
        assert path['validation']['cloud_called']
        assert all(t['instructions'].strip() and any(c.get('excerpt') and c['version_id'].endswith(weak+'-v1') for c in t['citations']) for t in tasks)
    else:assert sum(days.values())==60
    assert max(days.values())<=25 and max(weeks.values())<=50
    print(student,weak,dict(days))

    if not live:
        assert s.post(P+'/paths/'+path['id']+'/accept').status_code==200
        task=tasks[0]
        # 20 minutes complete, then 12 of the next 25; progress is explicitly reported.
        ck=s.post(P+'/check-ins',json=dict(path_id=path['id'],item_id=task['id'],minutes=32,proportion=32/60,note='隔离验收：第二段做到一半左右'))
        assert ck.status_code==200,ck.text
        job=s.post(P+'/goals/'+g+'/replan-previews?engine='+('cloud' if partial_live else 'local'),headers={'Idempotency-Key':'partial-'+student}).json()
        res=s.get(P+'/runs/'+job['run_id']).json()
        if partial_live:
            report=ROOT/'runtime-data'/('daily-partial-'+student+'.json')
            evidence={'prior_task':task,'reported_minutes':32,'reported_proportion':32/60,'run':res}
            report.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
        assert res['status']=='succeeded',res.get('error')
        preview=s.get(P+'/paths/'+res['result_id']).json()
        if partial_live:
            evidence['preview']=preview
            report.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
            assert preview['validation']['cloud_called']
        assert len(preview['validation']['execution'])==1
        nxt=preview['validation']['execution'][0]
        assert nxt['skill_id']=='catalog:'+weak
        assert all(d['activity'] and d['deliverable'] and d['minutes']<=25 for d in nxt['dates'])
        assert any(c['version_id'].endswith(weak+'-v1') for c in nxt['citations'])
        assert nxt['minutes']==28 and sum(d['minutes'] for d in nxt['dates'])==28
        assert nxt['resume_proportion']==32/60 and nxt['instructions'].strip()
        if not partial_live:
            assert '接着完成' in nxt['instructions']
            assert '先阅读本知识点关联正文' not in nxt['instructions']
            assert '小练习' in nxt['instructions'] and '复盘' in nxt['instructions']
        assert s.get(P+'/paths/'+path['id']).json()['status']=='active'
