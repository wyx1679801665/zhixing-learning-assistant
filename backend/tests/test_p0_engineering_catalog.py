from app.core.db import transaction, one, rows
from app.services.p0_engineering_catalog import COURSE_ID, RELATION_ID, SKILLS, seed
from app.services.engineering_goals import scope


def test_minimal_draft_catalog_has_explicit_prerequisites_without_claiming_readiness():
    result = seed()
    assert result['status'] == 'draft'
    assert result['questions_imported'] == 0 and result['materials_linked'] == 0
    assert seed()['already_imported']
    with transaction() as db:
        course = one(db, 'SELECT status FROM curriculum_catalog_courses WHERE id=:i', i=COURSE_ID)
        assert course['status'] == 'draft'
        expanded, relation = scope(db, [SKILLS[-1][0]])
        assert relation == RELATION_ID
        assert [skill['id'] for skill in expanded] == [skill[0] for skill in SKILLS]
        assert [len(skill['prerequisites']) for skill in expanded] == [0, 1, 1]
        assert not rows(db, 'SELECT id FROM learning_question_versions')
        assert not rows(db, 'SELECT id FROM learning_material_links')
