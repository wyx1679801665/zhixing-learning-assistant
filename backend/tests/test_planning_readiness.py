from app.core.db import transaction,insert,now
from datetime import date,timedelta

def test_catalog_and_diagnosis_without_model(clients):
    c=clients['student']
    catalog=c.get('/api/v1/curriculum/skills')
    assert catalog.status_code==200
    assert all(s['diagnostic_available'] for s in catalog.json()['skills'])
    r=c.post('/api/v1/goals',json={'title':'学习函数','target_skill_ids':['functions'],'due_date':(date.today()+timedelta(days=30)).isoformat(),'weekly_minutes':120,'preference':'text'})
    assert r.status_code==201,r.text
    d=c.post('/api/v1/goals/'+r.json()['id']+'/diagnoses')
    assert d.status_code==201,d.text
    assert len(d.json()['questions'])>0
    assert all('answer' not in q for q in d.json()['questions'])

def test_model_status_does_not_expose_secrets(clients,monkeypatch):
    c=clients['student']
    assert c.get('/api/v1/planning/model-status').json()['reason']=='not_active'
    with transaction() as db:
        insert(db,'model_configs',id='test-config',model='test-model',endpoint='https://api.openai.com/v1',secret_env='TEST_MODEL_SECRET',status='active',created_at=now())
    monkeypatch.delenv('TEST_MODEL_SECRET',raising=False)
    assert c.get('/api/v1/planning/model-status').json()['reason']=='missing_key'
    monkeypatch.setenv('TEST_MODEL_SECRET','private-token')
    r=c.get('/api/v1/planning/model-status')
    assert r.json()['ready'] is True
    assert 'private-token' not in r.text and 'TEST_MODEL_SECRET' not in r.text

def test_replan_diff_reports_remaining_minutes_after_partial_work(clients):
    from test_platform import P, classroom, resource, goal, planning
    cid,_ = classroom(clients)
    resource(clients,cid)
    gid = goal(clients,cid)
    student = clients['student']
    original = planning(student,gid)
    assert student.post(P+f'/paths/{original}/accept').status_code==200
    old = student.get(P+f'/paths/{original}').json()
    item = old['tasks'][0]
    checked = student.post(P+'/check-ins',json={'path_id':original,'item_id':item['id'],
                                                'minutes':10,'proportion':0.65})
    assert checked.status_code==200,checked.text
    preview = planning(student,gid,replan=True)
    new = student.get(P+f'/paths/{preview}').json()
    diff = new['validation']['diff']
    assert diff['before_remaining_minutes'] < sum(task['minutes'] for task in old['tasks'])
    assert diff['after_remaining_minutes']==sum(task['minutes'] for task in new['validation']['execution'])
    changed = {row['skill_id']:row for row in diff['remaining_by_skill']}
    assert changed[item['skill_id']]['before'] < item['minutes']
    assert changed[item['skill_id']]['original_minutes']==item['minutes']
    assert changed[item['skill_id']]['completion_proportion']==0.65
    assert student.post(P+f'/paths/{preview}/accept').status_code==200
    second=planning(student,gid,replan=True)
    next_diff=student.get(P+f'/paths/{second}').json()['validation']['diff']
    assert next_diff['before_remaining_minutes']==diff['after_remaining_minutes']
    assert next_diff['after_remaining_minutes']==diff['after_remaining_minutes']
    assert student.get(P+f'/paths/{preview}').json()['status']=='active'
    saved=student.get(P+f'/paths/{original}').json()['tasks'][0]['checkin']
    assert saved['proportion']==0.65 and saved['minutes']==10
    continued=next(task for task in new['tasks'] if task['skill_id']==item['skill_id'])
    checked=student.post(P+'/check-ins',json={'path_id':preview,'item_id':continued['id'],
                                             'minutes':5,'proportion':0.45})
    assert checked.status_code==200,checked.text
    third=planning(student,gid,replan=True)
    final=student.get(P+f'/paths/{third}').json()['validation']['diff']
    row=next(row for row in final['remaining_by_skill'] if row['skill_id']==item['skill_id'])
    assert row['original_minutes']==item['minutes']
    assert row['previous_plan_minutes']==continued['minutes']
    assert item['minutes']==30 and continued['minutes']==11
    # Exact remainder: 30 * .35 * .55 = 5.775; rounding 10.5 early yields 7 instead of 6.
    assert row['before']==row['after']==6
    from app.core.db import one
    with transaction() as db:
        reserved=one(db,"SELECT SUM(minutes) AS total FROM reservations WHERE source_id=:p AND status='active'",p=preview)
    assert reserved['total']==final['before_remaining_minutes']


def test_mastered_prerequisite_is_removed_from_replan_without_losing_old_path(clients):
    from test_platform import P, classroom, resource, goal, planning
    from test_batch5 import correct_functions
    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    student=clients['student']
    old_id=planning(student,gid)
    assert student.post(P+f'/paths/{old_id}/accept').status_code==200
    old=student.get(P+f'/paths/{old_id}').json()
    assert 'functions' in {item['skill_id'] for item in old['tasks']}
    correct_functions(clients,cid)
    preview_id=planning(student,gid,replan=True)
    preview=student.get(P+f'/paths/{preview_id}').json()
    remaining={item['skill_id'] for item in preview['tasks']}
    assert 'functions' not in remaining
    assert {'recursion','stack'}<=remaining
    validation=preview['validation']
    diff=validation['diff']
    assert 'functions' in diff['removed']
    assert 'functions' not in diff['reinforced']
    evidence=next(row for row in validation['learner_evidence']['skills'] if row['skill_id']=='functions')
    assert evidence['status']=='mastered'
    minutes=next(row for row in diff['remaining_by_skill'] if row['skill_id']=='functions')
    assert minutes['before']>0 and minutes['after']==0
    assert preview['status']=='pending_acceptance'
    assert student.get(P+f'/paths/{old_id}').json()['status']=='active'


def test_all_mastered_local_replan_returns_no_work_without_empty_candidate(clients):
    from datetime import datetime,timedelta,timezone
    import re
    import pytest
    from fastapi import HTTPException
    from test_platform import P,classroom,resource,goal,planning
    from app.services.learning import QUESTIONS,allocate
    from app.core.db import transaction,one
    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    student=clients['student']
    teacher=clients['teacher']
    original=planning(student,gid)
    assert student.post(P+f'/paths/{original}/accept').status_code==200
    with transaction() as db:
        booked=one(db,"SELECT SUM(minutes) AS total FROM reservations WHERE source_id=:p AND status='active'",p=original)['total']
    assert booked>0
    other_goal=goal(clients,cid)
    for question in (QUESTIONS[0],QUESTIONS[2],QUESTIONS[4]):
        for _ in range(3):
            body={'class_id':cid,'title':'掌握度试行','description':'隔离测验',
                  'due_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
                  'minutes':1,'questions':[question]}
            task=teacher.post(P+'/teacher/tasks',json=body)
            assert task.status_code==201,task.text
            task_id=task.json()['id']
            assert teacher.post(P+f'/teacher/tasks/{task_id}/publish').status_code==200
            response=student.post(P+f'/tasks/{task_id}/attempts',json={
                'answers':{question['id']:question['answer']},'minutes':1})
            assert response.status_code==200,response.text
    with transaction() as db:
        day=one(db,'SELECT MIN(local_date) AS day FROM reservations WHERE source_id=:p',p=original)['day']
        due=one(db,'SELECT due_date FROM goals WHERE id=:id',id=gid)['due_date']
        insert(db,'reservations',id='other-goal-booking',student_id='student',source_type='path',
               source_id='other-path',goal_id=other_goal,local_date=day,minutes=5)

    def shortfall():
        with transaction() as db, pytest.raises(HTTPException) as error:
            allocate(db,'student',100000,due)
        assert error.value.detail['code']=='BUDGET_CONFLICT'
        return int(re.search(r'缺(\d+)分钟',error.value.detail['message']).group(1))

    before=shortfall()
    queued=student.post(P+f'/goals/{gid}/replan-previews',headers={'Idempotency-Key':'all-mastered-local'})
    assert queued.status_code==202,queued.text
    result=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert result['status']=='no_work' and result['result_code']=='NO_PENDING_LEARNING_TASKS'
    with transaction() as db:
        assert one(db,'SELECT COUNT(*) n FROM paths WHERE goal_id=:g',g=gid)['n']==1
        assert one(db,"SELECT COUNT(*) n FROM reservations WHERE source_id=:p AND status='active' AND minutes>0",p=original)['n']==0
    assert f'已释放 {booked} 分钟' in result['error']
    assert 0 < before-shortfall() <= booked
    with transaction() as db:
        assert one(db,"SELECT minutes,status FROM reservations WHERE id='other-goal-booking'")=={'minutes':5,'status':'active'}
    queued=student.post(P+f'/goals/{gid}/replan-previews',headers={'Idempotency-Key':'all-mastered-again'})
    again=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert again['status']=='no_work' and '已释放' not in again['error']
    assert student.get(P+f'/paths/{original}').json()['status']=='active'


def test_legacy_empty_preview_cannot_replace_active_path(clients):
    import json
    from test_platform import P,classroom,resource,goal,planning
    from app.core.db import transaction,run
    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    student=clients['student']
    original=planning(student,gid)
    assert student.post(P+f'/paths/{original}/accept').status_code==200
    preview=planning(student,gid,replan=True)
    with transaction() as db:
        row=db.execute(__import__('sqlalchemy').text('SELECT validation FROM paths WHERE id=:id'),{'id':preview}).mappings().one()
        validation=json.loads(row['validation'])
        validation['execution']=[]
        validation['schedule']=[]
        run(db,'UPDATE paths SET tasks=:tasks,validation=:validation WHERE id=:id',
            tasks='[]',validation=json.dumps(validation,ensure_ascii=False),id=preview)
    response=student.post(P+f'/paths/{preview}/accept')
    assert response.status_code==409,response.text
    assert response.json()['code']=='NO_PENDING_LEARNING_TASKS',response.text
    assert student.get(P+f'/paths/{original}').json()['status']=='active'


def test_legacy_no_work_is_not_reported_as_retryable_failure(clients):
    from test_platform import P,classroom,goal
    cid,_=classroom(clients)
    gid=goal(clients,cid)
    student=clients['student']
    with transaction() as db:
        insert(db,'agent_runs',id='legacy-no-work',actor_id='student',goal_id=gid,
               kind='replan',status='failed',idem_key='legacy-no-work',created_at=now(),
               error='当前目标及先修知识点均已掌握，无需新增学习任务')
    detail=student.get(P+'/runs/legacy-no-work').json()
    assert detail['status']=='no_work'
    assert detail['result_code']=='NO_PENDING_LEARNING_TASKS'
    assert student.get(P+f'/goals/{gid}/runs').json()[0]['status']=='no_work'
    assert student.post(P+'/runs/legacy-no-work/retry').status_code==409


def test_repeated_path_accept_does_not_duplicate_reservations(clients):
    from test_platform import P, classroom, resource, goal, planning
    from app.core.db import transaction, one
    cid,_ = classroom(clients)
    resource(clients,cid)
    gid = goal(clients,cid)
    student = clients['student']
    path_id = planning(student,gid)
    assert student.post(P+f'/paths/{path_id}/accept').status_code==200
    with transaction() as db:
        before=one(db,"SELECT COUNT(*) n FROM reservations WHERE source_id=:i AND status='active'",i=path_id)['n']
    assert before>0
    assert student.post(P+f'/paths/{path_id}/accept').status_code==200
    with transaction() as db:
        after=one(db,"SELECT COUNT(*) n FROM reservations WHERE source_id=:i AND status='active'",i=path_id)['n']
    assert after==before


def test_budget_change_during_build_stops_old_plan(clients,monkeypatch):
    from test_platform import P, classroom, resource, goal
    from app.services import planning as service
    from app.core.db import transaction, run, one
    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    original=service.step
    def change_budget(state,agent,detail):
        if agent=='PathBuildingAgent':
            with transaction() as db:
                run(db,'UPDATE budgets SET version=version+1 WHERE user_id=:u',u=state['actor']['id'])
        return original(state,agent,detail)
    monkeypatch.setattr(service,'step',change_budget)
    student=clients['student']
    queued=student.post(P+f'/goals/{gid}/planning-runs',headers={'Idempotency-Key':'budget-mid-build'})
    assert queued.status_code==202
    result=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert result['status']=='failed' and result['result_code']=='PLANNING_INPUT_CHANGED'
    with transaction() as db:
        assert one(db,'SELECT COUNT(*) n FROM paths WHERE goal_id=:g',g=gid)['n']==0
    monkeypatch.setattr(service,'step',original)
    retried=student.post(P+'/runs/'+queued.json()['run_id']+'/retry')
    assert retried.status_code==202,retried.text
    recovered=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert recovered['status']=='succeeded' and recovered['retry_count']==1
    candidate=student.get(P+'/paths/'+recovered['result_id']).json()
    with transaction() as db:
        current=one(db,'SELECT version FROM budgets WHERE user_id=:u',u='student')['version']
    assert __import__('json').loads(candidate['fingerprint'])['budget']==current


def test_source_withdrawn_during_build_stops_old_plan_and_recovers(clients,monkeypatch):
    from test_platform import P, classroom, resource, goal, planning
    from app.services import planning as service
    from app.core.db import transaction, run, one
    cid,_=classroom(clients)
    rid=resource(clients,cid)
    gid=goal(clients,cid)
    original=service.step
    def withdraw_source(state,agent,detail):
        if agent=='PathBuildingAgent':
            with transaction() as db:
                run(db,"UPDATE resources SET status='withdrawn',version=version+1 WHERE id=:i",i=rid)
        return original(state,agent,detail)
    monkeypatch.setattr(service,'step',withdraw_source)
    student=clients['student']
    queued=student.post(P+f'/goals/{gid}/planning-runs',headers={'Idempotency-Key':'source-mid-build'})
    assert queued.status_code==202
    result=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert result['status']=='failed' and result['result_code']=='PLANNING_INPUT_CHANGED'
    with transaction() as db:
        assert one(db,'SELECT COUNT(*) n FROM paths WHERE goal_id=:g',g=gid)['n']==0
        run(db,"UPDATE resources SET status='published',version=version+1 WHERE id=:i",i=rid)
    monkeypatch.setattr(service,'step',original)
    retried=student.post(P+'/runs/'+queued.json()['run_id']+'/retry')
    assert retried.status_code==202,retried.text
    recovered=student.get(P+'/runs/'+queued.json()['run_id']).json()
    assert recovered['status']=='succeeded' and recovered['retry_count']==1
    candidate=student.get(P+'/paths/'+recovered['result_id']).json()
    with transaction() as db:
        current=one(db,'SELECT version FROM resources WHERE id=:i',i=rid)['version']
    assert next(item['version'] for item in __import__('json').loads(candidate['fingerprint'])['resources'] if item['id']==rid)==current


def test_budget_conflict_suggests_first_feasible_date(clients):
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    from fastapi import HTTPException
    import pytest
    from app.core.db import run
    from app.services.learning import allocate

    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with transaction() as db:
        run(db,"UPDATE budgets SET daily_minutes=30,weekly_minutes=210,timezone='Asia/Shanghai',days='[0,1,2,3,4,5,6]' WHERE user_id='student'")
        with pytest.raises(HTTPException) as failure:
            allocate(db,'student',45,today.isoformat())
        details=failure.value.detail['budget_conflict']
        assert details['shortfall_minutes']==15
        assert details['earliest_finish_date']==(today+timedelta(days=1)).isoformat()
        schedule=allocate(db,'student',45,details['earliest_finish_date'])
        assert [entry['minutes'] for entry in schedule]==[30,15]
        trial=allocate(db,'student',45,today.isoformat(),budget_override={'daily_minutes':45})
        assert [entry['minutes'] for entry in trial]==[45]
        from app.core.db import one
        assert one(db,"SELECT daily_minutes FROM budgets WHERE user_id='student'")['daily_minutes']==30


def test_two_goals_share_daily_budget(clients):
    from test_platform import P, classroom, resource, goal, planning
    from app.core.db import transaction, one, rows
    cid,_=classroom(clients)
    resource(clients,cid)
    student=clients['student']
    first=planning(student,goal(clients,cid))
    assert student.post(P+f'/paths/{first}/accept').status_code==200
    second=planning(student,goal(clients,cid))
    assert student.post(P+f'/paths/{second}/accept').status_code==200
    with transaction() as db:
        budget=one(db,'SELECT daily_minutes FROM budgets WHERE user_id=:u',u='student')
        days=rows(db,"SELECT local_date,SUM(minutes) total FROM reservations WHERE student_id=:u AND status='active' GROUP BY local_date",u='student')
    assert days and all(day['total']<=budget['daily_minutes'] for day in days)


def test_two_candidates_accepted_concurrently_leave_one_active(clients):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from app.main import app
    from test_platform import P, classroom, resource, goal, planning
    from app.core.db import transaction, one
    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    first=planning(clients['student'],gid)
    second=planning(clients['student'],gid)
    assert first!=second
    with TestClient(app) as a, TestClient(app) as b:
        for client in (a,b):
            assert client.post(P+'/auth/login',json={'username':'student','password':'TestPassword123!'}).status_code==200
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda item:item[0].post(P+f'/paths/{item[1]}/accept'),[(a,first),(b,second)]))
    assert sorted(r.status_code for r in responses)==[200,409]
    with transaction() as db:
        assert one(db,"SELECT COUNT(*) n FROM paths WHERE goal_id=:g AND status='active'",g=gid)['n']==1
        assert one(db,"SELECT COUNT(DISTINCT source_id) n FROM reservations WHERE goal_id=:g AND status='active'",g=gid)['n']==1

def test_goal_weekly_limit_counts_prior_work_on_that_goal_only(clients):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.core.db import run
    from app.services.learning import allocate
    from test_platform import P, classroom, resource, goal, planning

    cid,_=classroom(clients)
    resource(clients,cid)
    gid=goal(clients,cid)
    student=clients['student']
    path_id=planning(student,gid)
    assert student.post(P+f'/paths/{path_id}/accept').status_code==200
    task=student.get(P+f'/paths/{path_id}').json()['tasks'][0]
    assert student.post(P+'/check-ins',json={'path_id':path_id,'item_id':task['id'],
                                            'minutes':20,'proportion':0.5}).status_code==200
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    due=(today+timedelta(days=14)).isoformat()
    with transaction() as db:
        run(db,"UPDATE budgets SET daily_minutes=180,weekly_minutes=600,timezone='Asia/Shanghai',days='[0,1,2,3,4,5,6]' WHERE user_id='student'")
        same=allocate(db,'student',30,due,gid,30)
        other=allocate(db,'student',30,due,'other-goal',30)
    assert same[0]=={'date':today.isoformat(),'minutes':10}
    assert sum(day['minutes'] for day in same)==30
    assert other==[{'date':today.isoformat(),'minutes':30}]
