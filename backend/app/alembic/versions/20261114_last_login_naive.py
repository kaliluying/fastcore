"""统一 last_login 时间列时区语义为无时区本地时间，与 created_time/updated_time 一致。"""

import sqlalchemy as sa
from alembic import op

revision = "20261114_last_login_naive"
down_revision = "20261024_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "sys_user" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.alter_column(
        "sys_user",
        "last_login",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
        comment="最后登录时间",
    )


def downgrade() -> None:
    if "sys_user" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.alter_column(
        "sys_user",
        "last_login",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        comment="最后登录时间",
    )
