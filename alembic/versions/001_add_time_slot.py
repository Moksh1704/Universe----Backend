"""add time_slot and section to day_attendance

Revision ID: 001_add_time_slot
Revises: 
Create Date: 2026-04-12
"""
from alembic import op
import sqlalchemy as sa

revision = '001_add_time_slot'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add time_slot column (nullable so existing rows don't break)
    op.add_column(
        'day_attendance',
        sa.Column('time_slot', sa.String(50), nullable=True),
    )
    # Add section for grouping queries
    op.add_column(
        'day_attendance',
        sa.Column('section', sa.String(20), nullable=True),
    )
    # Unique constraint: one record per student per time slot per day
    op.create_unique_constraint(
        'uq_day_attendance_student_date_slot',
        'day_attendance',
        ['registration_number', 'date', 'time_slot'],
    )


def downgrade():
    op.drop_constraint('uq_day_attendance_student_date_slot', 'day_attendance', type_='unique')
    op.drop_column('day_attendance', 'section')
    op.drop_column('day_attendance', 'time_slot')
