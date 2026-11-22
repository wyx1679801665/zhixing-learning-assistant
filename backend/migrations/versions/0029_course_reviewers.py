from alembic import op
revision='0029'
down_revision='0028'
branch_labels=None
depends_on=None
def upgrade():
    op.execute('''CREATE TABLE course_reviewers(
        course_id TEXT NOT NULL REFERENCES curriculum_catalog_courses(id),
        teacher_id TEXT NOT NULL REFERENCES users(id),
        assigned_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
        PRIMARY KEY(course_id,teacher_id))''')
def downgrade():
    raise RuntimeError('Restore a verified backup')
