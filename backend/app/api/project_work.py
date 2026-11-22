"""Project work is submitted as evidence and graded only by an assigned teacher."""
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictInt
from app.core.db import transaction, one, rows, run, insert, uid, now, dump, audit
from app.core.security import current_user, role, fail
from app.services.p0_project_rubric import COURSE as TRIAL_PROJECT_COURSE

router=APIRouter()
CASES={'nested','empty','single'}

class ProjectWork(BaseModel):
    code_text:str=Field(min_length=20,max_length=20000)
    test_code:str=Field(min_length=20,max_length=12000)
    observed:dict[str,StrictInt]
    input_contract:str=Field(min_length=10,max_length=2000)
    explanation:str=Field(min_length=20,max_length=6000)

class TeacherReview(BaseModel):
    criterion_scores:dict[str,StrictInt]
    requires_revision:bool=False
    comment:str=Field(min_length=10,max_length=4000)

def rubric(db,course_id):
    if course_id!=TRIAL_PROJECT_COURSE:fail(404,'该项目尚未配置作品提交规则')
    course=one(db,'SELECT id,status,assessment_mode FROM curriculum_catalog_courses WHERE id=:i',i=course_id)
    if not course or course['assessment_mode']!='project_rubric':fail(404,'项目课程不存在')
    record=one(db,'''SELECT r.id,r.payload FROM learning_project_rubrics r
        JOIN learning_bank_releases b ON b.id=r.release_id
        WHERE r.course_id=:c ORDER BY b.created_at DESC,r.id DESC LIMIT 1''',c=course_id)
    if not record:fail(409,'项目量规尚未登记')
    return course,record,json.loads(record['payload'])

def dto(db,record):
    result=dict(record)
    result['observed']=json.loads(result['observed'])
    bound=one(db,'SELECT payload FROM learning_project_rubrics WHERE id=:i AND course_id=:c',i=record['rubric_id'],c=record['course_id'])
    if not bound:fail(409,'作品对应的评分量规版本不存在')
    result['rubric']=json.loads(bound['payload'])
    review=one(db,'SELECT teacher_id,criterion_scores,total_score,requires_revision,comment,created_at FROM project_reviews WHERE submission_id=:i',i=record['id'])
    if review:
        review['criterion_scores']=json.loads(review['criterion_scores'])
        review['requires_revision']=bool(review['requires_revision'])
    result['review']=review
    return result

@router.get('/learning/project-courses/{course_id}/my-submissions')
def my_submissions(course_id:str,user=Depends(current_user)):
    role(user,'student')
    with transaction() as db:
        rubric(db,course_id)
        return [dto(db,r) for r in rows(db,'SELECT * FROM project_submissions WHERE course_id=:c AND student_id=:u ORDER BY version DESC',c=course_id,u=user['id'])]

@router.post('/learning/project-courses/{course_id}/submissions',status_code=201)
def submit(course_id:str,body:ProjectWork,user=Depends(current_user)):
    role(user,'student')
    if set(body.observed)!=CASES:fail(422,'请分别填写嵌套目录、空目录、单文件的实际结果')
    if any(not str(value).strip() for value in (body.code_text,body.test_code,body.input_contract,body.explanation)):
        fail(422,'代码、测试和解释不能只填空白')
    with transaction() as db:
        _,r,_=rubric(db,course_id)
        if not one(db,'SELECT 1 FROM course_reviewers WHERE course_id=:c',c=course_id):
            fail(409,'该项目课程尚未指定审核教师，请管理员先分配','PROJECT_REVIEWER_MISSING')
        latest=one(db,'SELECT * FROM project_submissions WHERE rubric_id=:r AND student_id=:u ORDER BY version DESC LIMIT 1',r=r['id'],u=user['id'])
        content=body.model_dump()
        if latest and latest['status']=='submitted':
            same=all(latest[key]==content[key] for key in ('code_text','test_code','input_contract','explanation')) and json.loads(latest['observed'])==content['observed']
            if same:return dto(db,latest)
            fail(409,'已有待教师评分的版本；请等待审核后再提交修改','PROJECT_REVIEW_PENDING')
        if latest and latest['status']=='graded':fail(409,'项目已评分；如需重交，请由教师明确要求修订','PROJECT_ALREADY_GRADED')
        ident=uid();number=latest['version']+1 if latest else 1
        insert(db,'project_submissions',id=ident,rubric_id=r['id'],course_id=course_id,student_id=user['id'],version=number,
               code_text=content['code_text'],test_code=content['test_code'],observed=dump(content['observed']),
               input_contract=content['input_contract'],explanation=content['explanation'],status='submitted',created_at=now())
        audit(db,user['id'],'project_work_submitted',ident,dump({'rubric_id':r['id'],'version':number}))
        return dto(db,one(db,'SELECT * FROM project_submissions WHERE id=:i',i=ident))

@router.get('/learning/project-courses/{course_id}/review-queue')
def review_queue(course_id:str,user=Depends(current_user)):
    role(user,'teacher')
    with transaction() as db:
        rubric(db,course_id)
        if not one(db,'SELECT 1 FROM course_reviewers WHERE course_id=:c AND teacher_id=:u',c=course_id,u=user['id']):
            fail(403,'未获该项目课程评分权限')
        return [dto(db,r) for r in rows(db,'SELECT * FROM project_submissions WHERE course_id=:c ORDER BY created_at DESC',c=course_id)]

@router.post('/learning/project-courses/{course_id}/submissions/{submission_id}/review')
def review(course_id:str,submission_id:str,body:TeacherReview,user=Depends(current_user)):
    role(user,'teacher')
    with transaction() as db:
        rubric(db,course_id)
        if not one(db,'SELECT 1 FROM course_reviewers WHERE course_id=:c AND teacher_id=:u',c=course_id,u=user['id']):
            fail(403,'未获该项目课程评分权限')
        submission=one(db,'SELECT * FROM project_submissions WHERE id=:i AND course_id=:c',i=submission_id,c=course_id)
        if not submission:fail(404,'项目提交不存在')
        bound=one(db,'SELECT payload FROM learning_project_rubrics WHERE id=:i AND course_id=:c',i=submission['rubric_id'],c=course_id)
        if not bound:fail(409,'作品对应的评分量规版本不存在')
        r=json.loads(bound['payload'])
        limits={item['name']:item['max_score'] for item in r['criteria']}
        if set(body.criterion_scores)!=set(limits):fail(422,'请对量规全部评分项逐项打分')
        if any(score<0 or score>limits[name] for name,score in body.criterion_scores.items()):
            fail(422,'评分超出对应量规分值')
        if not body.comment.strip():fail(422,'请填写具体评分依据')
        existing=one(db,'SELECT * FROM project_reviews WHERE submission_id=:i',i=submission_id)
        if existing:
            same=(json.loads(existing['criterion_scores'])==body.criterion_scores and bool(existing['requires_revision'])==body.requires_revision and existing['comment']==body.comment.strip())
            if same:return dto(db,submission)
            fail(409,'此版本已有评分；历史不可覆盖，请按正式更正流程处理','PROJECT_ALREADY_REVIEWED')
        if submission['status']!='submitted':fail(409,'仅可评分待审核项目')
        total=sum(body.criterion_scores.values())
        insert(db,'project_reviews',id=uid(),submission_id=submission_id,teacher_id=user['id'],criterion_scores=dump(body.criterion_scores),
               total_score=total,requires_revision=int(body.requires_revision),comment=body.comment.strip(),created_at=now())
        run(db,'UPDATE project_submissions SET status=:s WHERE id=:i',s='revision_requested' if body.requires_revision else 'graded',i=submission_id)
        audit(db,user['id'],'project_work_reviewed',submission_id,dump({'total_score':total,'requires_revision':body.requires_revision}))
        return dto(db,one(db,'SELECT * FROM project_submissions WHERE id=:i',i=submission_id))
