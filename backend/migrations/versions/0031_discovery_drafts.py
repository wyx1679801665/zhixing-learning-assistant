from alembic import op

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE discovery_drafts (
        id TEXT PRIMARY KEY, student_id TEXT NOT NULL REFERENCES users(id),
        request_key TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL,
        status TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
        UNIQUE(student_id,request_key))""")


def downgrade():
    raise RuntimeError('Restore a verified backup to preserve personal drafts.')
