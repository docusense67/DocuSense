"""mechanism relay: cloud control of the Pi camera station's motors

Revision ID: b6f2d8e4c1a7
Revises: a9d4e2c7b1f5
Create Date: 2026-10-23 14:00:00.000000

docs/cloud-motor-control.md. With ADGVS_MECHANISM_MODE=relay, operator commands wait in
mechanism_commands until the station's relay long-polls them, and the relay's latest report
of the bridge (plus who controls the station) is one row per station in
mechanism_station_states. No student data in either.

Both tables are locked down like migration a9d4e2c7b1f5: RLS on, Data API roles revoked
(sequences too), so the Supabase test sees no table without RLS.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b6f2d8e4c1a7'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2c7b1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("mechanism_commands", "mechanism_station_states")
NEW_SEQUENCES = ("mechanism_commands_id_seq",)  # station_id is the states' key: no sequence
API_ROLES = ("anon", "authenticated", "service_role")

ACTIONS = ('SCAN', 'STOP', 'ACKNOWLEDGE', 'CONNECT', 'DISCONNECT', 'UP', 'TEST_MOTOR',
           'TEST_SERVO1', 'TEST_SERVO2', 'TEST_SERVO3')


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lock_down(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    present = {name for (name,) in bind.execute(
        sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
        {"names": list(API_ROLES)})}
    if not present & {"anon", "authenticated"}:
        return
    grantees = ", ".join(_quote(r) for r in API_ROLES if r in present)
    schema = _quote(bind.execute(sa.text("SELECT current_schema()")).scalar_one())
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {schema}.{_quote(table)} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {schema}.{_quote(table)} FROM {grantees}, PUBLIC")
    for sequence in NEW_SEQUENCES:
        op.execute(f"REVOKE ALL ON SEQUENCE {schema}.{_quote(sequence)} FROM {grantees}, PUBLIC")


def upgrade() -> None:
    op.create_table(
        'mechanism_commands',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.Enum(*ACTIONS, name='mechanismaction', native_enum=False,
                                    length=20), nullable=False),
        sa.Column('params', _json(), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result', _json(), nullable=True),
        sa.ForeignKeyConstraint(['station_id'], ['scan_stations.id']),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mechanism_commands_station_id', 'mechanism_commands', ['station_id'])
    op.create_index('ix_mechanism_commands_created_at', 'mechanism_commands', ['created_at'])
    op.create_table(
        'mechanism_station_states',
        sa.Column('station_id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('state', _json(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('remote_allowed', sa.Boolean(), nullable=False),
        sa.Column('controller_id', sa.Integer(), nullable=True),
        sa.Column('controller_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('viewer_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['station_id'], ['scan_stations.id']),
        sa.ForeignKeyConstraint(['controller_id'], ['users.id']),
        sa.PrimaryKeyConstraint('station_id'),
    )
    _lock_down(op.get_bind())


def downgrade() -> None:
    op.drop_table('mechanism_station_states')
    op.drop_index('ix_mechanism_commands_created_at', table_name='mechanism_commands')
    op.drop_index('ix_mechanism_commands_station_id', table_name='mechanism_commands')
    op.drop_table('mechanism_commands')
