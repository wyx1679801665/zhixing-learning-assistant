import json
import pytest
from app.core.db import transaction,one,run
from test_platform import classroom,resource,goal,P


def activate_cloud(clients,monkeypatch):
    from app.api import models
    def reply(config,prompt):
        if prompt.startswith('{') and 'case' in json.loads(prompt):
            case=json.loads(prompt)['case']
            assert "expected_status" not in case
            decision={'unknown':'diagnose','weak':'remediate','mastered':'proceed'}[case['prerequisite']['state']]
            kinds=([decision] if decision!='proceed' else [])+['practice','assessment']
            tasks=[{'skill_id':case['prerequisite']['skill_id'] if kind in ('diagnose','remediate') else case['skill_id'],'minutes':5,'kind':kind,'activity':'逐步分析示例并记录每次调用变化','deliverable':'提交完整的调用变化记录及解释','source_id':case['sources'][-1 if kind in ('diagnose','remediate') else 0]['id'],'quote':case['sources'][-1 if kind in ('diagnose','remediate') else 0]['text']} for kind in kinds] if case['sources'] else []
            return {'status':'candidate' if case['sources'] else 'draft','tasks':tasks,'reason':'依据资料与先修证据安排学习','prerequisite_decision':decision,'prerequisite_evidence_id':case['prerequisite']['evidence_id']}
        if 'agent' not in prompt:return {'ok':True}
        request=json.loads(prompt);task=request['task'];agent=request['agent']
        if agent=='GoalDiagnosisAgent':return {'summary':'根据诊断安排先修知识','risks':[]}
        if agent=='PathBuildingAgent':
            return {'tasks':[{'skill_id':item['skill_id'],'minutes':item['minutes']+1,'instructions':'阅读例题并解释运行步骤','citation_version_ids':[source['version_id'] for source in task['sources'] if item['skill_id'] in source['supported_skills']]} for item in task['tasks']],'reason':'按诊断补强'}
        if agent=='PlanReviewAgent':return {'approved':True,'issues':[]}
        return {'reason':'按证据和预算安排学习','recommendations':['完成后重测']}
    monkeypatch.setattr(models,'call_model',reply)
    admin=clients['admin'];created=admin.post(P+'/admin/model-configs',json={'model':'qwen-plus'}).json()['id']
    assert admin.post(P+f'/admin/model-configs/{created}/evaluate').status_code==200
    assert admin.post(P+f'/admin/model-configs/{created}/activate').status_code==409
    monkeypatch.setenv('DASHSCOPE_API_KEY','test-only')
    from app.services.quality_evaluation import evaluate
    from app.core.db import DATA
    report=evaluate(created,DATA/(created+'-quality.json'),'contract-test')
    assert admin.post(P+f'/admin/model-configs/{created}/quality-reports/'+report['quality_report_id']+'/review',json={'approved':True,'comment':'契约测试替身，仅验证接口流程','reviewed_case_ids':[entry['case']['id'] for entry in report['results']]}).status_code==200
    assert admin.post(P+f'/admin/model-configs/{created}/activate').status_code==200
    return created,reply


@pytest.mark.parametrize('budget_case',['normal','tight_fit','tight_overflow'])
def test_cloud_four_agent_entry_and_privacy(clients,monkeypatch,budget_case):
    config,reply=activate_cloud(clients,monkeypatch)
    cid,_=classroom(clients);rid=resource(clients,cid);gid=goal(clients,cid)
    student=clients['student'];teacher=clients['teacher']
    result=student.post(P+f'/goals/{gid}/planning-runs?engine=cloud',headers={'Idempotency-Key':'privacy'}).json()
    failed=student.get(P+'/runs/'+result['run_id']).json()
    assert failed['status']=='failed' and '授权' in failed['error']
    current=teacher.get(P+'/resources/'+rid).json()
    assert teacher.patch(P+'/resources/'+rid,json={'version':current['version'],'cloud_allowed':True}).status_code==200
    if budget_case!='normal':
        from app.api import models
        from app.services import planning as planner
        builds=[]
        original_build=planner.build
        def tracked_build(*args,**kwargs):
            builds.append(1)
            return original_build(*args,**kwargs)
        monkeypatch.setattr(planner,'build',tracked_build)
        review_count=[]
        def compact(config,prompt):
            response=reply(config,prompt)
            if json.loads(prompt).get('agent')=='PathBuildingAgent':
                payload=json.loads(prompt)['task']
                budget=payload['budget_constraints']
                assert 0 < budget['schedulable_reference_minutes'] < budget['reference_minutes']
                assert budget['shortfall_minutes']==budget['reference_minutes']-budget['schedulable_reference_minutes']
                assert budget['fixed_remaining_minutes']==0
                assert budget['available_for_adjustable_tasks']==budget['schedulable_reference_minutes']
                assert budget['weekly_minutes']==10
                assert payload['budget_constraints']['study_weekdays']==list(range(7))
                assert all(task['minutes_fixed'] is False for task in payload['tasks'])
                for task in response['tasks']:
                    assert task['minutes']>10
                    task['minutes']=2 if budget_case=='tight_fit' else 100
            if json.loads(prompt).get('agent')=='PlanReviewAgent' and not review_count:
                review_count.append(1)
                return {'approved':False,'issues':['请补充活动说明']}
            return response
        monkeypatch.setattr(models,'call_model',compact)
        with transaction() as db:
            run(db,"UPDATE budgets SET daily_minutes=10,weekly_minutes=10,days='[0,1,2,3,4,5,6]' WHERE user_id='student'")
    result=student.post(P+f'/goals/{gid}/planning-runs?engine=cloud',headers={'Idempotency-Key':'cloud'}).json()
    completed=student.get(P+'/runs/'+result['run_id']).json()
    if budget_case!='normal':
        assert len(builds)==1
    if budget_case=='tight_overflow':
        assert completed['status']=='failed',completed
        assert not completed.get('result_id')
        assert '分钟' in completed['error']
        with transaction() as db:
            checkpoint=one(db,"SELECT output_summary FROM agent_steps WHERE run_id=:r AND status='failed' LIMIT 1",r=result['run_id'])
            assert not one(db,'SELECT id FROM paths WHERE goal_id=:g',g=gid)
        details=json.loads(checkpoint['output_summary'])
        assert details['code']=='BUDGET_CONFLICT'
        conflict=details['budget_conflict']
        assert conflict['shortfall_minutes']==conflict['required_minutes']-conflict['available_minutes']>0
        assert conflict['earliest_finish_date']>conflict['due_date']
        return
    assert completed['status']=='succeeded',completed
    assert len(completed['steps'])==(6 if budget_case=='tight_fit' and review_count else 4)
    path=student.get(P+'/paths/'+completed['result_id']).json()
    assert path['validation']['cloud_called'] is True
    assert path['validation']['model_config_id']==config
    assert all(item['instructions'] for item in path['tasks'])
    validation=path['validation']
    if budget_case=='tight_fit':
        trial=validation['reference_budget']
        assert trial['reference_minutes']>trial['schedulable_reference_minutes']>=sum(day['minutes'] for day in validation['schedule'])
    final_minutes={item['skill_id']:item['minutes'] for item in validation['execution']}
    diff=validation['diff']
    assert {row['skill_id']:row['after'] for row in diff['remaining_by_skill']}==final_minutes
    assert diff['after_remaining_minutes']==sum(final_minutes.values())==sum(day['minutes'] for day in diff['after_schedule'])
    with transaction() as db:
        for item in path['tasks']:
            for citation in item['citations']:
                body=one(db,'SELECT body FROM resource_versions WHERE id=:id',id=citation['version_id'])['body']
                assert citation['excerpt'] and citation['excerpt']==body[citation['start']:citation['end']]
    assert student.post(P+'/paths/'+path['id']+'/accept').status_code==200


def test_upload_only_and_bounded_search(clients,monkeypatch):
    from app.api import evidence
    calls=[]
    monkeypatch.setattr(evidence,'search_web',lambda query:calls.append(query) or [])
    student=clients['student']
    assert student.post(P+'/retrieval/supplement',json={'query':'递归','mode':'upload_only'}).json()['rounds']==0
    assert not calls
    result=student.post(P+'/retrieval/supplement',json={'query':'递归','mode':'web_allowed'}).json()
    assert result['rounds']==2 and len(calls)==2 and result['status']=='draft'
    result=student.post(P+'/retrieval/supplement',json={'query':'递归','mode':'web_allowed','nodes':[{'id':'recursion','title':'递归','minutes':0,'question_count':0}]}).json()
    assert result['web_search_executed'] is False and len(calls)==2


def test_authorized_link_import_and_audit_result(clients,monkeypatch):
    from app.api import evidence
    cid,_=classroom(clients);teacher=clients['teacher']
    rid=teacher.post(P+'/resources/links',json={'class_id':cid,'url':'https://example.edu/lesson','title':'递归','description':'课程来源'}).json()['id']
    monkeypatch.setattr(evidence,'fetch_page',lambda url:{'url':url,'title':'递归','body':'递归函数需要终止条件，这是教师授权导入的课程正文。','domain':'example.edu','published_at':None})
    current=teacher.get(P+'/resources/'+rid).json()
    url=P+'/resources/'+rid+'/import-link'
    assert clients['student'].post(url,json={'authorized':True,'version':current['version']}).status_code==403
    assert teacher.post(url,json={'authorized':False,'version':current['version']}).status_code==422
    assert teacher.post(url,json={'authorized':True,'version':current['version']}).status_code==201
    imported=teacher.get(P+'/resources/'+rid).json()
    assert imported['review_status']=='pending'
    assert teacher.post(P+'/resources/'+rid+'/publish').status_code==409
    assert teacher.post(P+'/resources/'+rid+'/review-extraction',json={'version':imported['version'],'body':'递归函数需要终止条件，这是教师授权导入的课程正文。'}).status_code==200
    assert not clients['student'].post(P+'/retrieval/search',json={'query':'递归'}).json()['results']
    assert teacher.post(P+'/resources/'+rid+'/publish').status_code==200
    assert clients['student'].post(P+'/retrieval/search',json={'query':'递归'}).json()['results']
    assert not clients['student2'].post(P+'/retrieval/search',json={'query':'递归'}).json()['results']
    clients['student'].post(P+'/auth/login',json={'username':'student','password':'bad'})
    logs=clients['admin'].get(P+'/admin/audit?result=failed').json()
    assert logs and all(log['result']=='failed' for log in logs)


@pytest.mark.parametrize('url',['http://127.0.0.1/','http://[::1]/','http://169.254.169.254/','https://user:pass@example.com/','file:///etc/passwd'])
def test_fetch_rejects_private_and_credential_urls(url):
    from app.services.web_evidence import public_target
    with pytest.raises(ValueError):public_target(url)



def test_search_evaluation_is_admin_only(clients,monkeypatch):
    from app.api import evidence
    calls=[]
    monkeypatch.setattr(evidence,'search_web',lambda query: calls.append(query) or [])
    for role in ('student','teacher'):
        assert clients[role].post(P+'/admin/search/evaluate').status_code==403
    assert calls==[]
    assert clients['admin'].post(P+'/admin/search/evaluate').status_code==200
    assert len(calls)==1
def test_web_text_keeps_inline_formula_alt_in_reading_order():
    from app.services.web_evidence import PageText
    page=PageText()
    page.feed('<p>旋转公式<img alt="q p q*">再提取虚部</p>'
              '<template><img alt="隐藏公式"></template><img src="missing.png">')
    assert page.parts==['旋转公式','q p q*','再提取虚部']
