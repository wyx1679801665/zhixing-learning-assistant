"""Append-only project submissions and teacher rubric reviews."""
from alembic import op

revision='0030'
down_revision='0029'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('''CREATE TABLE project_submissions(
        id TEXT PRIMARY KEY,
        rubric_id TEXT NOT NULL REFERENCES learning_project_rubrics(id),
        course_id TEXT NOT NULL REFERENCES curriculum_catalog_courses(id),
        student_id TEXT NOT NULL REFERENCES users(id),
        version INTEGER NOT NULL,
        code_text TEXT NOT NULL,
        test_code TEXT NOT NULL,
        observed TEXT NOT NULL,
        input_contract TEXT NOT NULL,
        explanation TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('submitted','graded','revision_requested')),
        created_at TEXT NOT NULL,
        UNIQUE(rubric_id,student_id,version)
    )''')
    op.execute('CREATE INDEX ix_project_submissions_course_status ON project_submissions(course_id,status,created_at)')
    op.execute('''CREATE TABLE project_reviews(
        id TEXT PRIMARY KEY,
        submission_id TEXT NOT NULL UNIQUE REFERENCES project_submissions(id),
        teacher_id TEXT NOT NULL REFERENCES users(id),
        criterion_scores TEXT NOT NULL,
        total_score INTEGER NOT NULL,
        requires_revision INTEGER NOT NULL,
        comment TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')

def downgrade():
    raise RuntimeError('Restore a verified backup to preserve project work and reviews.')
