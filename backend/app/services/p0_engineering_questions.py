"""Import user-confirmed engineering questions as drafts for admin publication."""
import json
from pathlib import Path

from app.core.db import transaction, one, rows, insert, uid, now, dump, audit
from app.services.p0_engineering_catalog import COURSE_ID

PACK = Path(__file__).resolve().parents[3] / '课程审核草稿' / '工程目标_课程题目候选.json'


def import_drafts():
    pack = json.loads(PACK.read_text(encoding='utf-8'))
    items = pack['questions']
    if pack['course_id'] != COURSE_ID or len(items) != 9 or len({q['id'] for q in items}) != 9:
        raise ValueError('工程题包范围或数量不符合已确认版本')
    if len({(q['skill_id'], q['phase']) for q in items}) != 9:
        raise ValueError('每个知识点的初测、练习与重测各需一题')
    from app.api.learning_catalog import Question
    with transaction() as db:
        admin = one(db, "SELECT id FROM users WHERE role='admin' AND status='active' ORDER BY created_at LIMIT 1")
        if not admin:
            raise ValueError('需要已有管理员账号')
        prepared = []
        for item in items:
            q = Question(course_id=COURSE_ID, skill_id=item['skill_id'], phase=item['phase'],
                         prompt=item['prompt'], options=item['options'], answer=item['answer'],
                         explanation=item['explanation'],
                         source='项目自编课程审核稿；引用审核班资源版本 ' + item['source_version_id'] +
                                '；用户已确认题目，仍须有权限的管理员或课程审核人正式发布。')
            skill = one(db, 'SELECT id FROM curriculum_catalog_skills WHERE id=:i AND course_id=:c',
                        i=q.skill_id, c=COURSE_ID)
            version = one(db, '''SELECT r.current_version,r.status,v.parse_status FROM resource_versions v
                JOIN resources r ON r.id=v.resource_id WHERE v.id=:i''', i=item['source_version_id'])
            if not skill or not version or version['current_version'] != item['source_version_id'] or version['status'] != 'published' or version['parse_status'] != 'ready':
                raise ValueError('题目知识点或已发布正文版本不匹配：' + item['id'])
            if len(set(q.options)) != len(q.options) or not 0 <= q.answer < len(q.options):
                raise ValueError('题目选项或答案无效：' + item['id'])
            prepared.append((item, q))
        created = 0
        for item, q in prepared:
            qid = 'p0-' + item['id']
            vid = qid + '-v1'
            existing = one(db, 'SELECT * FROM learning_question_versions WHERE id=:i', i=vid)
            if existing:
                if (existing['prompt'], json.loads(existing['options']), existing['answer'],
                        existing['explanation'], existing['skill_id'], existing['phase']) != (
                        q.prompt, q.options, q.answer, q.explanation, q.skill_id, q.phase):
                    raise ValueError('已有题目版本内容不同：' + item['id'])
                continue
            insert(db, 'learning_questions', id=qid, course_id=COURSE_ID,
                   owner_id=admin['id'], created_at=now())
            insert(db, 'learning_question_versions', id=vid, question_id=qid,
                   course_id=COURSE_ID, skill_id=q.skill_id, version=1, phase=q.phase,
                   prompt=q.prompt, options=dump(q.options), answer=q.answer,
                   explanation=q.explanation, source=q.source, status='draft', created_at=now())
            created += 1
        if created:
            audit(db, None, 'p0_engineering_question_drafts_imported', COURSE_ID,
                  dump({'created': created, 'published': 0, 'pack': PACK.name}))
        statuses = rows(db, 'SELECT status FROM learning_question_versions WHERE course_id=:c', c=COURSE_ID)
    return {'course_id': COURSE_ID, 'created': created, 'drafts': sum(q['status'] == 'draft' for q in statuses),
            'published': sum(q['status'] == 'published' for q in statuses)}
