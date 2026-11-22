from app.services.p0_project_rubric import seed, COURSE, RUBRIC
from app.core.db import transaction, insert, dump, now
import copy

P='/api/v1'
WORK={
    'code_text':'def count_files(tree):\n    if tree["kind"] == "file": return 1\n    return sum(count_files(c) for c in tree["children"])',
    'test_code':'assert count_files({"kind":"dir","children":[]}) == 0\nassert count_files({"kind":"file"}) == 1',
    'observed':{'nested':3,'empty':0,'single':1},
    'input_contract':'kind=file or kind=dir with children list',
    'explanation':'文件节点返回一，目录递归求和，调用从最深层返回到外层。',
}

def test_project_work_teacher_review_and_version_history(clients):
    seed()
    student=clients['student'];teacher=clients['teacher'];teacher2=clients['teacher2'];admin=clients['admin']
    base=P+f'/learning/project-courses/{COURSE}'
    assert student.get(base+'/my-submissions').json()==[]
    assert teacher2.get(base+'/review-queue').status_code==403
    assert student.post(base+'/submissions',json=WORK).json()['code']=='PROJECT_REVIEWER_MISSING'
    assert admin.put(P+f'/learning/courses/{COURSE}/reviewers/teacher',json={'assigned':True}).status_code==200
    assert teacher.get(base+'/review-queue').json()==[]
    bad={**WORK,'observed':{'nested':3}}
    assert student.post(base+'/submissions',json=bad).status_code==422
    first=student.post(base+'/submissions',json=WORK)
    assert first.status_code==201,first.text
    first=first.json();assert first['version']==1 and first['status']=='submitted' and first['review'] is None
    assert student.post(base+'/submissions',json=WORK).json()['id']==first['id']
    assert student.post(base+'/submissions',json={**WORK,'explanation':WORK['explanation']+'修订'}).status_code==409
    assert teacher2.post(base+f"/submissions/{first['id']}/review",json={'criterion_scores':{},'comment':'无课程审核授权的教师不得评分此项目'}).status_code==403
    assert len(teacher.get(base+'/review-queue').json())==1
    limits={item['name']:item['max_score'] for item in RUBRIC['criteria']}
    over={**limits,next(iter(limits)):99}
    assert teacher.post(base+f"/submissions/{first['id']}/review",json={'criterion_scores':over,'comment':'超出量规分值'}).status_code==422
    weak={**limits,'三个边界样例':0}
    reviewed=teacher.post(base+f"/submissions/{first['id']}/review",json={'criterion_scores':weak,'requires_revision':True,'comment':'请补上三个边界样例的实际运行输出。'})
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['status']=='revision_requested' and reviewed.json()['review']['total_score']==80
    assert student.get(base+'/my-submissions').json()[0]['review']['teacher_id']=='teacher'
    second=student.post(base+'/submissions',json={**WORK,'test_code':WORK['test_code']+'\nassert count_files({"kind":"dir","children":[{"kind":"file"}]}) == 1'})
    assert second.status_code==201,second.text
    second=second.json();assert second['version']==2 and second['id']!=first['id']
    final=teacher.post(base+f"/submissions/{second['id']}/review",json={'criterion_scores':limits,'comment':'三个边界样例及递归解释均已核对。'})
    assert final.status_code==200,final.text
    assert final.json()['status']=='graded' and final.json()['review']['total_score']==100
    assert teacher.post(base+f"/submissions/{second['id']}/review",json={'criterion_scores':limits,'comment':'三个边界样例及递归解释均已核对。'}).status_code==200
    assert student.post(base+'/submissions',json=WORK).status_code==409
    assert [item['version'] for item in student.get(base+'/my-submissions').json()]==[2,1]


def test_pending_work_keeps_its_original_rubric_after_revision(clients):
    seed()
    student=clients['student'];teacher=clients['teacher'];admin=clients['admin']
    base=P+f'/learning/project-courses/{COURSE}'
    assert admin.put(P+f'/learning/courses/{COURSE}/reviewers/teacher',json={'assigned':True}).status_code==200
    original=student.post(base+'/submissions',json=WORK).json()
    revised=copy.deepcopy(RUBRIC)
    revised['id']='zz-project-rubric-revision'
    revised['criteria'][0]['max_score']=5
    revised['max_score']=90
    with transaction() as db:
        insert(db,'learning_bank_releases',id='zz-project-rubric-release',content_hash='trial-revision',
               provenance='isolated test',review_kind='user_confirmed',created_at=now())
        insert(db,'learning_project_rubrics',id=revised['id'],release_id='zz-project-rubric-release',
               course_id=COURSE,skill_id=RUBRIC['skill_id'],payload=dump(revised))
    old_limits={item['name']:item['max_score'] for item in RUBRIC['criteria']}
    graded=teacher.post(base+f"/submissions/{original['id']}/review",json={
        'criterion_scores':old_limits,'comment':'按作品提交时的旧版量规逐项核对评分。'})
    assert graded.status_code==200,graded.text
    assert graded.json()['review']['total_score']==100
    assert graded.json()['rubric']['id']==RUBRIC['id']
    fresh=student.post(base+'/submissions',json=WORK)
    assert fresh.status_code==201,fresh.text
    assert fresh.json()['rubric_id']==revised['id'] and fresh.json()['rubric']['max_score']==90
    history=student.get(base+'/my-submissions').json()
    assert {item['id']:item['rubric']['max_score'] for item in history}=={original['id']:100,fresh.json()['id']:90}
