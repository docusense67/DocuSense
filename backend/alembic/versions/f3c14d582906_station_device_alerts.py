"""Deduplicated device fault/recovery receipts and per-user notification reads."""
import sqlalchemy as sa
from alembic import op

revision = "f3c14d582906"
down_revision = "d5a8c3e1f2b7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "station_event_receipts",
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("scan_stations.id"), primary_key=True),
        sa.Column("event_id", sa.String(40), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "notification_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("audit_logs.id"), nullable=False),
        sa.UniqueConstraint("user_id", "event_id"),
    )
    op.create_index("ix_notification_reads_user_id", "notification_reads", ["user_id"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        roles = list(bind.execute(sa.text(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
        )).scalars())
        if roles:
            grantees = ", ".join('"' + role + '"' for role in roles)
            for table in ("station_event_receipts", "notification_reads"):
                op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                op.execute(f"REVOKE ALL ON TABLE {table} FROM {grantees}, PUBLIC")


def downgrade():
    op.drop_index("ix_notification_reads_user_id", table_name="notification_reads")
    op.drop_table("notification_reads")
    op.drop_table("station_event_receipts")
