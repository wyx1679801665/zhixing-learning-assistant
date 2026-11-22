from app.core.db import transaction,run
from test_platform import P

def test_route_catalog_gate_and_identity(clients,monkeypatch):
    student=clients['student']
    def preview():
        return student.post(P+'/goals/course-route-preview',json={'text':'学函数','consent':True})
    assert preview().json()['cloud_called'] is False
    with transaction() as db:
        run(db,"INSERT INTO curriculum_catalog_courses VALUES ('ct','course',1,'test',0,'quiz','published','test')")
        run(db,"INSERT INTO curriculum_catalog_skills VALUES ('kt','ct','functions','approved')")
        run(db,"INSERT INTO curriculum_catalog_courses VALUES ('base','base',1,'test',0,'quiz','published','test')")
        run(db,"INSERT INTO curriculum_catalog_skills VALUES ('kb','base','base skill','approved'),('extra','base','unrelated','approved')")
        run(db,"INSERT INTO curriculum_relation_versions VALUES ('rel','hash','test',0,1,'now')")
        run(db,"INSERT INTO curriculum_skill_prerequisites VALUES ('rel','kb','kt','required','test')")
    reply={'route':[{'id':'kt','reason':'target'}],'gaps':[]}
    monkeypatch.setattr('app.services.engineering_goals.model_call',lambda *args:(reply,None))
    proposal=preview().json()
    assert proposal['route'][0]['course_id']=='ct'
    from datetime import date,timedelta
    confirm=P+'/engineering-goals/proposals/'+proposal['proposal_id']+'/confirm'
    params={'confirmed':True,'due_date':str(date.today()+timedelta(days=30)),'weekly_minutes':100}
    assert clients['student2'].post(confirm,json=params).status_code==404
    result=student.post(confirm,json=params)
    assert result.status_code==201,result.text
    gid=result.json()['id']
    assert student.post(confirm,json=params).json()['id']==gid
    assert student.post(P+'/goals/'+gid+'/diagnoses').status_code==409
    assert [x['id'] for x in proposal['scope']]==['kb','kt']
    with transaction() as db:
        for skill,course in [('kb','base'),('kt','ct'),('extra','base')]:
            run(db,"INSERT INTO learning_questions VALUES (:q,:c,'teacher','now')",q=skill,c=course)
            run(db,"INSERT INTO learning_question_versions(id,question_id,course_id,skill_id,version,phase,prompt,options,answer,explanation,source,status,created_at) VALUES (:q,:q,:c,:q,1,'diagnostic','example','[1,2]',0,'example','fixture','published','now')",q=skill,c=course)
    diagnosis=student.post(P+'/goals/'+gid+'/diagnoses')
    assert diagnosis.status_code==201,diagnosis.text
    assert {q['skill'] for q in diagnosis.json()['questions']}=={'catalog:kb','catalog:kt'}
    reply['route'][0]['id']='invented'
    assert preview().status_code==503
