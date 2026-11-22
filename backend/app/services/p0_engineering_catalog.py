"""Minimal draft catalogue for the reviewed nested file counter goal."""
import hashlib

from app.core.db import transaction, one, rows, insert, now, dump, audit

COURSE_ID = 'p0-python-nested-counter'
SKILLS = (
    ('p0-python-functions', '函数定义、参数与返回值'),
    ('p0-python-recursion', '递归基本情况与规模收敛'),
    ('p0-python-stack', '嵌套调用与返回顺序'),
)
RELATION_ID = 'p0-python-nested-counter-relations-v1'
EDGES = (
    (SKILLS[0][0], SKILLS[1][0], '递归函数需先理解函数参数与返回值'),
    (SKILLS[1][0], SKILLS[2][0], '追踪递归调用栈需先理解基本情况与逐层返回'),
)


def seed():
    payload = {'course_id': COURSE_ID, 'skills': SKILLS, 'edges': EDGES}
    digest = hashlib.sha256(dump(payload).encode()).hexdigest()
    with transaction() as db:
        admin = one(db, "SELECT id FROM users WHERE role='admin' AND status='active' ORDER BY created_at LIMIT 1")
        if not admin:
            raise ValueError('需要已有管理员账号')
        active = one(db, 'SELECT id FROM curriculum_relation_versions WHERE active=1')
        if active and active['id'] != RELATION_ID:
            raise ValueError('已有其他活动课程先修版本；请先人工合并，不能覆盖')
        existing = one(db, 'SELECT * FROM curriculum_catalog_courses WHERE id=:i', i=COURSE_ID)
        old = one(db, 'SELECT * FROM curriculum_relation_versions WHERE id=:i', i=RELATION_ID)
        if existing or old:
            if not existing or not old or old['payload_hash'] != digest or old['active'] != 1:
                raise ValueError('最小课程目录已有不同内容，不能静默覆盖')
            actual = rows(db, 'SELECT id,name,status FROM curriculum_catalog_skills WHERE course_id=:c ORDER BY id', c=COURSE_ID)
            if {(s['id'], s['name']) for s in actual} != set(SKILLS):
                raise ValueError('目录知识点与本版清单不同')
            edges = rows(db, 'SELECT before_id,after_id,reason FROM curriculum_skill_prerequisites WHERE version_id=:v AND kind=\'required\'', v=RELATION_ID)
            if {(e['before_id'], e['after_id'], e['reason']) for e in edges} != set(EDGES):
                raise ValueError('目录先修关系与本版清单不同')
            return {'course_id': COURSE_ID, 'relation_version': RELATION_ID, 'already_imported': True}
        insert(db, 'curriculum_catalog_courses', id=COURSE_ID,
               name='Python 函数与递归：嵌套目录文件计数器基础', year=None,
               category='工程实践', elective=1, assessment_mode='quiz', status='draft',
               source_basis='用户确认的嵌套目录文件计数器最小能力范围；待课程目录与题库审核')
        for skill_id, name in SKILLS:
            insert(db, 'curriculum_catalog_skills', id=skill_id, course_id=COURSE_ID,
                   name=name, status='pending_syllabus_review')
        insert(db, 'curriculum_relation_versions', id=RELATION_ID, payload_hash=digest,
               basis='嵌套目录文件计数器：函数→递归→调用栈的最小先修链；待课程审核',
               official_verified=0, active=1, created_at=now())
        for before, after, reason in EDGES:
            insert(db, 'curriculum_skill_prerequisites', version_id=RELATION_ID,
                   before_id=before, after_id=after, kind='required', reason=reason)
        from app.services.curriculum_search import rebuild
        rebuild(db)
        audit(db, admin['id'], 'p0_engineering_draft_catalog_seeded', COURSE_ID,
              dump({'relation_version': RELATION_ID, 'skills': [s[0] for s in SKILLS]}))
    return {'course_id': COURSE_ID, 'relation_version': RELATION_ID,
            'skill_count': len(SKILLS), 'required_edges': len(EDGES),
            'status': 'draft', 'questions_imported': 0, 'materials_linked': 0}
