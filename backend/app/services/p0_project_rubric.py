"""Register the user-confirmed project rubric as a draft project course."""
import hashlib
from app.core.db import transaction, one, insert, now, dump, audit

COURSE='p0-python-nested-counter-project'
SKILL='p0-python-nested-counter-implementation'
RELEASE='p0-nested-counter-rubric-20261118-v1'
RUBRIC={
    'id':RELEASE+'-rubric', 'course_id':COURSE, 'skill_id':SKILL,
    'skill_name':'嵌套目录文件计数器项目实现',
    'task':'实现 count_files(tree)，统计内存中嵌套字典的文件节点，不访问真实文件系统。',
    'deliverables':['函数代码及输入约定','嵌套目录、空目录、单文件三个可运行测试','基本情况、递归汇总和调用返回顺序的文字解释'],
    'criteria':[
        {'name':'函数接口与输入约定','max_score':15,'evidence':'接收节点并返回整数；说明支持的节点结构'},
        {'name':'文件与空目录基本情况','max_score':20,'evidence':'文件返回1，空目录返回0，目录本身不计数'},
        {'name':'递归计数','max_score':25,'evidence':'遍历children并递归求和，支持至少两层目录'},
        {'name':'三个边界样例','max_score':20,'evidence':'嵌套样例3、空目录0、单文件1均展示预期和实测'},
        {'name':'解释与可读性','max_score':20,'evidence':'解释基本情况、逐层返回值汇总，命名和缩进清楚'},
    ],
    'max_score':100, 'grading':'teacher_only',
    'input_contract':'kind=file计1；kind=dir时遍历children；不符合约定的输入可明确报错或声明不支持。',
    'source':'用户于2026-11-18在会话中确认项目评分草稿；课程适用年级与正式教师身份未独立核实。',
}

def seed():
    assert sum(c['max_score'] for c in RUBRIC['criteria'])==100
    digest=hashlib.sha256(dump(RUBRIC).encode()).hexdigest()
    with transaction() as db:
        existing=one(db,'SELECT content_hash FROM learning_bank_releases WHERE id=:i',i=RELEASE)
        if existing:
            if existing['content_hash']!=digest:raise ValueError('量规版本内容冲突')
            return {'course_id':COURSE,'rubric_id':RUBRIC['id'],'already_imported':True}
        if one(db,'SELECT id FROM curriculum_catalog_courses WHERE id=:i',i=COURSE):
            raise ValueError('项目课程已存在，但量规版本未登记')
        insert(db,'curriculum_catalog_courses',id=COURSE,name='嵌套目录文件计数器项目',year=None,
               category='工程实践',elective=1,assessment_mode='project_rubric',status='draft',
               source_basis='用户确认的项目目标与100分量规；正式授课教师身份及项目提交流程待核对')
        insert(db,'curriculum_catalog_skills',id=SKILL,course_id=COURSE,
               name=RUBRIC['skill_name'],status='pending_syllabus_review')
        insert(db,'learning_bank_releases',id=RELEASE,content_hash=digest,
               provenance=RUBRIC['source'],review_kind='user_confirmed',created_at=now())
        insert(db,'learning_project_rubrics',id=RUBRIC['id'],release_id=RELEASE,
               course_id=COURSE,skill_id=SKILL,payload=dump(RUBRIC))
        audit(db,None,'p0_project_rubric_draft_registered',COURSE,dump({'release':RELEASE,'hash':digest}))
    return {'course_id':COURSE,'rubric_id':RUBRIC['id'],'status':'draft','score_total':100}
