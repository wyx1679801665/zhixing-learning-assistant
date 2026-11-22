"""Three goal contrasts against real draft catalogue identities, isolated database only."""
import json,os
from pathlib import Path
from datetime import date,timedelta
import pytest
from app.core.db import transaction,run
from test_platform import P

ROOT=Path(__file__).resolve().parents[2]
def data(name):
    return json.loads((ROOT/'docs/curriculum'/name).read_text(encoding='utf-8-sig'))

CPP='public-C06-K001'
GEO='public-C28-K001'
MOVE='public-C28-K002'

def seed_route_fixture():
    bank=data('original-foundations-bank.json')['questions']
    # Include distractors from both courses, not just expected answers.
    qs=[q for q in bank if q['course_id'] in ('public-C06','public-C28') and q['phase']=='diagnostic']
    ids={q['skill_id'] for q in qs}
    with transaction() as db:
        for course,name in {(q['course_id'],q['course_name']) for q in qs}:
            run(db,"INSERT INTO curriculum_catalog_courses VALUES (:c,:n,1,'test',0,'quiz','published','fixture')",c=course,n=name)
        for q in qs:
            params=dict(k=q['skill_id'],c=q['course_id'],n=q['skill_name'])
            run(db,"INSERT INTO curriculum_catalog_skills VALUES (:k,:c,:n,'approved')",**params)
            run(db,"INSERT INTO learning_questions VALUES (:k,:c,'teacher','now')",**params)
            run(db,"INSERT INTO learning_question_versions(id,question_id,course_id,skill_id,version,phase,prompt,options,answer,explanation,source,status,created_at) VALUES (:k,:k,:c,:k,1,'diagnostic',:p,:o,:a,:e,'fixture','published','now')",**params,p=q['prompt'],o=json.dumps(q['options']),a=q['answer'],e=q['explanation'])
        run(db,"INSERT INTO curriculum_relation_versions VALUES ('rel','hash','fixture',0,1,'now')")
        for edge in data('curriculum-relations.json')['skill_edges']:
            if edge['kind']=='required' and {edge['before_id'],edge['after_id']}<=ids:
                run(db,"INSERT INTO curriculum_skill_prerequisites VALUES ('rel',:b,:a,'required','fixture')",b=edge['before_id'],a=edge['after_id'])

@pytest.mark.parametrize('text,targets,expected',[
    ('用 C++ 编写二维点平移函数，解释平移前后坐标如何变化',[CPP,MOVE],{CPP,GEO,MOVE}),
    ('只解释二维点的平移原理，不涉及编程',[MOVE],{GEO,MOVE}),
    ('只学习 C++ 引用的基本语义',[CPP],{CPP})])
def test_route_contrast(clients,monkeypatch,text,targets,expected):
    seed_route_fixture()
    def advisor(agent,payload):
        if os.getenv('ZHIXING_ROUTE_TRIAL')=='live':
            from app.api.models import call_model
            reply=call_model(dict(model='qwen-plus',endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1',secret_env='DASHSCOPE_API_KEY'),json.dumps(payload,ensure_ascii=False))
            print(agent,text, json.dumps({'route':reply.get('route'),'gaps':reply.get('gaps')},ensure_ascii=False))
            return reply,None
        if targets==[CPP,MOVE] and agent=='CourseRouteAdvisor':
            return {'route':[{'id':key,'reason':'以点类实现'} for key in ('public-C06-K003','public-C06-K004','public-C06-K005',MOVE)],'gaps':[]},None
        if agent=='CourseRouteScopeReviewer':
            assert 'public-C06-K003' in {r['id'] for r in payload['candidate']}
            return {'route':[{'id':k,'reason':'最小实现所需'} for k in targets],
                    'gaps':[{'id':'public-C06-K003','reason':'不要求点类建模'},
                            {'id':'public-C06-K005','reason':'不需要封装'}]},None
        return {'route':[{'id':k,'reason':'模拟模型选择'} for k in targets],'gaps':[]},None
    monkeypatch.setattr('app.services.engineering_goals.model_call',advisor)
    s=clients['student'];r=s.post(P+'/goals/course-route-preview',json={'text':text,'consent':True})
    assert r.status_code==200,r.text
    proposal=r.json()
    assert {n['id'] for n in proposal['scope']}==expected,proposal
    r=s.post(P+'/engineering-goals/proposals/'+proposal['proposal_id']+'/confirm',json={'confirmed':True,'due_date':str(date.today()+timedelta(days=30)),'weekly_minutes':90})
    assert r.status_code==201,r.text
    diag=s.post(P+'/goals/'+r.json()['id']+'/diagnoses')
    assert diag.status_code==201,diag.text
    assert {q['skill'] for q in diag.json()['questions']}=={'catalog:'+k for k in expected}
